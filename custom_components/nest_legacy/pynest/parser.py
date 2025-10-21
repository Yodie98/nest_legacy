"""Data parsing for the Nest API."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from typing import Any

from .enums import (
    HotWaterMode,
    LockBoltActor,
    LockBoltState,
    StructureMode,
    TemperatureScale,
    ThermostatHvacMode,
    ThermostatHvacState,
)
from .models import (
    NestBatteryProtect,
    NestCamera,
    NestDevice,
    NestDoorbell,
    NestHeatLink,
    NestLock,
    NestProtect,
    NestStructure,
    NestTempSensor,
    NestThermostat,
    NestWiredProtect,
)
from .protobuf_gen.weave.trait import (
    heartbeat_pb2 as weave_heartbeat_pb2,
    security_pb2 as weave_security_pb2,
)


def _scale_value(
    value: float,
    source_min: float,
    source_max: float,
    target_min: float,
    target_max: float,
) -> float:
    """Scale a value from a source range to a target range."""
    if source_max == source_min:
        return float(target_min)
    value = max(source_min, min(source_max, value))
    return ((value - source_min) * (target_max - target_min)) / (
        source_max - source_min
    ) + target_min


def _milli_volt_to_percentage(state: int) -> float:
    """Convert battery level in mV to a percentage.

    The battery life percentage in devices is estimated using slopes from the L91 battery's datasheet.
    This is a rough estimation, and the battery life percentage is not linear.

    Tests on various devices have shown accurate results.
    """
    if 3000 < state <= 6000:
        if 4950 < state <= 6000:
            slope = 0.001816609
            yint = -8.548096886
        elif 4800 < state <= 4950:
            slope = 0.000291667
            yint = -0.991176471
        elif 4500 < state <= 4800:
            slope = 0.001077342
            yint = -4.730392157
        else:
            slope = 0.000434641
            yint = -1.825490196

        return max(0, min(100, round(((slope * state) + yint) * 100)))

    return 0.0


def _get_model_from_serial(serial_number: str | None) -> str:
    """Determine thermostat model from serial number as a fallback."""
    if not serial_number:
        return "Thermostat"
    prefix = serial_number[:2]
    if prefix == "15":
        return "Thermostat E"
    if prefix in ("09", "10"):
        return "Learning Thermostat (3rd gen)"
    if prefix == "02":
        return "Learning Thermostat (2nd gen)"
    if prefix == "01":
        return "Learning Thermostat (1st gen)"
    return "Thermostat"


@dataclass
class ParsedData:
    """Container for all parsed data."""

    devices: list[NestDevice]


class NestParser:
    """Parses raw Nest API data into structured objects."""

    def parse_all(self, raw_data: dict[str, Any]) -> ParsedData:
        """Process all raw data into a device list."""
        devices: list[NestDevice] = []
        device: NestDevice | None
        thermostats: list[NestThermostat] = []

        wheres_map = self._build_wheres_map(raw_data)

        for key, value in raw_data.items():
            if key.startswith("topaz."):
                if device := self._parse_protect(key, value, raw_data, wheres_map):
                    devices.append(device)
            elif key.startswith("device."):
                if device := self._parse_thermostat(key, value, raw_data, wheres_map):
                    devices.append(device)
                    thermostats.append(device)
            elif key.startswith("kryptonite."):
                if device := self._parse_tempsensor(key, value, wheres_map):
                    devices.append(device)
            elif key.startswith("quartz."):
                if device := self._parse_camera(key, value, wheres_map):
                    devices.append(device)
            elif key.startswith("structure."):
                if device := self._parse_structure(key, value, raw_data):
                    devices.append(device)
            elif key.startswith("DEVICE_"):
                # Handle protobuf devices
                if "bolt_lock" in value:
                    if device := self._parse_protobuf_lock(key, value):
                        devices.append(device)

        # Second pass to create derived devices like Heat Link
        devices.extend(
            heatlink
            for thermostat in thermostats
            if (heatlink := self._create_heatlink(thermostat))
        )

        return ParsedData(devices=devices)

    def _build_wheres_map(self, raw_data: dict[str, Any]) -> dict[str, str]:
        """Build a map of where_id to location name."""
        wheres_map: dict[str, str] = {}
        for key, value in raw_data.items():
            if key.startswith("where."):
                for where in value.get("wheres", []):
                    if "where_id" in where and "name" in where:
                        wheres_map[where["where_id"]] = where["name"]
        return wheres_map

    def _get_location(
        self, data: dict[str, Any], wheres_map: dict[str, str]
    ) -> str | None:
        """Get the location name for a device."""
        where_id = data.get("where_id")
        if where_id is None:
            return None
        return wheres_map.get(where_id)

    def _parse_protect(
        self,
        key: str,
        value: dict[str, Any],
        raw_data: dict[str, Any],
        wheres_map: dict[str, str],
    ) -> NestProtect | None:
        """Parse a Nest Protect device."""
        device_id = key.split(".")[1]
        widget_key = f"widget_track.{device_id}"
        online = raw_data.get(widget_key, {}).get("online", False)
        if value.get("wired_or_battery") == 0:
            return NestWiredProtect(
                object_key=key,
                serial_number=value["serial_number"],
                location=self._get_location(value, wheres_map),
                name=value.get("description", "Protect"),
                model=value.get("model"),
                software_version=value.get("software_version"),
                mac_address=value.get("wifi_mac_address"),
                online=online,
                smoke_status=value.get("smoke_status", 0) != 0,
                co_status=value.get("co_status", 0) != 0,
                heat_status=value.get("heat_status", 0) != 0,
                battery_level=_milli_volt_to_percentage(value.get("battery_level", 0)),
                battery_health_state=value.get("battery_health_state", 0),
                replace_by_date=datetime.date.fromtimestamp(
                    value["replace_by_date_utc_secs"]
                ),
                occupancy=not value.get("auto_away", True),
                line_power_present=value.get("line_power_present", False),
                night_light_enable=value.get("night_light_enable", False),
                steam_detection_enable=value.get("steam_detection_enable", False),
                night_light_brightness=value.get("night_light_brightness"),
                component_speaker_test_passed=value.get(
                    "component_speaker_test_passed", False
                ),
                component_smoke_test_passed=value.get(
                    "component_smoke_test_passed", False
                ),
                component_co_test_passed=value.get("component_co_test_passed", False),
                component_wifi_test_passed=value.get(
                    "component_wifi_test_passed", False
                ),
                component_led_test_passed=value.get("component_led_test_passed", False),
                component_pir_test_passed=value.get("component_pir_test_passed", False),
                component_buzzer_test_passed=value.get(
                    "component_buzzer_test_passed", False
                ),
                component_hum_test_passed=value.get("component_hum_test_passed", False),
                removed_from_base=value.get("removed_from_base", False),
                latest_manual_test_end_utc_secs=value.get(
                    "latest_manual_test_end_utc_secs", 0
                ),
                last_audio_self_test_end_utc_secs=value.get(
                    "last_audio_self_test_end_utc_secs", 0
                ),
                ntp_green_led_enable=value.get("ntp_green_led_enable", False),
                heads_up_enable=value.get("heads_up_enable", False),
            )
        return NestBatteryProtect(
            object_key=key,
            serial_number=value["serial_number"],
            location=self._get_location(value, wheres_map),
            name=value.get("description", "Protect"),
            model=value.get("model"),
            software_version=value.get("software_version"),
            mac_address=value.get("wifi_mac_address"),
            online=online,
            smoke_status=value.get("smoke_status", 0) != 0,
            co_status=value.get("co_status", 0) != 0,
            heat_status=value.get("heat_status", 0) != 0,
            battery_level=_milli_volt_to_percentage(value.get("battery_level", 0)),
            battery_health_state=value.get("battery_health_state", 0),
            replace_by_date=datetime.date.fromtimestamp(
                value["replace_by_date_utc_secs"]
            ),
            night_light_enable=value.get("night_light_enable", False),
            steam_detection_enable=value.get("steam_detection_enable", False),
            night_light_brightness=value.get("night_light_brightness"),
            component_speaker_test_passed=value.get(
                "component_speaker_test_passed", False
            ),
            component_smoke_test_passed=value.get("component_smoke_test_passed", False),
            component_co_test_passed=value.get("component_co_test_passed", False),
            component_wifi_test_passed=value.get("component_wifi_test_passed", False),
            component_led_test_passed=value.get("component_led_test_passed", False),
            component_pir_test_passed=value.get("component_pir_test_passed", False),
            component_buzzer_test_passed=value.get(
                "component_buzzer_test_passed", False
            ),
            component_hum_test_passed=value.get("component_hum_test_passed", False),
            removed_from_base=value.get("removed_from_base", False),
            latest_manual_test_end_utc_secs=value.get(
                "latest_manual_test_end_utc_secs", 0
            ),
            last_audio_self_test_end_utc_secs=value.get(
                "last_audio_self_test_end_utc_secs", 0
            ),
            ntp_green_led_enable=value.get("ntp_green_led_enable", False),
            heads_up_enable=value.get("heads_up_enable", False),
        )

    def _parse_thermostat(
        self,
        key: str,
        value: dict[str, Any],
        raw_data: dict[str, Any],
        wheres_map: dict[str, str],
    ) -> NestThermostat | None:
        """Parse a Nest Thermostat device."""
        device_id = key.split(".")[1]
        shared_key = f"shared.{device_id}"
        if shared_key not in raw_data:
            return None
        shared_data = raw_data[shared_key]

        data: dict[str, Any] = {**value, **shared_data}
        track_key = f"track.{device_id}"
        online = raw_data.get(track_key, {}).get("online", False)

        # Occupancy
        link_key = f"link.{device_id}"
        structure_key = raw_data.get(link_key, {}).get("structure")
        occupancy = (
            not raw_data.get(structure_key, {}).get("away", True)
            if structure_key
            else False
        )

        hvac_state = ThermostatHvacState.OFF
        if data.get("hvac_heater_state") or data.get("hvac_aux_heater_state"):
            hvac_state = ThermostatHvacState.HEATING
        elif data.get("hvac_ac_state"):
            hvac_state = ThermostatHvacState.COOLING

        hvac_mode = ThermostatHvacMode(data.get("target_temperature_type", "off"))
        is_eco = data.get("eco", {}).get("mode") in ("auto-eco", "manual-eco")
        if is_eco:
            target_low = data.get("away_temperature_low")
            target_high = data.get("away_temperature_high")
        else:
            target_low = data.get("target_temperature_low")
            target_high = data.get("target_temperature_high")

        current_temperature = data.get("current_temperature")

        # Check for active remote temperature sensor
        rcs_settings_key = f"rcs_settings.{device_id}"
        if rcs_settings_key in raw_data:
            rcs_data = raw_data[rcs_settings_key]
            active_sensors = rcs_data.get("active_rcs_sensors", [])
            if active_sensors:
                sensor_key = active_sensors[0]
                if sensor_key in raw_data:
                    sensor_data = raw_data[sensor_key]
                    current_temperature = sensor_data.get("current_temperature")

        return NestThermostat(
            object_key=key,
            serial_number=value["serial_number"],
            location=self._get_location(value, wheres_map),
            name=value.get("description", "Thermostat"),
            model=value.get("model") or _get_model_from_serial(value["serial_number"]),
            software_version=value.get("software_version"),
            mac_address=value.get("mac_address"),
            online=online,
            temperature_scale=TemperatureScale(data["temperature_scale"])
            if data.get("temperature_scale")
            else None,
            current_temperature=current_temperature,
            backplate_temperature=value.get("backplate_temperature"),
            target_temperature=data.get("target_temperature"),
            target_temperature_low=target_low,
            target_temperature_high=target_high,
            current_humidity=data.get("current_humidity"),
            hvac_state=hvac_state,
            hvac_mode=hvac_mode,
            is_eco_mode=is_eco,
            leaf=data.get("leaf", False),
            can_heat=data.get("can_heat", False),
            can_cool=data.get("can_cool", False),
            has_fan=data.get("has_fan", False),
            fan_state=data.get("fan_timer_timeout", 0) > 0,
            fan_timer_speed=int(
                data.get("fan_timer_speed", "stage0").replace("stage", "") or "1"
            ),
            fan_max_speed=int(
                data.get("fan_capabilities", "stage1").replace("stage", "") or "1"
            ),
            fan_duration=data.get("fan_duration", 900),
            fan_timer_timeout=data.get("fan_timer_timeout", 0),
            occupancy=occupancy,
            battery_level=_scale_value(data.get("battery_level", 0), 3.6, 3.9, 0, 100),
            has_hot_water_control=data.get("has_hot_water_control", False),
            heat_link_model=data.get("heat_link_model"),
            heat_link_serial_number=data.get("heat_link_serial_number"),
            heat_link_sw_version=data.get("heat_link_sw_version"),
            hot_water_active=data.get("hot_water_active", False),
            hot_water_mode=HotWaterMode(data.get("hot_water_mode", "off")),
            hot_water_away_enabled=data.get("hot_water_away_enabled", False),
            hot_water_boost_time_to_end=data.get("hot_water_boost_time_to_end", 0),
            hot_water_temperature=data.get("hot_water_temperature"),
            current_water_temperature=data.get("current_water_temperature"),
        )

    def _parse_tempsensor(
        self, key: str, value: dict[str, Any], wheres_map: dict[str, str]
    ) -> NestTempSensor | None:
        """Parse a Nest Temperature Sensor."""
        return NestTempSensor(
            object_key=key,
            serial_number=value["serial_number"],
            location=self._get_location(value, wheres_map),
            name="Temperature Sensor",
            model=value.get("model"),
            software_version=value.get("software_version"),
            online=(value.get("last_updated_at", 0) - value.get("creation_time", 0))
            < 3600 * 4,
            current_temperature=value.get("current_temperature"),
            battery_level=value.get("battery_level", 0.0),
        )

    def _parse_camera(
        self, key: str, value: dict[str, Any], wheres_map: dict[str, str]
    ) -> NestCamera | None:
        """Parse a Nest Camera or Doorbell."""
        streaming_state = value.get("streaming_state", "")
        model = value.get("model", "")
        props = value.get("properties", {})
        if "doorbell" in model.lower():
            return NestDoorbell(
                object_key=key,
                serial_number=value["serial_number"],
                location=self._get_location(value, wheres_map),
                name=value.get("description", "Camera"),
                model=model,
                software_version=value.get("software_version"),
                mac_address=value.get("mac_address"),
                online="offline" not in streaming_state,
                streaming_enabled="enabled" in streaming_state,
                audio_enabled=value.get("audio_input_enabled", False),
                is_streaming="streaming" in streaming_state,
                indoor_chime_enabled=props.get("doorbell.indoor_chime.enabled", False),
                doorbell_chime_assist_enabled=props.get(
                    "doorbell.chime_assist.enabled", False
                ),
                irled_enabled=props.get("irled.state") != "always_off",
                status_led_enabled=props.get("statusled.brightness", 1) != 1,
                video_flipped=props.get("video.flipped", False),
                web_url=value.get("web_url"),
                nexus_api_http_server_url=value.get("nexus_api_http_server_url"),
                structure_id=value.get("structure_id"),
            )
        return NestCamera(
            object_key=key,
            serial_number=value["serial_number"],
            location=self._get_location(value, wheres_map),
            name=value.get("description", "Camera"),
            model=model,
            software_version=value.get("software_version"),
            mac_address=value.get("mac_address"),
            online="offline" not in streaming_state,
            streaming_enabled="enabled" in streaming_state,
            audio_enabled=value.get("audio_input_enabled", False),
            is_streaming="streaming" in streaming_state,
            irled_enabled=props.get("irled.state") != "always_off",
            status_led_enabled=props.get("statusled.brightness", 1) != 1,
            video_flipped=props.get("video.flipped", False),
            web_url=value.get("web_url"),
            nexus_api_http_server_url=value.get("nexus_api_http_server_url"),
            structure_id=value.get("structure_id"),
        )

    def _parse_structure(
        self, key: str, value: dict[str, Any], raw_data: dict[str, Any]
    ) -> NestStructure | None:
        """Parse a Nest Structure."""
        structure_key = next(
            (key for key in raw_data if key.startswith("STRUCTURE_")), None
        )
        if not structure_key:
            return None  # Cannot control structure without its protobuf key
        mode = StructureMode.HOME
        if value.get("vacation_mode"):
            mode = StructureMode.VACATION
        elif value.get("away"):
            mode = StructureMode.AWAY
        return NestStructure(
            object_key=structure_key,
            serial_number=key.split(".")[1],
            name=value.get("name", "Home"),
            mode=mode,
        )

    def _parse_protobuf_lock(
        self,
        key: str,
        traits: dict[str, Any],
    ) -> NestLock | None:
        """Parse a Nest x Yale Lock from protobuf data."""
        bolt_lock_trait = traits.get("bolt_lock")
        if not bolt_lock_trait:
            return None

        # Determine bolt state
        bolt_state = LockBoltState.UNKNOWN
        if (
            bolt_lock_trait.actuatorState
            == weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_LOCKING
        ):
            bolt_state = LockBoltState.LOCKING
        elif (
            bolt_lock_trait.actuatorState
            == weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_UNLOCKING
        ):
            bolt_state = LockBoltState.UNLOCKING
        elif bolt_lock_trait.actuatorState in (
            weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_JAMMED_UNLOCKING,
            weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_JAMMED_LOCKING,
            weave_security_pb2.BoltLockTrait.BoltActuatorState.BOLT_ACTUATOR_STATE_JAMMED_OTHER,
        ):
            bolt_state = LockBoltState.JAMMED
        elif (
            bolt_lock_trait.lockedState
            == weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_LOCKED
        ):
            bolt_state = LockBoltState.LOCKED
        elif (
            bolt_lock_trait.lockedState
            == weave_security_pb2.BoltLockTrait.BoltLockedState.BOLT_LOCKED_STATE_UNLOCKED
        ):
            bolt_state = LockBoltState.UNLOCKED

        # Determine bolt actor
        actor_map = {
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_PHYSICAL: LockBoltActor.PHYSICAL,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_KEYPAD_PIN: LockBoltActor.KEYPAD,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_REMOTE_USER_EXPLICIT: LockBoltActor.REMOTE,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_REMOTE_USER_IMPLICIT: LockBoltActor.REMOTE,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_REMOTE_USER_OTHER: LockBoltActor.REMOTE,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_REMOTE_DELEGATE: LockBoltActor.REMOTE,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_VOICE_ASSISTANT: LockBoltActor.VOICE,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_LOCAL_IMPLICIT: LockBoltActor.IMPLICIT,
            weave_security_pb2.BoltLockTrait.BoltLockActorMethod.BOLT_LOCK_ACTOR_METHOD_LOW_POWER_SHUTDOWN: LockBoltActor.IMPLICIT,
        }
        bolt_actor = actor_map.get(
            bolt_lock_trait.boltLockActor.method, LockBoltActor.UNKNOWN
        )

        # Extract other properties from traits
        identity_trait = traits.get("device_identity")
        serial_number = identity_trait.serialNumber if identity_trait else key
        software_version = identity_trait.softwareVersion if identity_trait else None

        label_trait = traits.get("label")
        name = label_trait.label if label_trait and label_trait.label else "Lock"

        liveness_trait = traits.get("liveness")
        online = (
            liveness_trait.status
            == weave_heartbeat_pb2.LivenessTrait.LIVENESS_DEVICE_STATUS_ONLINE
            if liveness_trait
            else True
        )

        battery_trait = traits.get("battery_power_source")
        battery_level = (
            _scale_value(battery_trait.remaining.remainingPercent.value, 0, 1, 0, 100)
            if battery_trait
            else 0.0
        )

        tamper_trait = traits.get("tamper")
        tampered = (
            tamper_trait.tamperState
            == weave_security_pb2.TamperTrait.TamperState.TAMPER_STATE_TAMPERED
            if tamper_trait
            else False
        )

        settings_trait = traits.get("bolt_lock_settings")
        auto_relock_duration = (
            settings_trait.autoRelockDuration.seconds if settings_trait else 0
        )

        caps_trait = traits.get("bolt_lock_capabilities")
        max_auto_relock_duration = (
            caps_trait.maxAutoRelockDuration.seconds if caps_trait else 300
        )

        return NestLock(
            object_key=key,
            serial_number=serial_number,
            location=None,  # Location parsing for protobuf needs more context
            name=name,
            model="Nest x Yale Lock",
            software_version=software_version,
            online=online,
            bolt_state=bolt_state,
            bolt_actor=bolt_actor,
            battery_level=battery_level,
            tampered=tampered,
            auto_relock_on=settings_trait.autoRelockOn if settings_trait else False,
            auto_relock_duration=auto_relock_duration,
            max_auto_relock_duration=max_auto_relock_duration,
        )

    def _create_heatlink(self, thermostat: NestThermostat) -> NestHeatLink | None:
        """Create a virtual Heat Link device from thermostat data."""
        if not (
            thermostat.heat_link_model
            and thermostat.has_hot_water_control
            and thermostat.heat_link_serial_number
        ):
            return None
        return NestHeatLink(
            object_key=f"heatlink.{thermostat.heat_link_serial_number}",
            serial_number=thermostat.heat_link_serial_number,
            location=thermostat.location,
            name="Heat Link",
            model=thermostat.heat_link_model,
            software_version=thermostat.heat_link_sw_version,
            online=thermostat.online,
            associated_thermostat_object_key=thermostat.object_key,
            hot_water_active=thermostat.hot_water_active,
            hot_water_boost_active=thermostat.hot_water_boost_time_to_end > 0,
            hot_water_mode=thermostat.hot_water_mode,
            hot_water_away_enabled=thermostat.hot_water_away_enabled,
            current_temperature=thermostat.current_water_temperature,
            target_temperature=thermostat.hot_water_temperature,
            temperature_scale=thermostat.temperature_scale,
        )
