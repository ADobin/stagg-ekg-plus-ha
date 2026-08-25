"""Service calls on the switch, number, water heater and select entities."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest

from custom_components.fellow_stagg.kettle_ble import KettleError

from .conftest import FULL_STATE_C, FULL_STATE_F
from .test_init import HEATER, POWER, TARGET, advance


async def test_switch_turn_on_sends_power_and_refreshes(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_poll.return_value = {**FULL_STATE_F, "power": True}
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    await hass.async_block_till_done()
    kettle.async_set_power.assert_awaited_once()
    assert kettle.async_set_power.await_args.args[1] is True
    assert hass.states.get(POWER).state == "on"
    assert hass.states.get(HEATER).state == "electric"


async def test_switch_turn_off(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    await hass.services.async_call("switch", "turn_off", {"entity_id": POWER}, blocking=True)
    assert kettle.async_set_power.await_args.args[1] is False


async def test_number_sets_fahrenheit(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 200}, blocking=True)
    kettle.async_set_temperature.assert_awaited_once()
    assert kettle.async_set_temperature.await_args.args[1] == 200
    assert kettle.async_set_temperature.await_args.kwargs == {"fahrenheit": True}


async def test_number_sets_celsius(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.async_poll.return_value = dict(FULL_STATE_C)
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 91}, blocking=True)
    assert kettle.async_set_temperature.await_args.args[1] == 91
    assert kettle.async_set_temperature.await_args.kwargs == {"fahrenheit": False}


async def test_number_rejects_out_of_range_for_unit(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    hass.config.units = METRIC_SYSTEM
    kettle.async_poll.return_value = dict(FULL_STATE_C)
    await setup_entry()
    with pytest.raises(Exception, match="outside valid range 40 - 100"):
        await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 195}, blocking=True)
    kettle.async_set_temperature.assert_not_awaited()


async def test_water_heater_set_temperature_and_power(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    """water_heater.set_temperature takes the HA unit system's unit and converts to the entity's."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_entry()
    await hass.services.async_call(
        "water_heater", "set_temperature", {"entity_id": HEATER, "temperature": 190}, blocking=True
    )
    assert kettle.async_set_temperature.await_args.args[1] == 190
    await hass.services.async_call("water_heater", "turn_on", {"entity_id": HEATER}, blocking=True)
    assert kettle.async_set_power.await_args.args[1] is True


async def test_command_failure_raises_homeassistant_error(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    kettle.async_set_power.side_effect = KettleError("Connection closed")
    with pytest.raises(HomeAssistantError, match="Could not reach the kettle: Connection closed"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    assert hass.states.get(POWER).state == "off"


async def test_command_uses_cached_device_when_not_advertising(
    hass: HomeAssistant, setup_entry, kettle: MagicMock, ble_lookup: MagicMock, ble_device: MagicMock
) -> None:
    await setup_entry()
    ble_lookup.return_value = None
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    assert kettle.async_set_power.await_args.args[0] is ble_device


async def test_delayed_kettle_response_does_not_fail_command(
    hass: HomeAssistant, setup_entry, kettle: MagicMock
) -> None:
    """The refresh right after a command may still show the old state; the next poll catches up."""
    await setup_entry()
    await hass.services.async_call("switch", "turn_on", {"entity_id": POWER}, blocking=True)
    assert hass.states.get(POWER).state == "off"
    kettle.async_poll.return_value = {**FULL_STATE_F, "power": True}
    await advance(hass)
    assert hass.states.get(POWER).state == "on"


async def test_water_heater_operation_mode(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    await setup_entry()
    await hass.services.async_call(
        "water_heater", "set_operation_mode", {"entity_id": HEATER, "operation_mode": "electric"}, blocking=True
    )
    assert kettle.async_set_power.await_args.args[1] is True
    await hass.services.async_call(
        "water_heater", "set_operation_mode", {"entity_id": HEATER, "operation_mode": "off"}, blocking=True
    )
    assert kettle.async_set_power.await_args.args[1] is False


async def test_number_converts_from_ha_unit_system(hass: HomeAssistant, setup_entry, kettle: MagicMock) -> None:
    """A metric HA sets 90 °C on a °F kettle: the kettle receives 194 °F."""
    hass.config.units = METRIC_SYSTEM
    await setup_entry()
    await hass.services.async_call("number", "set_value", {"entity_id": TARGET, "value": 90}, blocking=True)
    assert kettle.async_set_temperature.await_args.args[1] == 194
    assert kettle.async_set_temperature.await_args.kwargs == {"fahrenheit": True}
