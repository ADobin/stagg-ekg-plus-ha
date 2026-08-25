"""Diagnostics support for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import FellowStaggConfigEntry

TO_REDACT = frozenset({"address", "name", "source", "device", "bluetooth_address", "unique_id", "title"})


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FellowStaggConfigEntry) -> dict[str, Any]:
  """Return diagnostics for a config entry."""
  coordinator = entry.runtime_data
  service_info = bluetooth.async_last_service_info(hass, coordinator.address, connectable=True)
  return {
    "entry": async_redact_data(entry.as_dict(), TO_REDACT),
    "coordinator": {
      "data": coordinator.data,
      "last_update_success": coordinator.last_update_success,
      "failed_polls": coordinator._failed_polls,
      "update_interval": str(coordinator.update_interval),
      "temperature_unit": coordinator.temperature_unit,
      "cached_service_info": coordinator._last_service_info is not None,
    },
    "service_info": async_redact_data(service_info.as_dict() if service_info else None, TO_REDACT),
  }
