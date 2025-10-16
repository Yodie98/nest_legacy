"""Water heater platform for Nest Heat Link."""

from __future__ import annotations

from typing import Any

from bidict import bidict

from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NestConfigEntry
from .entity import NestEntity
from .pynest.enums import HotWaterMode
from .pynest.models import NestHeatLink

# Bidirectional mapping between Nest API modes and Home Assistant states
_OPERATION_MODE_BIDICT: bidict[HotWaterMode, str] = bidict(
    {
        HotWaterMode.SCHEDULE: STATE_ON,
        HotWaterMode.OFF: STATE_OFF,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nest water heater platform from a config entry."""
    coordinator = entry.runtime_data
    entities = [
        NestHeatLinkWaterHeater(coordinator, device)
        for device in coordinator.data.values()
        if isinstance(device, NestHeatLink)
    ]
    async_add_devices(entities)


class NestHeatLinkWaterHeater(NestEntity[NestHeatLink], WaterHeaterEntity):
    """Representation of a Nest Heat Link."""

    _attr_name = None  # Main feature of the device
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.AWAY_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )
    _attr_operation_list = [*_OPERATION_MODE_BIDICT.inverse]
    _attr_min_temp = 30.0
    _attr_max_temp = 70.0

    @property
    def current_operation(self) -> str | None:
        """Return current operation."""
        return _OPERATION_MODE_BIDICT.get(self.device.hot_water_mode)

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self.device.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self.device.target_temperature

    @property
    def is_away_mode_on(self) -> bool | None:
        """Return true if away mode is on."""
        return self.device.hot_water_away_enabled

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if temp := kwargs.get("temperature"):
            await self._set_device_data({"hot_water_temperature": temp})

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Set new target operation mode."""
        nest_mode = _OPERATION_MODE_BIDICT.inverse.get(operation_mode)
        if nest_mode:
            await self._set_device_data({"hot_water_mode": nest_mode.value})

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the water heater (activates boost)."""
        await self._set_device_data({"hot_water_boost": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the water heater (cancels boost)."""
        await self._set_device_data({"hot_water_boost": False})

    async def async_turn_away_mode_on(self) -> None:
        """Turn away mode on."""
        await self._set_device_data({"hot_water_away_enabled": True})

    async def async_turn_away_mode_off(self) -> None:
        """Turn away mode off."""
        await self._set_device_data({"hot_water_away_enabled": False})
