"""Switch platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FellowStaggDataUpdateCoordinator
from .const import DOMAIN
from .entity import FellowStaggEntity

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
  hass: HomeAssistant,
  entry: ConfigEntry,
  async_add_entities: AddEntitiesCallback,
) -> None:
  """Set up Fellow Stagg switch based on a config entry."""
  coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
  async_add_entities([FellowStaggPowerSwitch(coordinator)])

class FellowStaggPowerSwitch(FellowStaggEntity, SwitchEntity):
  """Switch class for Fellow Stagg kettle power control."""

  _attr_name = "Power"

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator) -> None:
    """Initialize the switch."""
    super().__init__(coordinator, "power")

  @property
  def is_on(self) -> bool | None:
    """Return true if the switch is on."""
    return self.data.get("power")

  async def async_turn_on(self, **kwargs: Any) -> None:
    """Turn the switch on."""
    _LOGGER.debug("Turning power switch ON")
    await self.coordinator.async_set_power(True)

  async def async_turn_off(self, **kwargs: Any) -> None:
    """Turn the switch off."""
    _LOGGER.debug("Turning power switch OFF")
    await self.coordinator.async_set_power(False)
