"""Coordinator behaviour: setup, unit detection, partial polls, failures."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fellow_stagg.const import DOMAIN, MAX_FAILED_POLLS, POLLING_INTERVAL
from custom_components.fellow_stagg.kettle_ble import KettleError

from .conftest import ADDRESS, FULL_STATE_C, FULL_STATE_F

PREFIX = f"fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}"
TARGET = f"number.{PREFIX}_target_temperature"
CURRENT = f"sensor.{PREFIX}_current_temperature"
COUNTDOWN = f"sensor.{PREFIX}_auto_off_countdown"
POWER = f"switch.{PREFIX}_power"
ON_BASE = f"binary_sensor.{PREFIX}_on_base"
HOLD = f"binary_sensor.{PREFIX}_hold"
HEATER = f"water_heater.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}"  # primary entity: device name


async def advance(hass: HomeAssistant, seconds: float = POLLING_INTERVAL) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_setup_fahrenheit(hass: HomeAssistant, setup_entry) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.LOADED

    target = hass.states.get(TARGET)
    assert target.state == "195"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT
    assert (target.attributes["min"], target.attributes["max"]) == (104, 212)
    assert hass.states.get(POWER).state == "off"
    assert hass.states.get(ON_BASE).state == "on"
    assert hass.states.get(HOLD).state == "off"
    assert hass.states.get(COUNTDOWN).state == "0"
    heater = hass.states.get(HEATER)
    assert heater.state == "off"
    assert heater.attributes["operation_list"] == ["off", "electric"]
    assert heater.attributes["temperature"] == 195
    assert heater.attributes["current_temperature"] == 150


async def test_setup_celsius(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.async_poll.return_value = dict(FULL_STATE_C)
    entry = await setup_entry()
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.CELSIUS
    target = hass.states.get(TARGET)
    assert target.state == "91"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert (target.attributes["min"], target.attributes["max"]) == (40, 100)


@pytest.mark.parametrize(
    ("unit_system", "expected"),
    [(US_CUSTOMARY_SYSTEM, UnitOfTemperature.FAHRENHEIT), (METRIC_SYSTEM, UnitOfTemperature.CELSIUS)],
)
async def test_missing_units_falls_back_to_ha_unit_system(
    hass: HomeAssistant, setup_entry, kettle: MagicMock, unit_system, expected
) -> None:
    hass.config.units = unit_system
    kettle.async_poll.return_value = {"power": False, "lifted": False}
    entry = await setup_entry()
    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["unit_of_measurement"] == expected
    assert entry.runtime_data.temperature_unit == expected
    assert hass.states.get(POWER).state == "off"


async def test_metadata_updates_once_units_arrive(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    """A metric HA with a °F kettle: native values are °F, HA converts for display."""
    hass.config.units = METRIC_SYSTEM
    kettle.async_poll.return_value = {"power": False}
    entry = await setup_entry()
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.CELSIUS
    assert hass.states.get(TARGET).attributes["max"] == 100

    kettle.async_poll.return_value = dict(FULL_STATE_F)
    await advance(hass)
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.FAHRENHEIT
    target = hass.states.get(TARGET)
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert target.state == "91.0"  # 195 °F shown in °C, rounded to the 1° step
    assert (target.attributes["min"], target.attributes["max"]) == (40, 100)
    assert float(hass.states.get(CURRENT).state) == pytest.approx(65.6, abs=0.1)  # 150 °F in °C


async def test_partial_poll_keeps_known_values(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_poll.return_value = {"power": True}
    await advance(hass)
    assert hass.states.get(POWER).state == "on"
    assert hass.states.get(HEATER).state == "electric"
    target = hass.states.get(TARGET)
    assert target.state == "195"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT


async def test_unit_change_drops_stale_temperatures(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_poll.return_value = {"current_temp": 65, "units": "C"}
    await advance(hass)
    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["max"] == 212  # HA unit system (°F) display of the 100 °C max
    assert hass.states.get(HEATER).attributes["current_temperature"] == 149  # 65 °C shown in °F


async def test_transient_failures_keep_state_then_unavailable(
    hass: HomeAssistant, setup_entry, kettle: MagicMock
) -> None:
    await setup_entry()
    kettle.async_poll.side_effect = KettleError("Connection closed")
    for _ in range(MAX_FAILED_POLLS - 1):
        await advance(hass)
        assert hass.states.get(TARGET).state == "195"
        assert hass.states.get(POWER).state == "off"

    await advance(hass)
    assert hass.states.get(TARGET).state == "unavailable"
    assert hass.states.get(POWER).state == "unavailable"
    assert hass.states.get(HEATER).state == "unavailable"

    kettle.async_poll.side_effect = None
    await advance(hass)
    assert hass.states.get(TARGET).state == "195"


async def test_no_advertisement_counts_as_failure(
    hass: HomeAssistant, setup_entry, kettle: MagicMock, ble_lookup: MagicMock
) -> None:
    entry = await setup_entry()
    ble_lookup.return_value = None
    kettle.async_poll.side_effect = KettleError("Kettle not reachable")
    # Drop the cached service info so no directed connect is possible either
    entry.runtime_data._last_service_info = None
    for _ in range(MAX_FAILED_POLLS):
        await advance(hass)
    assert kettle.async_poll.await_args.args[0] is None
    assert hass.states.get(TARGET).state == "unavailable"


async def test_unreachable_at_setup_retries(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    kettle.async_poll.side_effect = KettleError("Connection closed")
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get(TARGET) is None


async def test_unload_disconnects(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    entry = await setup_entry()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    kettle.disconnect.assert_awaited_once()


async def test_entity_names_come_from_translations(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    device = f"Fellow Stagg EKG+ {ADDRESS}"
    assert hass.states.get(TARGET).attributes["friendly_name"] == f"{device} Target temperature"
    assert hass.states.get(ON_BASE).attributes["friendly_name"] == f"{device} On base"
    assert hass.states.get(HEATER).attributes["friendly_name"] == device


async def test_current_temperature_unknown_without_reading(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    kettle.async_poll.return_value = {**FULL_STATE_F, "current_temp": None}
    await setup_entry()
    assert hass.states.get(CURRENT).state == "unknown"
    assert hass.states.get(HEATER).attributes["current_temperature"] is None


async def test_stale_entities_from_older_versions_are_removed(
    hass: HomeAssistant, setup_entry, entity_registry: er.EntityRegistry
) -> None:
    stale = [
        ("sensor", f"{ADDRESS}_lifted"),
        ("sensor", f"{ADDRESS}_power"),
        ("number", f"{ADDRESS}_polling_interval"),
        ("select", f"{ADDRESS}_temperature_unit"),
    ]
    for platform, unique_id in stale:
        entity_registry.async_get_or_create(platform, DOMAIN, unique_id)
    await setup_entry()
    for platform, unique_id in stale:
        assert entity_registry.async_get_entity_id(platform, DOMAIN, unique_id) is None
    assert entity_registry.async_get_entity_id("number", DOMAIN, f"{ADDRESS}_target_temp")
