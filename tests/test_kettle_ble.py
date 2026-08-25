"""Tests for the BLE client: frame parsing, connection handling and commands."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fellow_stagg import kettle_ble
from custom_components.fellow_stagg.kettle_ble import KettleBLEClient, KettleError, parse_notifications

from .conftest import ADDRESS, frame


def test_parse_full_state_fahrenheit() -> None:
    notifications = (
        frame(0, [0]) + frame(1, [0]) + frame(2, [195, 1]) + frame(3, [150, 1])
        + frame(4, [0, 0]) + frame(6, [0]) + frame(8, [1, 1, 0])
    )
    assert parse_notifications(notifications) == {
        "power": False,
        "hold_button": False,
        "target_temp": 195,
        "units": "F",
        "current_temp": 150,
        "countdown": 0,
        "hold": False,
        "lifted": False,
    }


def test_parse_celsius_and_lifted() -> None:
    state = parse_notifications(frame(2, [91, 0]) + frame(8, [0, 1, 0]) + frame(0, [1]))
    assert state == {"target_temp": 91, "units": "C", "lifted": True, "power": True}


def test_parse_empty() -> None:
    assert parse_notifications([]) == {}


def test_parse_coalesced_and_split_notifications() -> None:
    coalesced = [bytes([0xEF, 0xDD, 2, 195, 1, 0xEF, 0xDD, 0, 1])]
    split = [bytes([0xEF, 0xDD, 2]), bytes([195]), bytes([1]), bytes([0xEF, 0xDD, 0]), bytes([1])]
    expected = {"target_temp": 195, "units": "F", "power": True}
    assert parse_notifications(coalesced) == expected
    assert parse_notifications(split) == expected


def test_parse_skips_leading_payload_and_empty_header() -> None:
    assert parse_notifications([bytes([1])] + frame(2, [195, 1])) == {"target_temp": 195, "units": "F"}
    assert parse_notifications([bytes([0xEF, 0xDD, 2])] + frame(0, [1])) == {"power": True}


def test_parse_ignores_unknown_types_and_short_payloads() -> None:
    notifications = frame(5, [0xFF] * 4) + frame(7, [0, 0, 0]) + frame(2, [195]) + frame(4, [5]) + frame(0, [0])
    assert parse_notifications(notifications) == {"power": False}


def test_parse_countdown_is_16_bit_seconds() -> None:
    assert parse_notifications(frame(4, [0x10, 0x0E])) == {"countdown": 3600}
    assert parse_notifications(frame(4, [0x2C, 0x01, 0x2C, 0x01])) == {"countdown": 300}


def test_parse_hold_engaged_vs_hold_button() -> None:
    assert parse_notifications(frame(1, [1]) + frame(6, [0])) == {"hold_button": True, "hold": False}


def test_parse_current_temp_no_reading_sentinel() -> None:
    assert parse_notifications(frame(3, [0x20, 1])) == {"current_temp": None, "units": "F"}
    assert parse_notifications(frame(3, [0x20, 0])) == {"current_temp": None, "units": "C"}


def test_parse_ignores_init_echo_on_position_type() -> None:
    echo = frame(8, list(range(11)))
    assert parse_notifications(echo) == {}
    assert parse_notifications(echo + frame(8, [0, 1, 0])) == {"lifted": True}


@pytest.fixture
def bleak():
    """Fake connected BleakClient; .notify(data) delivers a notification."""
    mock = MagicMock(name="BleakClient")
    mock.is_connected = True
    mock.write_gatt_char = AsyncMock()
    mock.stop_notify = AsyncMock()
    mock.disconnect = AsyncMock()
    mock.handler = None
    mock.characteristic = MagicMock(name="Characteristic", properties=["write-without-response", "notify"])
    mock.services.get_characteristic = MagicMock(return_value=mock.characteristic)

    async def start_notify(_uuid, handler):
        mock.handler = handler

    mock.start_notify = AsyncMock(side_effect=start_notify)
    mock.notify = lambda data: mock.handler(None, bytearray(data))
    with patch.object(kettle_ble, "establish_connection", AsyncMock(return_value=mock)) as connect:
        mock.establish = connect
        yield mock


@pytest.fixture
def client() -> KettleBLEClient:
    updates: list[dict] = []
    disconnects: list[None] = []
    c = KettleBLEClient(ADDRESS, on_update=updates.append, on_disconnect=lambda: disconnects.append(None))
    c.updates = updates  # type: ignore[attr-defined]
    c.disconnects = disconnects  # type: ignore[attr-defined]
    return c


async def test_connect_subscribes_then_authenticates(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    assert client.connected
    bleak.services.get_characteristic.assert_called_once_with(kettle_ble.CHAR_UUID)
    bleak.start_notify.assert_awaited_once()
    assert bleak.start_notify.await_args.args[0] is bleak.characteristic
    # write mode follows the characteristic's advertised properties, never bleak's deprecated default
    bleak.write_gatt_char.assert_awaited_once_with(bleak.characteristic, kettle_ble.INIT_SEQUENCE, response=False)
    kwargs = bleak.establish.await_args.kwargs
    assert kwargs["disconnected_callback"] == client._on_disconnected
    await client.async_connect(MagicMock())  # idempotent while connected
    bleak.establish.assert_awaited_once()


async def test_connect_uses_write_with_response_when_supported(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.characteristic.properties = ["write", "notify"]
    await client.async_connect(MagicMock())
    assert bleak.write_gatt_char.await_args.kwargs == {"response": True}


async def test_connect_without_characteristic_raises(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.services.get_characteristic.return_value = None
    with pytest.raises(KettleError, match="not found"):
        await client.async_connect(MagicMock())
    assert not client.connected


async def test_connect_without_device_raises(client: KettleBLEClient) -> None:
    with pytest.raises(KettleError, match="not reachable"):
        await client.async_connect(None)


async def test_connect_failure_raises_and_resets(client: KettleBLEClient, bleak: MagicMock) -> None:
    bleak.start_notify = AsyncMock(side_effect=OSError("boom"))
    with pytest.raises(KettleError, match="boom"):
        await client.async_connect(MagicMock())
    assert not client.connected
    bleak.disconnect.assert_awaited_once()


async def test_notifications_emit_only_changes(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    for data in frame(0, [0]) + frame(2, [195, 1]) + frame(3, [150, 1]):
        bleak.notify(data)
    assert client.updates == [{"power": False}, {"target_temp": 195, "units": "F"}, {"current_temp": 150}]
    assert await client.async_wait_for_state(0.01)

    for data in frame(0, [0]) + frame(3, [151, 1]):
        bleak.notify(data)
    assert client.updates[3:] == [{"current_temp": 151}]
    assert client.state["current_temp"] == 151


async def test_wait_for_state_requires_frames_from_the_current_connection(
    client: KettleBLEClient, bleak: MagicMock
) -> None:
    await client.async_connect(MagicMock())
    for data in frame(0, [0]) + frame(2, [195, 1]) + frame(3, [150, 1]):
        bleak.notify(data)
    assert await client.async_wait_for_state(0.01)
    await client.async_disconnect()
    await client.async_connect(MagicMock())
    assert client.state["target_temp"] == 195  # last known state is kept...
    assert not await client.async_wait_for_state(0.01)  # ...but does not count as fresh


async def test_unit_change_reemits_unchanged_temperature_byte(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    for data in frame(3, [65, 1]) + frame(2, [80, 1]):
        bleak.notify(data)
    client.updates.clear()
    for data in frame(3, [65, 0]):
        bleak.notify(data)
    assert client.updates == [{"current_temp": 65, "units": "C"}]
    assert "target_temp" not in client.state  # stale until re-read in the new unit
    for data in frame(2, [80, 0]):
        bleak.notify(data)
    assert client.updates[-1] == {"target_temp": 80}


async def test_fragmented_init_echo_is_not_a_position_frame(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.notify(bytes([0xEF, 0xDD, 8, 0]))  # first byte of the echo arrives alone
    assert client.updates == []
    bleak.notify(bytes(range(1, 11)))  # rest of the 11-byte echo
    assert client.updates == []
    bleak.notify(bytes([0xEF, 0xDD, 8, 0, 1, 0]))  # real position frame: lifted
    assert client.updates == [{"lifted": True}]


async def test_trailing_frame_decoded_at_expected_length_only(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.notify(bytes([0xEF, 0xDD, 2, 195]))  # half a temperature payload
    assert client.updates == []
    bleak.notify(bytes([1]))
    assert client.updates == [{"target_temp": 195, "units": "F"}]


async def test_buffer_is_bounded_without_delimiter(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.notify(bytes([0xEF, 0xDD, 7]) + bytes(300))  # oversized unknown frame, never terminated
    assert len(client._buffer) <= kettle_ble.MAX_BUFFER
    bleak.notify(bytes(300))  # still no delimiter
    assert len(client._buffer) <= kettle_ble.MAX_BUFFER
    for data in frame(0, [1]):
        bleak.notify(data)
    assert client.updates == [{"power": True}]


async def test_wait_for_state_times_out(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.notify(bytes([0xEF, 0xDD, 0, 1]))
    assert not await client.async_wait_for_state(0.01)


async def test_buffer_keeps_only_trailing_frame(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.notify(bytes([0xEF, 0xDD, 2, 195, 1, 0xEF, 0xDD, 3]))
    assert bytes(client._buffer) == bytes([0xEF, 0xDD, 3])
    bleak.notify(bytes([150, 1]))
    assert client.state["current_temp"] == 150


async def test_unexpected_disconnect_reports(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.is_connected = False
    client._on_disconnected(bleak)
    assert client.disconnects == [None]
    assert not client.connected


async def test_requested_disconnect_is_silent(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    await client.async_disconnect()
    bleak.disconnect.assert_awaited_once()
    client._on_disconnected(bleak)  # bleak reports the disconnect we asked for
    assert client.disconnects == []


async def test_commands_write_frames(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    with patch.object(kettle_ble, "WRITE_DEBOUNCE", 0):
        await client.async_set_power(True)
        await client.async_set_temperature(195, fahrenheit=True)
    writes = [call.args[1] for call in bleak.write_gatt_char.await_args_list]
    assert writes[1] == bytes([0xEF, 0xDD, 0x0A, 0, 0, 1, 1, 0])
    assert writes[2] == bytes([0xEF, 0xDD, 0x0A, 1, 1, 195, 196, 1])
    assert all(call.kwargs == {"response": False} for call in bleak.write_gatt_char.await_args_list)


@pytest.mark.parametrize(
    ("temp", "fahrenheit", "expected"),
    [(300, True, 212), (50, True, 104), (150, False, 100), (10, False, 40)],
)
async def test_set_temperature_clamps(
    client: KettleBLEClient, bleak: MagicMock, temp: int, fahrenheit: bool, expected: int
) -> None:
    await client.async_connect(MagicMock())
    with patch.object(kettle_ble, "WRITE_DEBOUNCE", 0):
        await client.async_set_temperature(temp, fahrenheit=fahrenheit)
    assert bleak.write_gatt_char.await_args.args[1][5] == expected


async def test_command_without_connection_raises(client: KettleBLEClient) -> None:
    with pytest.raises(KettleError, match="Not connected"):
        await client.async_set_power(True)


async def test_command_error_resets_connection_and_reports_loss(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    bleak.write_gatt_char = AsyncMock(side_effect=OSError("boom"))
    with patch.object(kettle_ble, "WRITE_DEBOUNCE", 0), pytest.raises(KettleError, match="boom"):
        await client.async_set_power(False)
    assert not client.connected
    assert client.disconnects == [None]
    client._on_disconnected(bleak)  # bleak's own callback for the same loss is not double-counted
    assert client.disconnects == [None]


async def test_write_debounce_spaces_writes(client: KettleBLEClient, bleak: MagicMock) -> None:
    await client.async_connect(MagicMock())
    with patch.object(kettle_ble, "WRITE_DEBOUNCE", 0.05):
        loop = asyncio.get_running_loop()
        start = loop.time()
        await client.async_set_power(True)
        assert loop.time() - start >= 0.04
