"""Support for Fellow Stagg EKG+ kettle sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fellow_stagg_ble import KettleState
from homeassistant.components.sensor import (
  SensorDeviceClass,
  SensorEntity,
  SensorEntityDescription,
  SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator
from .entity import FellowStaggEntity

PARALLEL_UPDATES = 0

@dataclass(frozen=True, kw_only=True)
class FellowStaggSensorEntityDescription(SensorEntityDescription):
  """Describes a numeric kettle state field."""

  value_fn: Callable[[KettleState], int | None]


# Keys keep the unique_ids of earlier releases
SENSOR_DESCRIPTIONS: tuple[FellowStaggSensorEntityDescription, ...] = (
  FellowStaggSensorEntityDescription(
    key="current_temp",
    translation_key="current_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda state: state.current_temperature,
  ),
  FellowStaggSensorEntityDescription(
    key="countdown",
    translation_key="countdown",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    value_fn=lambda state: state.countdown,
  ),
)


async def async_setup_entry(
  hass: HomeAssistant,
  entry: FellowStaggConfigEntry,
  async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
  """Set up the Fellow Stagg sensors."""
  coordinator = entry.runtime_data
  async_add_entities(FellowStaggSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS)


class FellowStaggSensor(FellowStaggEntity, SensorEntity):
  """Numeric kettle state."""

  entity_description: FellowStaggSensorEntityDescription

  def __init__(
    self, coordinator: FellowStaggDataUpdateCoordinator, description: FellowStaggSensorEntityDescription
  ) -> None:
    """Initialize the sensor."""
    super().__init__(coordinator, description.key)
    self.entity_description = description

  @property
  def native_unit_of_measurement(self) -> str | None:
    """Temperature sensors follow the kettle's unit."""
    if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE:
      return self.coordinator.temperature_unit
    return self.entity_description.native_unit_of_measurement

  @property
  def native_value(self) -> int | None:
    """Return the state of the sensor."""
    return self.entity_description.value_fn(self.state_data)
