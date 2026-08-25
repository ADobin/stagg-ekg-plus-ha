"""Support for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

import logging

from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
  Platform.BINARY_SENSOR,
  Platform.NUMBER,
  Platform.SENSOR,
  Platform.SWITCH,
  Platform.WATER_HEATER,
]

# Entities removed in 0.5: (platform, unique_id suffix)
REMOVED_ENTITIES = (
  (Platform.SENSOR, "power"),
  (Platform.SENSOR, "hold"),
  (Platform.SENSOR, "lifted"),
  (Platform.SENSOR, "target_temp"),
  (Platform.NUMBER, "polling_interval"),
  (Platform.SELECT, "temperature_unit"),
)


async def async_setup_entry(hass: HomeAssistant, entry: FellowStaggConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  address = entry.unique_id
  if address is None:
    raise ConfigEntryError("Config entry has no Bluetooth address")

  _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)
  _remove_stale_entities(hass, address)
  coordinator = FellowStaggDataUpdateCoordinator(hass, entry, address)

  # Raises ConfigEntryNotReady (HA retries setup) if the kettle can't be reached
  await coordinator.async_config_entry_first_refresh()
  entry.runtime_data = coordinator

  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
  return True


def _remove_stale_entities(hass: HomeAssistant, address: str) -> None:
  """Remove registry entries for entities this version no longer provides."""
  registry = er.async_get(hass)
  for platform, suffix in REMOVED_ENTITIES:
    if entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{address}_{suffix}"):
      _LOGGER.debug("Removing stale entity %s", entity_id)
      registry.async_remove(entity_id)


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
