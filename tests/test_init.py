"""Coordinator behaviour: setup, unit detection, partial polls, failures."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fellow_stagg.const import (
    CONF_TEMPERATURE_UNIT,
    DEFAULT_POLLING_INTERVAL,
    MAX_FAILED_POLLS,
    UNIT_CELSIUS,
    UNIT_FAHRENHEIT,
)
from custom_components.fellow_stagg.kettle_ble import KettleError

from .conftest import ADDRESS, FULL_STATE_C, FULL_STATE_F

TARGET = f"number.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}_target_temperature"
TARGET_SENSOR = f"sensor.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}_target_temperature"
POWER = f"switch.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}_power"
POSITION = f"sensor.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}_kettle_position"
HEATER = f"water_heater.fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}_water_heater"


async def advance(hass: HomeAssistant, seconds: float = DEFAULT_POLLING_INTERVAL) -> None:
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
    assert hass.states.get(POSITION).state == "On Base"
    heater = hass.states.get(HEATER)
    assert heater.attributes["temperature"] == 195
    assert heater.attributes["current_temperature"] == 150


async def test_setup_celsius(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    kettle.async_poll.return_value = dict(FULL_STATE_C)
    await setup_entry()
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
    await setup_entry()
    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["unit_of_measurement"] == expected
    assert hass.states.get(POWER).state == "off"


@pytest.mark.parametrize(
    ("option", "expected"),
    [(UNIT_FAHRENHEIT, UnitOfTemperature.FAHRENHEIT), (UNIT_CELSIUS, UnitOfTemperature.CELSIUS)],
)
async def test_missing_units_uses_configured_fallback(
    hass: HomeAssistant, setup_entry, kettle: MagicMock, option, expected
) -> None:
    hass.config.units = METRIC_SYSTEM if option == UNIT_FAHRENHEIT else US_CUSTOMARY_SYSTEM
    kettle.async_poll.return_value = {"power": False}
    await setup_entry({CONF_TEMPERATURE_UNIT: option})
    assert hass.states.get(TARGET).attributes["unit_of_measurement"] == expected


async def test_kettle_unit_overrides_fallback(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    kettle.async_poll.return_value = dict(FULL_STATE_F)
    await setup_entry({CONF_TEMPERATURE_UNIT: UNIT_CELSIUS})
    assert hass.states.get(TARGET).attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT


async def test_metadata_updates_once_units_arrive(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.async_poll.return_value = {"power": False}
    await setup_entry()
    assert hass.states.get(TARGET).attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS

    kettle.async_poll.return_value = dict(FULL_STATE_F)
    await advance(hass)
    target = hass.states.get(TARGET)
    assert target.state == "195"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT
    assert target.attributes["max"] == 212
    # HA converts the °F native value for the metric system; 195 would mean stale °C metadata
    assert float(hass.states.get(TARGET_SENSOR).state) == pytest.approx(90.56, abs=0.01)


async def test_partial_poll_keeps_known_values(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_poll.return_value = {"power": True}
    await advance(hass)
    assert hass.states.get(POWER).state == "on"
    target = hass.states.get(TARGET)
    assert target.state == "195"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT


async def test_unit_change_drops_stale_temperatures(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_poll.return_value = {"current_temp": 65, "units": "C"}
    await advance(hass)
    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert hass.states.get(HEATER).attributes["current_temperature"] == 65


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
