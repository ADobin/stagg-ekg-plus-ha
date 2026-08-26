"""Coordinator behaviour: setup, pushed state, unit handling, connection loss and recovery."""
from __future__ import annotations

import dataclasses
from datetime import timedelta
import time
from unittest.mock import MagicMock

from fellow_stagg_ble import FellowStaggConnectionError, KettleState, TemperatureUnit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fellow_stagg.const import DOMAIN, FRAME_TIMEOUT, UPDATE_INTERVAL

from .conftest import ADDRESS, FULL_STATE_C, FULL_STATE_F, KettleHarness

PREFIX = f"fellow_stagg_ekg_{ADDRESS.replace(':', '_').lower()}"
TARGET = f"number.{PREFIX}_target_temperature"
CURRENT = f"sensor.{PREFIX}_current_temperature"
COUNTDOWN = f"sensor.{PREFIX}_auto_off_countdown"
POWER = f"switch.{PREFIX}_power"
ON_BASE = f"binary_sensor.{PREFIX}_on_base"
HOLD = f"binary_sensor.{PREFIX}_hold"
HEATER = f"water_heater.{PREFIX}"  # primary entity: device name


async def advance(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_setup_fahrenheit(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.LOADED
    assert kettle.kettle.connected

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


async def test_setup_celsius(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.initial_state = FULL_STATE_C
    entry = await setup_entry()
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.CELSIUS
    target = hass.states.get(TARGET)
    assert target.state == "91"
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert (target.attributes["min"], target.attributes["max"]) == (40, 100)


async def test_pushed_state_updates_entities_immediately(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    await setup_entry()
    kettle.kettle.push({"power": True, "current_temperature": 160})
    await hass.async_block_till_done()
    assert hass.states.get(POWER).state == "on"
    assert hass.states.get(HEATER).state == "electric"
    assert hass.states.get(CURRENT).state == "160"
    assert hass.states.get(TARGET).state == "195"  # untouched keys are kept


async def test_metadata_updates_once_units_arrive(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    """A metric HA with a °F kettle: native values are °F, HA converts for display."""
    hass.config.units = METRIC_SYSTEM
    kettle.initial_state = FULL_STATE_C
    entry = await setup_entry()
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.CELSIUS
    assert hass.states.get(TARGET).attributes["max"] == 100

    kettle.kettle.push({"target_temperature": 195, "current_temperature": 150, "unit": TemperatureUnit.FAHRENHEIT})
    await hass.async_block_till_done()
    assert entry.runtime_data.temperature_unit == UnitOfTemperature.FAHRENHEIT
    target = hass.states.get(TARGET)
    assert target.attributes["unit_of_measurement"] == UnitOfTemperature.CELSIUS
    assert target.state == "91.0"  # 195 °F shown in °C, rounded to the 1° step
    assert (target.attributes["min"], target.attributes["max"]) == (40, 100)
    assert float(hass.states.get(CURRENT).state) == pytest.approx(65.6, abs=0.1)  # 150 °F in °C


async def test_unit_change_drops_stale_temperatures(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    """The library clears the target until it is re-read in the new unit; entities follow."""
    await setup_entry()
    kettle.kettle.push({"current_temperature": 65, "unit": TemperatureUnit.CELSIUS, "target_temperature": None})
    await hass.async_block_till_done()
    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["max"] == 212  # HA unit system (°F) display of the 100 °C max
    assert hass.states.get(HEATER).attributes["current_temperature"] == 149  # 65 °C shown in °F


async def test_current_temperature_unknown_without_reading(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    kettle.initial_state = dataclasses.replace(FULL_STATE_F, current_temperature=None)
    await setup_entry()
    assert hass.states.get(CURRENT).state == "unknown"
    assert hass.states.get(HEATER).attributes["current_temperature"] is None


async def test_connection_loss_marks_unavailable_and_reconnects_on_tick(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    await setup_entry()
    kettle.connect_error = FellowStaggConnectionError("Connection closed")
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).state == "unavailable"
    assert hass.states.get(POWER).state == "unavailable"
    assert hass.states.get(HEATER).state == "unavailable"
    assert len(kettle.kettle.connect_calls) == 2  # immediate reconnect attempt failed

    kettle.connect_error = None
    await advance(hass, UPDATE_INTERVAL + 1)  # next tick reconnects
    assert kettle.kettle.connected
    assert hass.states.get(TARGET).state == "195"
    assert hass.states.get(POWER).state == "off"


async def test_immediate_reconnect_restores_entities(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert kettle.kettle.connected
    assert hass.states.get(TARGET).state == "195"


async def test_reconnect_without_state_stays_unavailable(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    await setup_entry()
    kettle.initial_state = None  # the new connection delivers nothing
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).state == "unavailable"
    assert not kettle.kettle.connected  # a silent connection is torn down

    kettle.initial_state = FULL_STATE_F
    await advance(hass, UPDATE_INTERVAL + 1)
    assert hass.states.get(TARGET).state == "195"


async def test_not_advertising_waits_for_advertisement(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, ble_lookup: MagicMock
) -> None:
    entry = await setup_entry()
    entry.runtime_data._last_service_info = None  # no cached directed-connect path either
    ble_lookup.return_value = None
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert not kettle.kettle.connected
    assert hass.states.get(TARGET).state == "unavailable"

    kettle.advertise()
    await advance(hass, UPDATE_INTERVAL + 1)  # request_refresh is debounced after the failed attempt
    assert kettle.kettle.connected
    assert entry.runtime_data._last_service_info is not None
    assert hass.states.get(TARGET).state == "195"


async def test_cached_device_allows_reconnect_without_advertisement(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, ble_lookup: MagicMock, ble_device: MagicMock
) -> None:
    await setup_entry()
    ble_lookup.return_value = None
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert kettle.kettle.connected
    assert kettle.kettle.connect_calls[-1] is ble_device


async def test_silent_link_is_reset_on_tick(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    kettle.kettle.last_frame_at = time.monotonic() - FRAME_TIMEOUT - 1
    await advance(hass, UPDATE_INTERVAL + 1)
    assert kettle.kettle.disconnect_calls == 1
    assert kettle.kettle.connected  # reconnected
    assert hass.states.get(TARGET).state == "195"


async def test_idle_kettle_is_not_reconnected_while_frames_flow(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    """Unchanged state still counts as frames; the tick must not churn the connection."""
    await setup_entry()
    for _ in range(3):
        kettle.kettle.last_frame_at = time.monotonic()
        await advance(hass, UPDATE_INTERVAL + 1)
    assert kettle.kettle.disconnect_calls == 0
    assert len(kettle.kettle.connect_calls) == 1


async def test_unreachable_at_setup_retries(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    kettle.connect_error = FellowStaggConnectionError("Connection closed")
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get(TARGET) is None


async def test_never_seen_at_setup_retries(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, ble_lookup: MagicMock
) -> None:
    """No advertisement known yet: retry until discovery reloads the entry."""
    ble_lookup.return_value = None
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not kettle.instances


@pytest.mark.parametrize(
    "initial_state",
    [None, KettleState(power=False, on_base=True), KettleState(power=False, target_temperature=195)],
)
async def test_incomplete_state_at_setup_retries(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, initial_state
) -> None:
    kettle.initial_state = initial_state
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not kettle.kettle.connected


async def test_unload_disconnects(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    entry = await setup_entry()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert kettle.kettle.disconnect_calls == 1
    assert not kettle.advertisement_callbacks


async def test_entity_names_come_from_translations(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    device = f"Fellow Stagg EKG+ {ADDRESS}"
    assert hass.states.get(TARGET).attributes["friendly_name"] == f"{device} Target temperature"
    assert hass.states.get(ON_BASE).attributes["friendly_name"] == f"{device} On base"
    assert hass.states.get(HEATER).attributes["friendly_name"] == device


async def test_stale_entities_from_older_versions_are_removed(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, entity_registry: er.EntityRegistry
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={"address": ADDRESS}, minor_version=2)
    entry.add_to_hass(hass)
    stale = [
        ("sensor", f"{ADDRESS}_lifted"),
        ("sensor", f"{ADDRESS}_power"),
        ("number", f"{ADDRESS}_polling_interval"),
        ("select", f"{ADDRESS}_temperature_unit"),
    ]
    for platform, unique_id in stale:
        entity_registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry)
    foreign = entity_registry.async_get_or_create("sensor", DOMAIN, "11:22:33:44:55:66_lifted")

    kettle.connect_error = FellowStaggConnectionError("unreachable")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, f"{ADDRESS}_lifted")  # kept until setup succeeds

    kettle.connect_error = None
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    for platform, unique_id in stale:
        assert entity_registry.async_get_entity_id(platform, DOMAIN, unique_id) is None
    assert entity_registry.async_get(foreign.entity_id)  # another kettle's entity is untouched
    assert entity_registry.async_get_entity_id("number", DOMAIN, f"{ADDRESS}_target_temp")


async def test_hass_stop_disconnects(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert kettle.kettle.disconnect_calls == 1
