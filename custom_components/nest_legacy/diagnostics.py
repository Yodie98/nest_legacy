"""Provides diagnostics for Nest."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_COOKIES, CONF_ISSUE_TOKEN
from .coordinator import NestConfigEntry
from .pynest.models import NestDevice

TO_REDACT = [
    CONF_ACCESS_TOKEN,
    CONF_COOKIES,
    CONF_ISSUE_TOKEN,
    "access_token",
    "address_lines",
    "aux_primary_fabric_id",
    "city",
    "country",
    "email",
    "emergency_contact_description",
    "emergency_contact_phone",
    "ifj_primary_fabric_id",
    "latitude",
    "location",
    "longitude",
    "mac_address",
    "name",
    "parameters",
    "pairing_token",
    "postal_code",
    "profile_image_url",
    "serial_number",
    "service_config",
    "state",
    "sunrise",
    "sunset",
    "temp_c",
    "thread_ip_address",
    "thread_mac_address",
    "time_zone",
    "topaz_hush_key",
    "user",
    "userid",
    "wifi_mac_address",
    "zip",
    "cookie",
    "issuetoken",
    "title",
    "phone_numbers",
]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    # Ensure we have the latest data for diagnostics
    try:
        if coordinator.client.is_expired():
            await coordinator.async_reauthenticate()
    except Exception as e:  # noqa: BLE001
        return {"error": f"Authentication failed during diagnostics: {e}"}

    processed_data = {
        key: dataclasses.asdict(value)
        for key, value in coordinator.data.items()
        if value
    }

    data: dict[str, Any] = {
        "config_entry": entry.as_dict(),
        "processed_data": processed_data,
        "raw_api_data": coordinator.client.get_raw_data_for_diagnostics(),
    }

    return async_redact_data(data, TO_REDACT)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device entry."""
    coordinator = entry.runtime_data
    identifier = next(iter(device.identifiers))
    serial_number = identifier[1]

    device_data = coordinator.data.get(serial_number)
    if not isinstance(device_data, NestDevice):
        return {"error": "Device not found in coordinator data"}

    data: dict[str, Any] = {
        "device_entry": {
            "name": device.name,
            "model": device.model,
            "sw_version": device.sw_version,
            "hw_version": device.hw_version,
            "manufacturer": device.manufacturer,
        },
        "processed_data": dataclasses.asdict(device_data),
    }

    return async_redact_data(data, TO_REDACT)
