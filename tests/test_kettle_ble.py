"""Tests for the BLE client: frame parsing and polling."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fellow_stagg import kettle_ble
from custom_components.fellow_stagg.kettle_ble import KettleBLEClient, KettleError

from .conftest import ADDRESS, frame


@pytest.fixture
def client() -> KettleBLEClient:
    return KettleBLEClient(ADDRESS)


def test_parse_full_state_fahrenheit(client: KettleBLEClient) -> None:
    notifications = (
        frame(0, [0]) + frame(1, [0]) + frame(2, [195, 1]) + frame(3, [150, 1])
        + frame(4, [0, 0]) + frame(6, [0]) + frame(8, [1, 1, 0])
    )
    assert client.parse_notifications(notifications) == {
        "power": False,
        "hold_button": False,
        "target_temp": 195,
        "units": "F",
        "current_temp": 150,
        "countdown": 0,
        "hold": False,
        "lifted": False,
    }


def test_parse_celsius_and_lifted(client: KettleBLEClient) -> None:
    state = client.parse_notifications(frame(2, [91, 0]) + frame(8, [0, 1, 0]) + frame(0, [1]))
    assert state == {"target_temp": 91, "units": "C", "lifted": True, "power": True}


def test_parse_partial_state_has_no_units(client: KettleBLEClient) -> None:
    assert client.parse_notifications(frame(0, [1]) + frame(8, [1, 1, 0])) == {"power": True, "lifted": False}


def test_parse_empty(client: KettleBLEClient) -> None:
    assert client.parse_notifications([]) == {}


def test_parse_coalesced_and_split_notifications(client: KettleBLEClient) -> None:
    """Header+payload in one notification, and a payload split across two, parse the same."""
    coalesced = [bytes([0xEF, 0xDD, 2, 195, 1, 0xEF, 0xDD, 0, 1])]
    split = [bytes([0xEF, 0xDD, 2]), bytes([195]), bytes([1]), bytes([0xEF, 0xDD, 0]), bytes([1])]
    expected = {"target_temp": 195, "units": "F", "power": True}
    assert client.parse_notifications(coalesced) == expected
    assert client.parse_notifications(split) == expected


def test_parse_skips_leading_payload(client: KettleBLEClient) -> None:
    assert client.parse_notifications([bytes([1])] + frame(2, [195, 1])) == {"target_temp": 195, "units": "F"}


def test_parse_header_without_payload_is_ignored(client: KettleBLEClient) -> None:
    assert client.parse_notifications([bytes([0xEF, 0xDD, 2])] + frame(0, [1])) == {"power": True}


def test_parse_ignores_unknown_types_and_short_payloads(client: KettleBLEClient) -> None:
    notifications = frame(5, [0xFF] * 4) + frame(7, [0, 0, 0]) + frame(2, [195]) + frame(4, [5]) + frame(0, [0])
    assert client.parse_notifications(notifications) == {"power": False}


def test_parse_countdown_is_16_bit_seconds(client: KettleBLEClient) -> None:
    assert client.parse_notifications(frame(4, [0x10, 0x0E])) == {"countdown": 3600}
    assert client.parse_notifications(frame(4, [0x2C, 0x01, 0x2C, 0x01])) == {"countdown": 300}


def test_parse_hold_engaged_vs_hold_button(client: KettleBLEClient) -> None:
    assert client.parse_notifications(frame(1, [1]) + frame(6, [0])) == {"hold_button": True, "hold": False}


def test_parse_current_temp_no_reading_sentinel(client: KettleBLEClient) -> None:
    assert client.parse_notifications(frame(3, [0x20, 1])) == {"current_temp": None, "units": "F"}
    assert client.parse_notifications(frame(3, [0x20, 0])) == {"current_temp": None, "units": "C"}


def test_parse_ignores_init_echo_on_position_type(client: KettleBLEClient) -> None:
    echo = frame(8, list(range(11)))
    assert client.parse_notifications(echo) == {}
    assert client.parse_notifications(echo + frame(8, [0, 1, 0])) == {"lifted": True}


@pytest.fixture
def bleak(client: KettleBLEClient):
    """Connected fake BleakClient whose start_notify replays queued frames."""
    mock = MagicMock(name="BleakClient")
    mock.is_connected = True
    mock.write_gatt_char = AsyncMock()
    mock.stop_notify = AsyncMock()
    mock.disconnect = AsyncMock()
    mock.frames: list[bytes] = []

    async def start_notify(_uuid, handler):
        for data in mock.frames:
            handler(None, bytearray(data))

    mock.start_notify = AsyncMock(side_effect=start_notify)
    with (
        patch.object(kettle_ble, "establish_connection", AsyncMock(return_value=mock)),
        patch.object(kettle_ble, "NOTIFY_WINDOW", 0),
        patch.object(kettle_ble, "NOTIFY_TIMEOUT", 0.05),
    ):
        yield mock


async def test_poll_authenticates_and_returns_state(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.frames = frame(0, [1]) + frame(2, [195, 1]) + frame(3, [150, 1])
    state = await client.async_poll(MagicMock())
    assert state == {"power": True, "target_temp": 195, "current_temp": 150, "units": "F"}
    bleak.write_gatt_char.assert_awaited_once_with(client.char_uuid, client.init_sequence)
    bleak.stop_notify.assert_awaited_once()


async def test_poll_returns_partial_state_after_timeout(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.frames = frame(0, [1]) + frame(8, [1, 1, 0])
    assert await client.async_poll(MagicMock()) == {"power": True, "lifted": False}


async def test_poll_without_frames_raises(client: KettleBLEClient, bleak: MagicMock) -> None:
    with pytest.raises(KettleError, match="No state frames"):
        await client.async_poll(MagicMock())


async def test_poll_without_device_raises(client: KettleBLEClient) -> None:
    with pytest.raises(KettleError, match="not reachable"):
        await client.async_poll(None)


async def test_poll_connection_error_resets_client(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.start_notify = AsyncMock(side_effect=OSError("Connection closed"))
    with pytest.raises(KettleError, match="Connection closed"):
        await client.async_poll(MagicMock())
    bleak.disconnect.assert_awaited_once()
    assert client._client is None


async def test_poll_keeps_frames_when_stop_notify_fails(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.frames = frame(0, [1]) + frame(2, [195, 1]) + frame(3, [150, 1])
    bleak.stop_notify = AsyncMock(side_effect=OSError("Connection closed"))
    state = await client.async_poll(MagicMock())
    assert state["target_temp"] == 195
    assert client._client is None


async def test_poll_stops_early_once_required_frames_arrive(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.frames = frame(0, [1]) + frame(2, [195, 1]) + frame(3, [150, 1])
    with patch.object(kettle_ble, "NOTIFY_TIMEOUT", 30):
        await asyncio.wait_for(client.async_poll(MagicMock()), 1)


async def test_set_power_and_temperature_commands(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_set_power(MagicMock(), True)
    await client.async_set_temperature(MagicMock(), 195, fahrenheit=True)
    writes = [call.args[1] for call in bleak.write_gatt_char.await_args_list]
    assert writes[0] == client.init_sequence
    assert writes[1] == bytes([0xEF, 0xDD, 0x0A, 0, 0, 1, 1, 0])
    assert writes[2] == bytes([0xEF, 0xDD, 0x0A, 1, 1, 195, 196, 1])


@pytest.mark.parametrize(
    ("temp", "fahrenheit", "expected"),
    [(300, True, 212), (50, True, 104), (150, False, 100), (10, False, 40)],
)
async def test_set_temperature_clamps(
    client: KettleBLEClient, bleak: MagicMock, temp: int, fahrenheit: bool, expected: int
) -> None:
    await client.async_set_temperature(MagicMock(), temp, fahrenheit=fahrenheit)
    assert bleak.write_gatt_char.await_args.args[1][5] == expected


async def test_command_error_raises_kettle_error(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.write_gatt_char = AsyncMock(side_effect=[None, OSError("boom")])
    with pytest.raises(KettleError, match="boom"):
        await client.async_set_power(MagicMock(), False)
    assert client._client is None
