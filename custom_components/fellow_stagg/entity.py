"""Base entity for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FellowStaggDataUpdateCoordinator


class FellowStaggEntity(CoordinatorEntity[FellowStaggDataUpdateCoordinator]):
  """Entity updated on every coordinator poll; unavailable after repeated poll failures."""

  _attr_has_entity_name = True

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, key: str) -> None:
    """Initialize with a unique_id of <address>_<key>."""
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator._address}_{key}"
    self._attr_device_info = coordinator.device_info

  @property
  def data(self) -> dict[str, Any]:
    """Last known kettle state, empty before the first successful poll."""
    return self.coordinator.data or {}
