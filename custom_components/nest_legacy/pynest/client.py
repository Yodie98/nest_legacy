"""PyNest API Client."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin
import uuid

from aiohttp import ClientSession, ClientTimeout, ContentTypeError, FormData
import google.protobuf.any_pb2
from google.protobuf.duration_pb2 import Duration

from .enums import BucketType, Environment, StructureMode
from .exceptions import (
    BadCredentialsException,
    BadGatewayException,
    EmptyResponseException,
    GatewayTimeoutException,
    NotAuthenticatedException,
    PynestException,
)
from .models import (
    Bucket,
    FirstDataAPIResponse,
    GoogleAuthResponse,
    NestAuthResponse,
    NestCamera,
    NestDevice,
    NestEnvironment,
    NestHeatLink,
    NestLock,
    NestSession,
    NestStructure,
    NestThermostat,
)
from .protobuf_gen.nest.trait import occupancy_pb2 as nest_occupancy_pb2
from .protobuf_gen.nestlabs.gateway import v1_pb2, v2_pb2
from .protobuf_gen.weave.trait import (
    description_pb2 as weave_description_pb2,
    heartbeat_pb2 as weave_heartbeat_pb2,
    power_pb2 as weave_power_pb2,
    security_pb2 as weave_security_pb2,
)

# A list of all protobuf traits we want to subscribe to.
_OBSERVE_TRAITS = (
    nest_occupancy_pb2.StructureModeTrait,
    weave_security_pb2.BoltLockTrait,
    weave_power_pb2.BatteryPowerSourceTrait,
    weave_description_pb2.DeviceIdentityTrait,
    weave_description_pb2.LabelSettingsTrait,
    weave_heartbeat_pb2.LivenessTrait,
    weave_security_pb2.TamperTrait,
    weave_security_pb2.BoltLockSettingsTrait,
    weave_security_pb2.BoltLockCapabilitiesTrait,
)
_TRAIT_TYPE_TO_CLASS_MAP = {
    trait.DESCRIPTOR.full_name: trait for trait in _OBSERVE_TRAITS
}

_USER_AGENT = "Nest/5.82.2 (iOScom.nestlabs.jasper.release) os=18.5"

_NEST_ENVIRONMENTS: dict[str, NestEnvironment] = {
    Environment.PRODUCTION: NestEnvironment(
        host="home.nest.com",
        camera_host="camera.home.nest.com",
        camera_cookie_name="website_2=",
        grpc_host="grpc-web.production.nest.com",
    ),
    Environment.FIELDTEST: NestEnvironment(
        host="home.ft.nest.com",
        camera_host="camera.home.ft.nest.com",
        camera_cookie_name="website_ft=",
        grpc_host="grpc-web.ft.nest.com",
    ),
}

# App launch API endpoint
_APP_LAUNCH_URL_FORMAT = "https://{host}/api/0.1/user/{user_id}/app_launch"
_NEST_AUTH_URL_JWT = "https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt"

# Legacy Nest account login
_LEGACY_LOGIN_URL_FORMAT = "https://webapi.{host}/api/v1/login.login_nest"

# Protobuf endpoints
_OBSERVE_ENDPOINT = "/nestlabs.gateway.v2.GatewayService/Observe"
_SEND_COMMAND_ENDPOINT = "/nestlabs.gateway.v1.ResourceApi/SendCommand"
_BATCH_UPDATE_ENDPOINT = "/nestlabs.gateway.v1.TraitBatchApi/BatchUpdateState"

_NESTLABS_TYPE_URL_PREFIX = "type.nestlabs.com/"

_NEST_REQUEST: dict[str, Any] = {
    "known_bucket_types": [
        BucketType.BUCKETS,
        BucketType.DELAYED_TOPAZ,
        BucketType.DEMAND_RESPONSE,
        BucketType.DEVICE,
        BucketType.DEVICE_ALERT_DIALOG,
        BucketType.GEOFENCE_INFO,
        BucketType.KRYPTONITE,
        BucketType.LINK,
        BucketType.MESSAGE,
        BucketType.MESSAGE_CENTER,
        BucketType.METADATA,
        BucketType.OCCUPANCY,
        BucketType.QUARTZ,
        BucketType.SAFETY,
        BucketType.RCS_SETTINGS,
        BucketType.SAFETY_SUMMARY,
        BucketType.SCHEDULE,
        BucketType.SHARED,
        BucketType.STRUCTURE,
        BucketType.STRUCTURE_METADATA,
        BucketType.TOPAZ,
        BucketType.TOPAZ_RESOURCE,
        BucketType.TRACK,
        BucketType.TRIP,
        BucketType.TUNEUPS,
        BucketType.USER,
        BucketType.USER_SETTINGS,
        BucketType.WHERE,
        BucketType.WIDGET_TRACK,
    ],
    "known_bucket_versions": [],
}

_SUBSCRIBE_TIMEOUT = 600  # seconds
_OBSERVE_TIMEOUT = 600  # seconds
_CONNECT_TIMEOUT = 60  # seconds
_PROTOBUF_COMMAND_TIMEOUT = 30  # seconds

_LOGGER = logging.getLogger(__package__)


# The _decode_varint helper function is required to find frame boundaries.
def _decode_varint(buffer: bytes) -> tuple[int | None, int]:
    """Decodes a varint from the start of a buffer and returns the value and bytes read."""
    shift = 0
    result = 0
    bytes_read = 0
    while bytes_read < len(buffer):
        i = buffer[bytes_read]
        bytes_read += 1
        result |= (i & 0x7F) << shift
        shift += 7
        if not (i & 0x80):
            return result, bytes_read
    # If we run out of buffer before finding the end of the varint
    return None, 0


class NestClient:
    """Interface class for the Nest API."""

    def __init__(
        self,
        session: ClientSession | None = None,
        field_test: bool = False,
    ) -> None:
        """Initialize NestClient."""
        self._session = session if session else ClientSession()
        self._environment = _NEST_ENVIRONMENTS[
            Environment.FIELDTEST if field_test else Environment.PRODUCTION
        ]
        self._nest_session: NestSession | None = None
        self._camera_session_token: str | None = None
        self._raw_data: dict[str, Any] = {}
        self._buckets_for_subscription: list[Bucket] = []

    async def async_authenticate_with_google_credentials(
        self, issue_token: str, cookies: str
    ) -> NestSession:
        """Authenticate using Google issue token and cookies."""
        try:
            auth = await self._async_get_access_token_from_cookies(issue_token, cookies)
            return await self._async_authenticate(auth.access_token)
        except Exception:
            _LOGGER.exception("Error during Google credential authentication")
            raise

    async def async_authenticate_with_nest_token(
        self, access_token: str
    ) -> NestSession:
        """Authenticate using a legacy Nest access token."""
        try:
            await self._async_get_camera_session_token(access_token)
            return await self._async_get_session(access_token)
        except Exception:
            _LOGGER.exception("Error during legacy Nest token authentication")
            raise

    async def _async_get_access_token_from_cookies(
        self, issue_token: str, cookies: str
    ) -> GoogleAuthResponse:
        """Get a Google access token."""
        _LOGGER.debug("Requesting Google access token from URL: %s", issue_token)
        async with self._session.get(
            issue_token,
            headers={
                "Sec-Fetch-Mode": "cors",
                "User-Agent": _USER_AGENT,
                "X-Requested-With": "XmlHttpRequest",
                "Referer": "https://accounts.google.com/o/oauth2/iframe",
                "cookie": cookies,
            },
        ) as response:
            result = await response.json()
            if "error" in result:
                raise BadCredentialsException(result.get("detail", result["error"]))
            return GoogleAuthResponse(**result)

    async def _async_authenticate(self, access_token: str) -> NestSession:
        """Start a new Nest session with a Google access token."""
        _LOGGER.debug(
            "Authenticating with Google access token at URL: %s", _NEST_AUTH_URL_JWT
        )
        async with self._session.post(
            _NEST_AUTH_URL_JWT,
            data=FormData(
                {
                    "embed_google_oauth_access_token": True,
                    "expire_after": "3600s",
                    "google_oauth_access_token": access_token,
                    "policy_id": "authproxy-oauth-policy",
                }
            ),
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": _USER_AGENT,
                "Referer": f"https://{self._environment.host}",
            },
        ) as response:
            result = await response.json()
            nest_auth = NestAuthResponse(**result)
            if not nest_auth.jwt:
                raise BadCredentialsException("Could not get JWT from Google token")
            return await self._async_get_session(nest_auth.jwt)

    async def _async_get_camera_session_token(self, access_token: str) -> None:
        """Get a session token for camera APIs (legacy only)."""
        url = _LEGACY_LOGIN_URL_FORMAT.format(host=self._environment.camera_host)
        headers = {
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"https://{self._environment.host}/",
            "Origin": f"https://{self._environment.host}",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        _LOGGER.debug("Requesting legacy camera session token from URL: %s", url)
        try:
            async with self._session.post(
                url,
                headers=headers,
                data=f"access_token={access_token}",
            ) as response:
                if not response.ok:
                    _LOGGER.error(
                        "Failed to get camera session token. Status: %s, Body: %s",
                        response.status,
                        await response.text(),
                    )
                    raise BadCredentialsException("Failed to get camera session token")

                login_data = await response.json()
                if not login_data.get("items"):
                    _LOGGER.error(
                        "Failed to get camera session token, response indicates error: %s",
                        login_data,
                    )
                    raise BadCredentialsException(
                        login_data.get("status_description", "Unknown")
                    )
                self._camera_session_token = login_data["items"][0]["session_token"]
                _LOGGER.debug("Successfully obtained legacy camera session token")
        except (KeyError, IndexError, TypeError, ContentTypeError) as err:
            _LOGGER.exception("Failed to parse camera session token from response")
            raise BadCredentialsException(
                "Could not extract camera session token"
            ) from err

    async def _async_get_session(self, token: str) -> NestSession:
        """Fetch main session data."""
        url = f"https://{self._environment.host}/session"
        _LOGGER.debug("Requesting main session from URL: %s", url)
        async with self._session.get(
            url,
            headers={"Authorization": f"Basic {token}", "User-Agent": _USER_AGENT},
        ) as response:
            if not response.ok:
                _LOGGER.error(
                    "Failed to get session. Status: %s, Body: %s",
                    response.status,
                    await response.text(),
                )
                raise BadCredentialsException(
                    f"Failed to get session: {response.status}"
                )
            nest_session_dict = await response.json()
            self._nest_session = NestSession.from_dict(nest_session_dict)
            _LOGGER.debug(
                "Successfully obtained main session for user %s",
                self._nest_session.email,
            )
            return self._nest_session

    def is_expired(self) -> bool:
        """Check if the current session is expired."""
        if not self._nest_session:
            return True
        return self._nest_session.is_expired()

    async def async_get_first_data(self) -> dict[str, Any]:
        """Get all initial data from the API."""
        if not self._nest_session:
            raise NotAuthenticatedException("No active Nest session.")

        url = _APP_LAUNCH_URL_FORMAT.format(
            host=self._environment.host, user_id=self._nest_session.userid
        )
        _LOGGER.debug("Requesting initial data from URL: %s", url)
        async with self._session.post(
            url,
            json=_NEST_REQUEST,
            headers={
                "Authorization": f"Basic {self._nest_session.access_token}",
                "X-nl-user-id": self._nest_session.userid,
                "X-nl-protocol-version": "1",
            },
        ) as response:
            result = await response.json()
            if "error" in result:
                raise PynestException(f"Error fetching first data: {result['error']}")

            response_data = FirstDataAPIResponse.from_dict(result)
            self._buckets_for_subscription = response_data.updated_buckets
            self._raw_data = {
                bucket.object_key: bucket for bucket in response_data.updated_buckets
            }
            _LOGGER.debug(
                "Successfully fetched initial data with %d buckets",
                len(self._raw_data),
            )
            return {
                bucket.object_key: bucket.value
                for bucket in response_data.updated_buckets
            }

    def get_raw_data_for_diagnostics(self) -> dict[str, Any]:
        """Return raw data, useful for diagnostics."""
        return {key: bucket.value for key, bucket in self._raw_data.items()}

    async def async_subscribe_for_updates(self) -> dict[str, dict[str, Any]]:
        """Subscribe for data updates (long poll)."""
        if not self._nest_session:
            raise NotAuthenticatedException("No active Nest session.")

        objects: list[dict[str, str | int]] = [
            {
                "object_key": b.object_key,
                "object_revision": b.object_revision,
                "object_timestamp": b.object_timestamp,
            }
            for b in self._buckets_for_subscription
        ]
        url = f"{self._nest_session.urls.transport_url}/v6/subscribe"
        _LOGGER.debug("Subscribing for data updates from URL: %s", url)
        async with self._session.post(
            url,
            timeout=ClientTimeout(total=_SUBSCRIBE_TIMEOUT, connect=_CONNECT_TIMEOUT),
            json={"objects": objects, "timeout": _SUBSCRIBE_TIMEOUT},
            headers={
                "Authorization": f"Basic {self._nest_session.access_token}",
                "X-nl-user-id": self._nest_session.userid,
                "X-nl-protocol-version": "1",
            },
        ) as response:
            _LOGGER.debug("Subscriber response status: %s", response.status)
            if response.status == 401:
                raise NotAuthenticatedException
            if response.status == 504:
                raise GatewayTimeoutException
            if response.status == 502:
                raise BadGatewayException
            if response.content_length == 0:
                raise EmptyResponseException
            try:
                result = await response.json()
            except ContentTypeError as err:
                text = await response.text()
                _LOGGER.error("Subscriber content error: %s", text)
                raise PynestException(f"Subscriber error: {text}") from err

            updates: dict[str, dict[str, Any]] = {}
            for bucket_data in result.get("objects", []):
                bucket = Bucket(**bucket_data)
                updates[bucket.object_key] = bucket.value
                for existing_bucket in self._buckets_for_subscription:
                    if existing_bucket.object_key == bucket.object_key:
                        existing_bucket.object_revision = bucket.object_revision
                        existing_bucket.object_timestamp = bucket.object_timestamp
                        break
            return updates

    async def _async_update_objects(
        self, objects_to_update: list[dict[str, Any]]
    ) -> None:
        """Send updates to the Nest API."""
        if not self._nest_session:
            raise NotAuthenticatedException("No active Nest session.")

        url = f"{self._nest_session.urls.transport_url}/v6/put"
        _LOGGER.debug("Updating objects via URL %s: %s", url, objects_to_update)
        async with self._session.post(
            url,
            json={"objects": objects_to_update},
            headers={
                "Authorization": f"Basic {self._nest_session.access_token}",
                "X-nl-user-id": self._nest_session.userid,
                "X-nl-protocol-version": "1",
            },
        ) as response:
            if not response.ok:
                raise PynestException(
                    f"Error updating objects: {await response.text()}"
                )

    async def _async_set_thermostat_property(
        self, object_key: str, data: dict[str, Any]
    ) -> None:
        """Set properties for a thermostat."""
        device_id = object_key.split(".")[1]
        shared_properties = {
            "target_temperature",
            "target_temperature_low",
            "target_temperature_high",
            "target_temperature_type",
        }
        shared_payload = {k: v for k, v in data.items() if k in shared_properties}
        device_payload = {k: v for k, v in data.items() if k not in shared_properties}
        if shared_payload:
            shared_key = f"shared.{device_id}"
            await self._async_set_generic_property(shared_key, shared_payload)
        if device_payload:
            await self._async_set_generic_property(object_key, device_payload)

    async def _async_set_generic_property(
        self, object_key: str, data: dict[str, Any]
    ) -> None:
        """Set a property on a generic device."""
        await self._async_update_objects(
            [{"object_key": object_key, "op": "MERGE", "value": data}]
        )

    async def _async_set_heatlink_property(
        self, device: NestHeatLink, data: dict[str, Any]
    ) -> None:
        """Set properties for a Heat Link."""
        if not device.associated_thermostat_object_key:
            return

        payload = data.copy()
        if "hot_water_boost" in payload:
            duration_seconds = 1800  # Default to 30 minutes
            if payload.pop("hot_water_boost"):
                end_timestamp = int(time.time()) + duration_seconds
            else:
                end_timestamp = 0
            payload["hot_water_boost_time_to_end"] = end_timestamp

        await self._async_set_generic_property(
            device.associated_thermostat_object_key, payload
        )

    async def _async_set_structure_property(
        self, device: NestStructure, data: dict[str, Any]
    ) -> None:
        """Set properties for a Nest Structure (Home/Away)."""
        if "mode" in data:
            mode_map = {
                StructureMode.HOME: nest_occupancy_pb2.StructureModeTrait.StructureMode.STRUCTURE_MODE_HOME,
                StructureMode.AWAY: nest_occupancy_pb2.StructureModeTrait.StructureMode.STRUCTURE_MODE_AWAY,
                StructureMode.VACATION: nest_occupancy_pb2.StructureModeTrait.StructureMode.STRUCTURE_MODE_VACATION,
            }
            target_mode = mode_map.get(data["mode"])
            if target_mode is None:
                raise PynestException(f"Invalid structure mode: {data['mode']}")

            command = v1_pb2.ResourceCommand(traitLabel="structure_mode")
            command.command.Pack(
                nest_occupancy_pb2.StructureModeTrait.StructureModeChangeRequest(
                    structureMode=target_mode,
                    reason=nest_occupancy_pb2.StructureModeTrait.StructureModeReason.STRUCTURE_MODE_REASON_EXPLICIT_INTENT,
                ),
                type_url_prefix=_NESTLABS_TYPE_URL_PREFIX,
            )
            await self._async_send_command(device, command)

    def _get_protobuf_headers(self) -> dict[str, str]:
        """Get headers for protobuf API requests."""
        if not self._nest_session:
            raise NotAuthenticatedException(
                "No active Nest session for protobuf command."
            )
        return {
            "Authorization": f"Basic {self._nest_session.access_token}",
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-protobuf",
            "X-Accept-Response-Streaming": "true",
        }

    def _get_camera_headers(self) -> dict[str, str]:
        """Get headers for camera API requests."""
        headers = {
            "User-Agent": _USER_AGENT,
            "Referer": f"https://{self._environment.host}/",
            "Origin": f"https://{self._environment.host}",
        }
        if self._camera_session_token:  # Legacy account
            cookie = (
                f"{self._environment.camera_cookie_name}{self._camera_session_token}"
            )
            headers["Cookie"] = cookie
        elif self._nest_session:  # Google account
            headers["Authorization"] = f"Basic {self._nest_session.access_token}"
        else:
            raise NotAuthenticatedException("No session for camera command.")
        return headers

    async def _async_set_camera_property(
        self, device: NestCamera, key: str, value: bool
    ) -> None:
        """Set properties for a camera via the dropcams API."""
        key_map = {
            "streaming_enabled": "streaming.enabled",
            "audio_enabled": "audio.enabled",
            "indoor_chime_enabled": "doorbell.indoor_chime.enabled",
            "doorbell_chime_assist_enabled": "doorbell.chime_assist.enabled",
            "irled_enabled": "irled.state",
            "status_led_enabled": "statusled.brightness",
            "video_flipped": "video.flipped",
        }
        api_key = key_map.get(key)
        if not api_key:
            raise PynestException(f"Unsupported camera property: {key}")

        # Handle special value mapping for some properties
        api_value = str(value).lower()
        if key == "irled_enabled":
            api_value = "auto_on" if value else "always_off"
        elif key == "status_led_enabled":
            # 0=auto, 1=off, 2=on. We map enabled to "on".
            api_value = "2" if value else "1"

        camera_uuid = device.object_key.split(".")[1]
        payload = f"uuid={camera_uuid}&{api_key}={api_value}"
        url = f"https://webapi.{self._environment.camera_host}/api/dropcams.set_properties"
        _LOGGER.debug(
            "Setting camera property via URL %s with payload: %s", url, payload
        )

        headers = self._get_camera_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with self._session.post(url, headers=headers, data=payload) as response:
            if not response.ok:
                raise PynestException(
                    f"Error setting camera property: {await response.text()}"
                )
            result = await response.json()
            if result.get("status") != 0:
                raise PynestException(f"API error setting camera property: {result}")

    async def _async_send_command(
        self, device: NestDevice, command: v1_pb2.ResourceCommand
    ) -> None:
        """Send a command via the protobuf API."""
        send_command_req = v1_pb2.SendCommandRequest(
            resourceRequest=v1_pb2.ResourceRequest(
                resourceId=device.object_key, requestId=str(uuid.uuid4())
            ),
            resourceCommands=[command],
        )

        url = f"https://{self._environment.grpc_host}{_SEND_COMMAND_ENDPOINT}"

        async with self._session.post(
            url,
            data=send_command_req.SerializeToString(),
            headers=self._get_protobuf_headers(),
            timeout=ClientTimeout(total=_PROTOBUF_COMMAND_TIMEOUT),
        ) as response:
            if not response.ok:
                raise PynestException(f"Error sending command: {await response.text()}")
            if _LOGGER.isEnabledFor(logging.DEBUG):
                response_bytes = await response.read()
                send_command_resp = v1_pb2.SendCommandResponse()
                send_command_resp.ParseFromString(response_bytes)
                _LOGGER.debug("SendCommand response: %s", send_command_resp)

    async def _async_update_trait_state(
        self, trait_update_request: v1_pb2.TraitUpdateStateRequest
    ) -> None:
        """Update a device's trait state via the protobuf API."""
        batch_update_req = v1_pb2.BatchUpdateStateRequest(
            batchUpdateStateRequest=[trait_update_request]
        )
        url = f"https://{self._environment.grpc_host}{_BATCH_UPDATE_ENDPOINT}"

        async with self._session.post(
            url,
            data=batch_update_req.SerializeToString(),
            headers=self._get_protobuf_headers(),
            timeout=ClientTimeout(total=_PROTOBUF_COMMAND_TIMEOUT),
        ) as response:
            if not response.ok:
                raise PynestException(
                    f"Error updating trait state: {await response.text()}"
                )
            if _LOGGER.isEnabledFor(logging.DEBUG):
                response_bytes = await response.read()
                batch_update_resp = v1_pb2.BatchUpdateStateResponse()
                batch_update_resp.ParseFromString(response_bytes)
                _LOGGER.debug("BatchUpdate response: %s", batch_update_resp)

    async def _async_set_lock_property(
        self, device: NestLock, data: dict[str, Any]
    ) -> None:
        """Set properties for a Nest x Yale Lock."""
        if "bolt_locked" in data:
            state = (
                weave_security_pb2.BoltLockTrait.BoltState.BOLT_STATE_EXTENDED
                if data["bolt_locked"]
                else weave_security_pb2.BoltLockTrait.BoltState.BOLT_STATE_RETRACTED
            )

            command = v1_pb2.ResourceCommand(traitLabel="bolt_lock")
            command.command.Pack(
                weave_security_pb2.BoltLockTrait.BoltLockChangeRequest(
                    state=state,
                    boltLockActor=weave_security_pb2.BoltLockTrait.BoltLockActorStruct(
                        method=weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_REMOTE_USER_EXPLICIT
                    ),
                ),
                type_url_prefix=_NESTLABS_TYPE_URL_PREFIX,
            )
            await self._async_send_command(device, command)
        if "auto_relock_duration" in data or "auto_relock_on" in data:
            state_proto = weave_security_pb2.BoltLockSettingsTrait(
                autoRelockOn=data.get("auto_relock_on", device.auto_relock_on),
                autoRelockDuration=Duration(
                    seconds=data.get(
                        "auto_relock_duration", device.auto_relock_duration
                    )
                ),
            )
            any_proto = google.protobuf.any_pb2.Any()
            any_proto.Pack(state_proto, type_url_prefix=_NESTLABS_TYPE_URL_PREFIX)

            request = v1_pb2.TraitUpdateStateRequest(
                traitRequest=v1_pb2.TraitRequest(
                    resourceId=device.object_key,
                    traitLabel="bolt_lock_settings",
                    requestId=str(uuid.uuid4()),
                ),
                state=any_proto,
            )
            await self._async_update_trait_state(request)

    async def async_set_device_data(
        self, device: NestDevice, data: dict[str, Any]
    ) -> None:
        """Set device data, dispatching to the correct internal method."""
        if isinstance(device, NestCamera):
            for key, value in data.items():
                await self._async_set_camera_property(device, key, value)
        elif isinstance(device, NestHeatLink):
            await self._async_set_heatlink_property(device, data)
        elif isinstance(device, NestThermostat):
            await self._async_set_thermostat_property(device.object_key, data)
        elif isinstance(device, NestStructure):
            await self._async_set_structure_property(device, data)
        elif isinstance(device, NestLock):
            await self._async_set_lock_property(device, data)
        else:
            await self._async_set_generic_property(device.object_key, data)

    async def async_get_camera_snapshot(self, device: NestCamera) -> bytes | None:
        """Get a snapshot from a camera."""
        if not device.nexus_api_http_server_url:
            _LOGGER.error(
                "Cannot get snapshot for %s, nexus_api_http_server_url is missing",
                device.object_key,
            )
            return None

        camera_uuid = device.object_key.split(".")[1]
        url = urljoin(
            device.nexus_api_http_server_url,
            f"/get_image?uuid={camera_uuid}",
        )
        _LOGGER.debug("Requesting camera snapshot from URL: %s", url)

        async with self._session.get(
            url, headers=self._get_camera_headers()
        ) as response:
            if response.ok:
                return await response.read()
            _LOGGER.error(
                "Failed to get camera snapshot. Status: %s, URL: %s",
                response.status,
                response.url,
            )
            return None

    async def async_get_camera_event_clip(
        self,
        device: NestCamera,
        event_id: str,
        height: int | None = None,
        format: str = "mp4",
    ) -> bytes | None:
        """Get a historical clip from a camera event."""
        if not device.nexus_api_http_server_url:
            _LOGGER.error(
                "Cannot get event clip for %s, nexus_api_http_server_url is missing",
                device.object_key,
            )
            return None

        camera_uuid = device.object_key.split(".")[1]
        params: dict[str, str | int] = {
            "uuid": camera_uuid,
            "cuepoint_id": event_id,
            "format": format,
        }
        if height:
            params["height"] = height

        url = urljoin(device.nexus_api_http_server_url, "/get_event_clip")
        _LOGGER.debug("Requesting event clip from URL: %s with params %s", url, params)

        async with self._session.get(
            url, params=params, headers=self._get_camera_headers()
        ) as response:
            if response.ok:
                return await response.read()
            _LOGGER.error(
                "Failed to get camera event clip. Status: %s, URL: %s",
                response.status,
                response.url,
            )
            return None

    async def async_get_camera_event_thumbnail(
        self, device: NestCamera, event_id: str
    ) -> bytes | None:
        """Get a historical thumbnail from a camera event."""
        return await self.async_get_camera_event_clip(
            device, event_id, height=92, format="jpeg"
        )

    async def async_get_camera_events(
        self,
        device: NestCamera,
        start_time: int | None = None,
        end_time: int | None = None,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get camera events from the cuepoint API."""
        if not device.nexus_api_http_server_url:
            return []

        if start_time is None:
            start_time = int(time.time() - 60)

        camera_uuid = device.object_key.split(".")[1]
        url = f"{device.nexus_api_http_server_url}/cuepoint/{camera_uuid}/2"
        params: dict[str, Any] = {"start_time": start_time}
        if end_time:
            params["end_time"] = end_time
        if types:
            params["types"] = ",".join(types)

        if end_time:
            _LOGGER.debug(
                "Requesting camera events from URL: %s with params %s", url, params
            )
        async with self._session.get(
            url,
            params=params,
            headers=self._get_camera_headers(),
        ) as response:
            if not response.ok:
                _LOGGER.warning(
                    "Failed to fetch camera events: %s, URL: %s",
                    response.status,
                    response.url,
                )
                return []
            return await response.json()

    async def async_get_camera_properties(self, device: NestCamera) -> dict[str, Any]:
        """Get camera properties from the API."""
        camera_uuid = device.object_key.split(".")[1]
        url = f"https://webapi.{self._environment.camera_host}/api/cameras.get_with_properties"
        params = {"uuid": camera_uuid}
        _LOGGER.debug(
            "Requesting camera properties from URL: %s with params %s", url, params
        )
        async with self._session.get(
            url, params=params, headers=self._get_camera_headers()
        ) as response:
            if not response.ok:
                _LOGGER.warning(
                    "Failed to fetch camera properties: %s, URL: %s",
                    response.status,
                    response.url,
                )
                return {}
            data = await response.json()
            try:
                return data["items"][0].get("properties", {})
            except (KeyError, IndexError):
                return {}

    async def async_observe_for_updates(self):
        """Listen for protobuf data updates."""
        url = f"https://{self._environment.grpc_host}{_OBSERVE_ENDPOINT}"
        headers = self._get_protobuf_headers()

        observe_req = v2_pb2.ObserveRequest(
            stateTypes=[
                v2_pb2.ACCEPTED,
                v2_pb2.CONFIRMED,
            ],
            traitTypeParams=[
                v2_pb2.TraitTypeObserveParams(traitType=trait_type)
                for trait_type in _TRAIT_TYPE_TO_CLASS_MAP
            ],
        )
        request_payload_bytes = observe_req.SerializeToString()

        try:
            async with self._session.post(
                url,
                headers=headers,
                data=request_payload_bytes,
                timeout=ClientTimeout(total=_OBSERVE_TIMEOUT, connect=_CONNECT_TIMEOUT),
            ) as response:
                response.raise_for_status()
                buffer = bytearray()

                while True:
                    chunk = await response.content.read(4096)
                    if not chunk:
                        _LOGGER.debug("Observe stream finished")
                        break
                    buffer.extend(chunk)

                    while True:
                        if len(buffer) < 2:
                            break
                        message_length, varint_size = _decode_varint(buffer[1:])
                        if message_length is None:
                            break

                        frame_size = 1 + varint_size + message_length
                        if len(buffer) < frame_size:
                            break

                        # Extract the entire frame and pass it to the parser.
                        full_frame_data = buffer[:frame_size]

                        # Remove the frame from the buffer for the next iteration.
                        buffer = buffer[frame_size:]

                        outer_response = v2_pb2.ObserveResponse()
                        outer_response.ParseFromString(full_frame_data)

                        # Iterate through the list of actual response messages within the frame.
                        for inner_response in outer_response.observeResponse:
                            updates: dict[str, dict[str, Any]] = {}
                            for state in inner_response.traitStates:
                                type_url = state.patch.values.type_url
                                target_class = _TRAIT_TYPE_TO_CLASS_MAP.get(
                                    type_url.removeprefix(_NESTLABS_TYPE_URL_PREFIX)
                                )
                                if not target_class:
                                    _LOGGER.debug(
                                        "Unknown type_url received, skipping: %s",
                                        type_url,
                                    )
                                    continue

                                unpacked_message = target_class()
                                state.patch.values.Unpack(unpacked_message)

                                resource_id = state.traitId.resourceId
                                trait_label = state.traitId.traitLabel

                                if resource_id not in updates:
                                    updates[resource_id] = {}
                                updates[resource_id][trait_label] = unpacked_message

                            if updates:
                                yield updates

        except TimeoutError:
            _LOGGER.debug(
                "Stream connection timed out due to inactivity. The stream will need to be restarted"
            )
        except Exception as err:
            _LOGGER.exception("Observe stream failed")
            raise PynestException(f"Observe stream failed: {err}") from err
