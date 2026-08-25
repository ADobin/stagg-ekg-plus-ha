"""Support for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import CONF_ADDRESS, Platform
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


async def async_migrate_entry(hass: HomeAssistant, entry: FellowStaggConfigEntry) -> bool:
  """Migrate the data key bluetooth_address (1.1) to address (1.2)."""
  if entry.version != 1:
    return False
  if entry.minor_version < 2:
    data = {k: v for k, v in entry.data.items() if k != "bluetooth_address"}
    data[CONF_ADDRESS] = entry.data.get(CONF_ADDRESS) or entry.data.get("bluetooth_address") or entry.unique_id
    hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
  return True
