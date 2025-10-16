"""Nest legacy integration."""

from __future__ import annotations

import asyncio

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .coordinator import NestConfigEntry, NestCoordinator

_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.CLIMATE,
    Platform.EVENT,
    Platform.FAN,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    """Set up Nest from a config entry."""
    coordinator = NestCoordinator(hass, entry)

    try:
        await coordinator.async_initialize()
    except ConfigEntryAuthFailed:
        # Re-raise authentication failures to trigger reauth flow
        raise
    except Exception as err:
        # Other startup errors
        raise ConfigEntryNotReady(f"Failed to initialize Nest: {err}") from err

    entry.runtime_data = coordinator

    # Start subscribers to get all device data (including from protobuf)
    coordinator.async_start_subscriber()

    # Wait for the first protobuf update to ensure all devices are available
    try:
        await asyncio.wait_for(
            coordinator.first_protobuf_update_received.wait(), timeout=15
        )
    except TimeoutError as err:
        raise ConfigEntryNotReady(f"Failed to initialize Nest: {err}") from err

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data
    coordinator.async_stop_subscriber()
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
