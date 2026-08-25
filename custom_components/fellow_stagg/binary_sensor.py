"""Binary sensor platform for Fellow Stagg EKG+ kettle."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator
from .entity import FellowStaggEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FellowStaggBinarySensorEntityDescription(BinarySensorEntityDescription):
  """Describes a kettle state key; invert for keys reported in the opposite sense."""

  invert: bool = False


BINARY_SENSOR_DESCRIPTIONS: tuple[FellowStaggBinarySensorEntityDescription, ...] = (
  FellowStaggBinarySensorEntityDescription(key="lifted", translation_key="on_base", invert=True),
  FellowStaggBinarySensorEntityDescription(key="hold", translation_key="hold"),
  FellowStaggBinarySensorEntityDescription(
    key="hold_button",
    translation_key="hold_button",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
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
    super().__init__(coordinator, description.translation_key or description.key)
    self.entity_description = description

  @property
  def is_on(self) -> bool | None:
    """Return the state, None until reported."""
    value = self.data.get(self.entity_description.key)
    if value is None:
      return None
    return not value if self.entity_description.invert else value
