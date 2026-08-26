"""Binary sensor platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fellow_stagg_ble import KettleState
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator
from .entity import FellowStaggEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FellowStaggBinarySensorEntityDescription(BinarySensorEntityDescription):
  """Describes a boolean kettle state field."""

  is_on_fn: Callable[[KettleState], bool | None]


# Keys keep the unique_ids of earlier releases
BINARY_SENSOR_DESCRIPTIONS: tuple[FellowStaggBinarySensorEntityDescription, ...] = (
  FellowStaggBinarySensorEntityDescription(
    key="on_base", translation_key="on_base", is_on_fn=lambda state: state.on_base
  ),
  FellowStaggBinarySensorEntityDescription(key="hold", translation_key="hold", is_on_fn=lambda state: state.hold),
  FellowStaggBinarySensorEntityDescription(
    key="hold_button",
    translation_key="hold_button",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    is_on_fn=lambda state: state.hold_button,
  ),
)


async def async_setup_entry(
  hass: HomeAssistant,
  entry: FellowStaggConfigEntry,
  async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
  """Set up Fellow Stagg binary sensors based on a config entry."""
  coordinator = entry.runtime_data
  async_add_entities(
    FellowStaggBinarySensor(coordinator, description) for description in BINARY_SENSOR_DESCRIPTIONS
  )


class FellowStaggBinarySensor(FellowStaggEntity, BinarySensorEntity):
  """Boolean kettle state."""

  entity_description: FellowStaggBinarySensorEntityDescription

  def __init__(
    self, coordinator: FellowStaggDataUpdateCoordinator, description: FellowStaggBinarySensorEntityDescription
  ) -> None:
    """Initialize the binary sensor."""
    super().__init__(coordinator, description.key)
    self.entity_description = description

  @property
  def is_on(self) -> bool | None:
    """Return the state, None until reported."""
    return self.entity_description.is_on_fn(self.state_data)
