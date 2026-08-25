"""Support for Fellow Stagg EKG+ kettle sensors."""
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FellowStaggConfigEntry, FellowStaggDataUpdateCoordinator
from .entity import FellowStaggEntity

PARALLEL_UPDATES = 0


def _label(data: dict[str, Any], key: str, on: str, off: str) -> str | None:
    """Map a boolean state key to a label, None if not yet reported."""
    value = data.get(key)
    if value is None:
        return None
    return on if value else off


# Define value functions separately to avoid serialization issues
VALUE_FUNCTIONS: dict[str, Callable[[dict[str, Any]], Any | None]] = {
    "power": lambda data: _label(data, "power", "On", "Off"),
    "current_temp": lambda data: data.get("current_temp"),
    "target_temp": lambda data: data.get("target_temp"),
    "hold": lambda data: _label(data, "hold", "Hold", "Normal"),
    "lifted": lambda data: _label(data, "lifted", "Lifted", "On Base"),
    "countdown": lambda data: data.get("countdown"),
}


SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="power",
        name="Power",
        icon="mdi:power",
    ),
    SensorEntityDescription(
        key="current_temp",
        name="Current Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    SensorEntityDescription(
        key="target_temp",
        name="Target Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    SensorEntityDescription(
        key="hold",
        name="Hold Mode",
        icon="mdi:timer",
    ),
    SensorEntityDescription(
        key="lifted",
        name="Kettle Position",
        icon="mdi:cup",
    ),
    SensorEntityDescription(
        key="countdown",
        name="Countdown",
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FellowStaggConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Fellow Stagg sensors."""
    coordinator = entry.runtime_data

    async_add_entities(
        FellowStaggSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    )


class FellowStaggSensor(FellowStaggEntity, SensorEntity):
    """Fellow Stagg sensor."""

    def __init__(
        self,
        coordinator: FellowStaggDataUpdateCoordinator,
        description: SensorEntityDescription,
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
    def native_value(self) -> Any | None:
        """Return the state of the sensor."""
        return VALUE_FUNCTIONS[self.entity_description.key](self.data)
