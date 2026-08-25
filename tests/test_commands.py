"""Service calls on the switch, number and water heater entities."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_system import METRIC_SYSTEM
import pytest

from custom_components.fellow_stagg.const import UPDATE_INTERVAL
from custom_components.fellow_stagg.kettle_ble import KettleError

from .conftest import FULL_STATE_C, KettleHarness
from .test_init import HEATER, POWER, TARGET, advance


async def test_switch_turn_on_sends_power(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    kettle.set_power.assert_awaited_once_with(True)
    # The kettle confirms by pushing its state
    kettle.kettle.push({"power": True})
    await hass.async_block_till_done()
    assert hass.states.get(POWER).state == "on"
    assert hass.states.get(HEATER).state == "electric"


async def test_switch_turn_off(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    await hass.services.async_call("switch", "turn_off", {"entity_id": POWER}, blocking=True)
    kettle.set_power.assert_awaited_once_with(False)


async def test_number_sets_fahrenheit(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 200}, blocking=True)
    kettle.set_temperature.assert_awaited_once_with(200, fahrenheit=True)


async def test_number_sets_celsius(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.initial_state = dict(FULL_STATE_C)
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 91}, blocking=True)
    kettle.set_temperature.assert_awaited_once_with(91, fahrenheit=False)


async def test_number_converts_from_ha_unit_system(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    """A metric HA sets 90 °C on a °F kettle: the kettle receives 194 °F."""
    hass.config.units = METRIC_SYSTEM
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 90}, blocking=True)
    kettle.set_temperature.assert_awaited_once_with(194, fahrenheit=True)


async def test_number_rejects_out_of_range_for_unit(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.initial_state = dict(FULL_STATE_C)
    await setup_entry()
    with pytest.raises(Exception, match="outside valid range 40 - 100"):
        await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 195}, blocking=True)
    kettle.set_temperature.assert_not_awaited()


async def test_water_heater_set_temperature_and_power(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": HEATER, "temperature": 190}, blocking=True
    )
    kettle.set_temperature.assert_awaited_once_with(190, fahrenheit=True)
    await hass.services.async_call("water_heater", "turn_on", {"entity_id": HEATER}, blocking=True)
    kettle.set_power.assert_awaited_once_with(True)


async def test_water_heater_operation_mode(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    await hass.services.async_call(
        "water_heater", "set_operation_mode", {"entity_id": HEATER, "operation_mode": "electric"}, blocking=True
    )
    assert kettle.set_power.await_args.args == (True,)
    await hass.services.async_call(
        "water_heater", "set_operation_mode", {"entity_id": HEATER, "operation_mode": "off"}, blocking=True
    )
    assert kettle.set_power.await_args.args == (False,)


async def test_command_failure_raises_translated_error(hass: HomeAssistant, setup_entry, kettle: KettleHarness) -> None:
    await setup_entry()
    kettle.set_power.side_effect = KettleError("Connection closed")
    with pytest.raises(HomeAssistantError, match="Could not reach the kettle: Connection closed"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    assert hass.states.get(POWER).state == "off"


async def test_commands_skipped_while_unavailable_then_work_after_reconnect(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness, ble_lookup: MagicMock
) -> None:
    entry = await setup_entry()
    ble_lookup.return_value = None
    entry.runtime_data._last_service_info = None
    kettle.kettle.drop()
    await hass.async_block_till_done()
    assert not kettle.kettle.connected
    assert hass.states.get(POWER).state == "unavailable"

    # HA does not deliver service calls to unavailable entities
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    kettle.set_power.assert_not_awaited()

    ble_lookup.return_value = MagicMock(name="BLEDevice")
    kettle.advertise()
    await advance(hass, UPDATE_INTERVAL + 1)
    assert kettle.kettle.connected
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    kettle.set_power.assert_awaited_once_with(True)


async def test_write_failure_is_treated_as_connection_loss(
    hass: HomeAssistant, setup_entry, kettle: KettleHarness
) -> None:
    """A failed write drops the link; the coordinator must reconnect (or go unavailable), not stay stale."""
    entry = await setup_entry()
    kettle.set_power.side_effect = KettleError("Command failed: ATT error")
    kettle.kettle.connected = False
    kettle.kettle.on_disconnect()  # what KettleBLEClient now does after a failed write
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.disconnects == 1
    assert kettle.kettle.connected  # reconnected immediately
