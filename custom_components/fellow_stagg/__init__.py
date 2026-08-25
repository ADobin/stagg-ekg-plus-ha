"""Support for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
  Platform.SENSOR,
  Platform.SWITCH,
  Platform.NUMBER,
  Platform.SELECT,
  Platform.WATER_HEATER,
]


async def async_setup_entry(hass: HomeAssistant, entry: FellowStaggConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  address = entry.unique_id
  if address is None:
    raise ConfigEntryError("Config entry has no Bluetooth address")

  _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)
  interval_seconds = entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
  coordinator = FellowStaggDataUpdateCoordinator(hass, entry, address, timedelta(seconds=interval_seconds))

  # Raises ConfigEntryNotReady (HA retries setup) if the kettle can't be reached
  await coordinator.async_config_entry_first_refresh()
  entry.runtime_data = coordinator

  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  return True


async def async_unload_entry(hass: HomeAssistant, entry: FellowStaggConfigEntry) -> bool:
  """Unload a config entry; the coordinator disconnects via async_shutdown."""
  return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
