"""Select platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FellowStaggDataUpdateCoordinator
from .const import CONF_TEMPERATURE_UNIT, DOMAIN, TEMPERATURE_UNIT_OPTIONS, UNIT_AUTO
from .entity import FellowStaggEntity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg select based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggTemperatureUnit(coordinator)])

class FellowStaggTemperatureUnit(FellowStaggEntity, SelectEntity):
  """Unit assumed until the kettle reports its own; the kettle's unit always wins."""

  _attr_name = "Fallback Temperature Unit"
  _attr_translation_key = "temperature_unit"
  _attr_options = TEMPERATURE_UNIT_OPTIONS
  _attr_icon = "mdi:thermometer-lines"
  _attr_entity_category = EntityCategory.CONFIG
  _attr_entity_registry_enabled_default = False

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the select."""
    super().__init__(coordinator, "temperature_unit")

  @property
  def current_option(self) -> str:
    """Return the configured fallback unit."""
    return self.coordinator.config_entry.options.get(CONF_TEMPERATURE_UNIT, UNIT_AUTO)

  async def async_select_option(self, option: str) -> None:
    """Store the fallback unit and refresh entities that depend on it."""
    entry = self.coordinator.config_entry
    self.hass.config_entries.async_update_entry(
      entry, options={**entry.options, CONF_TEMPERATURE_UNIT: option}
    )
    _LOGGER.debug("Fallback temperature unit set to %s", option)
    self.coordinator.async_update_listeners()
