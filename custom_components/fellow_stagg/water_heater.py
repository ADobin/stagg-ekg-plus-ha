"""Water heater platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.water_heater import (
  STATE_ELECTRIC,
  WaterHeaterEntity,
  WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator
from .entity import FellowStaggEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

async def async_setup_entry(
  hass: HomeAssistant,
  entry: FellowStaggConfigEntry,
  async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
  """Set up Fellow Stagg water heater based on a config entry."""
  coordinator = entry.runtime_data
  async_add_entities([FellowStaggWaterHeater(coordinator)])

class FellowStaggWaterHeater(FellowStaggEntity, WaterHeaterEntity):
  """Water heater entity for Fellow Stagg kettle; unit and range follow the kettle."""

  _attr_name = None  # primary entity: takes the device name
  _attr_supported_features = (
    WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
    | WaterHeaterEntityFeature.ON_OFF
  )
  _attr_operation_list = [STATE_OFF, STATE_ELECTRIC]
  _attr_precision = PRECISION_WHOLE
  _attr_target_temperature_step = 1

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the water heater."""
    super().__init__(coordinator, "water_heater")

  @property
  def temperature_unit(self) -> str:
    """Return the kettle's temperature unit."""
    return self.coordinator.temperature_unit

  @property
  def min_temp(self) -> float:
    """Return the minimum target temperature for the unit."""
    return self.coordinator.min_temp

  @property
  def max_temp(self) -> float:
    """Return the maximum target temperature for the unit."""
    return self.coordinator.max_temp

  @property
  def current_temperature(self) -> float | None:
    """Return the current temperature."""
    return self.state_data.current_temperature

  @property
  def target_temperature(self) -> float | None:
    """Return the target temperature."""
    return self.state_data.target_temperature

  @property
  def current_operation(self) -> str | None:
    """Return current operation."""
    power = self.state_data.power
    if power is None:
      return None
    return STATE_ELECTRIC if power else STATE_OFF

  async def async_set_operation_mode(self, operation_mode: str) -> None:
    """Set operation mode."""
    await self.coordinator.async_set_power(operation_mode == STATE_ELECTRIC)

  async def async_set_temperature(self, **kwargs: Any) -> None:
    """Set new target temperature."""
    temperature = kwargs.get(ATTR_TEMPERATURE)
    if temperature is None:
      return
    _LOGGER.debug("Setting water heater target temperature to %s %s", temperature, self.coordinator.temperature_unit)
    await self.coordinator.async_set_temperature(temperature)

  async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn the water heater on."""
    _LOGGER.debug("Turning water heater ON")
    await self.coordinator.async_set_power(True)

  async def async_turn_off(self, **kwargs: Any) -> None:
    """Turn the water heater off."""
    _LOGGER.debug("Turning water heater OFF")
    await self.coordinator.async_set_power(False)
