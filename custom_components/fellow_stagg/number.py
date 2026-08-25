"""Number platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
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
  """Set up Fellow Stagg number based on a config entry."""
  async_add_entities([FellowStaggTargetTemperature(entry.runtime_data)])


class FellowStaggTargetTemperature(FellowStaggEntity, NumberEntity):
  """Target temperature; native unit and range follow the kettle, HA converts for display."""

  _attr_translation_key = "target_temperature"
  _attr_device_class = NumberDeviceClass.TEMPERATURE
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1.0

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the number."""
    super().__init__(coordinator, "target_temp")

  @property
  def native_unit_of_measurement(self) -> str:
    """Return the kettle's temperature unit."""
    return self.coordinator.temperature_unit

  @property
  def native_min_value(self) -> float:
    """Return the minimum target temperature for the unit."""
    return self.coordinator.min_temp

  @property
  def native_max_value(self) -> float:
    """Return the maximum target temperature for the unit."""
    return self.coordinator.max_temp

  @property
  def native_value(self) -> float | None:
    """Return the current target temperature."""
    return self.data.get("target_temp")

  async def async_set_native_value(self, value: float) -> None:
    """Set new target temperature."""
    _LOGGER.debug("Setting target temperature to %s %s", value, self.coordinator.temperature_unit)
    await self.coordinator.async_set_temperature(value)
