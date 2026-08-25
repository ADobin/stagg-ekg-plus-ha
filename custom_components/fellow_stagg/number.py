"""Number platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components.number import (
  NumberEntity,
  NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FellowStaggDataUpdateCoordinator
from .const import CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL, DOMAIN, MAX_POLLING_INTERVAL, MIN_POLLING_INTERVAL
from .entity import FellowStaggEntity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg number based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggTargetTemperature(coordinator), FellowStaggPollingInterval(coordinator)])

class FellowStaggTargetTemperature(FellowStaggEntity, NumberEntity):
  """Target temperature in the kettle's unit; unit and range follow the kettle."""

  _attr_name = "Target Temperature"
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
    _LOGGER.debug("Setting target temperature to %s°%s", value, self.coordinator.temperature_unit)
    await self.coordinator.async_set_temperature(value)


class FellowStaggPollingInterval(FellowStaggEntity, NumberEntity):
  """Number entity to configure the polling interval."""

  _attr_name = "Polling Interval"
  _attr_mode = NumberMode.BOX
  _attr_native_step = 1
  _attr_native_min_value = MIN_POLLING_INTERVAL
  _attr_native_max_value = MAX_POLLING_INTERVAL
  _attr_native_unit_of_measurement = "s"
  _attr_icon = "mdi:timer-sync"
  _attr_entity_category = EntityCategory.DIAGNOSTIC
  _attr_entity_registry_enabled_default = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the polling interval entity."""
    super().__init__(coordinator, "polling_interval")

  @property
  def native_value(self) -> int:
    """Return the current polling interval."""
    entry = self.hass.config_entries.async_get_entry(self.coordinator.entry_id)
    if entry is None:
      return DEFAULT_POLLING_INTERVAL
    return int(entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))

  async def async_set_native_value(self, value: float) -> None:
    """Set a new polling interval."""
    seconds = int(value)
    entry = self.hass.config_entries.async_get_entry(self.coordinator.entry_id)
    if entry is not None:
      self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_POLLING_INTERVAL: seconds})
    self.coordinator.update_interval = timedelta(seconds=seconds)
    self.async_write_ha_state()
