"""Base entity for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

from fellow_stagg_ble import KettleState
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FellowStaggDataUpdateCoordinator


class FellowStaggEntity(CoordinatorEntity[FellowStaggDataUpdateCoordinator]):
  """Entity fed by the kettle's pushed state; unavailable while disconnected."""

  _attr_has_entity_name = True

  def __init__(self, coordinator: FellowStaggDataUpdateCoordinator, key: str) -> None:
    """Initialize with a unique_id of <address>_<key>."""
    super().__init__(coordinator)
    self._attr_unique_id = f"{coordinator.address}_{key}"
    self._attr_device_info = coordinator.device_info

  @property
  def available(self) -> bool:
    """Available only while connected to the kettle."""
    return super().available and self.coordinator.kettle.connected

  @property
  def state_data(self) -> KettleState:
    """Last known kettle state."""
    return self.coordinator.data if self.coordinator.data is not None else KettleState()
