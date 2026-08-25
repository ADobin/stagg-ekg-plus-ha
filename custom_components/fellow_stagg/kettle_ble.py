"""BLE client for the Fellow Stagg EKG+ kettle."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection

_LOGGER = logging.getLogger(__name__)

# Advertised local name is LOCAL_NAME_PREFIX + 4 hex digits, e.g. FELLOW46B9
LOCAL_NAME_PREFIX = "FELLOW"
# "Serial Port Service" and its characteristic
SERVICE_UUID = "00001820-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "00002a80-0000-1000-8000-00805f9b34fb"
# Authenticates the connection; the kettle then streams state frames
INIT_SEQUENCE = bytes.fromhex("efdd0b3031323334353637383930313233349a6d")

# Target temperature range accepted by the kettle
MIN_TEMP_F = 104
MAX_TEMP_F = 212
MIN_TEMP_C = 40
MAX_TEMP_C = 100

# Notification window per poll; extended up to the timeout while required frames are missing
NOTIFY_WINDOW = 2.0   # seconds
NOTIFY_TIMEOUT = 5.0  # seconds

FRAME_MAGIC = b"\xef\xdd"
# A poll is complete once these keys have been parsed; others are best-effort.
REQUIRED_STATE_KEYS = frozenset({"power", "target_temp", "current_temp"})

# State frame types (byte after the magic)
FRAME_POWER = 0x00
FRAME_HOLD_BUTTON = 0x01
FRAME_TARGET_TEMP = 0x02
FRAME_CURRENT_TEMP = 0x03
FRAME_COUNTDOWN = 0x04
FRAME_HOLD = 0x06
FRAME_LIFTED = 0x08
# Current temperature byte sent while the kettle is off or lifted (not a reading)
CURRENT_TEMP_NO_READING = 0x20
# 0x08 state frames are 3 bytes; the ~11-byte init echo shares the type and is ignored
LIFTED_MAX_PAYLOAD = 3


class KettleError(Exception):
    """Communication with the kettle failed."""


def split_frames(data: bytes) -> list[tuple[int, bytes]]:
    """Split magic-delimited data into (type, payload) frames."""
    return [(chunk[0], bytes(chunk[1:])) for chunk in data.split(FRAME_MAGIC)[1:] if chunk]


class KettleBLEClient:
    """BLE client for the Fellow Stagg EKG+ kettle."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.service_uuid = SERVICE_UUID
        self.char_uuid = CHAR_UUID
        self.init_sequence = INIT_SEQUENCE
        self._client: BleakClient | None = None
        self._sequence = 0  # Command sequence number
        self._last_command_time = 0.0  # monotonic seconds, for debouncing commands
        self._lock = asyncio.Lock()  # Serialize polls and commands on the single connection

    async def _ensure_connected(self, ble_device) -> BleakClient:
        """Return a connected, authenticated client."""
        if ble_device is None:
            raise KettleError("Kettle not reachable: no Bluetooth advertisement seen")
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("Connecting to kettle at %s", self.address)
            self._client = await establish_connection(
                BleakClient, ble_device, self.address, max_attempts=3
            )
            await self._authenticate()
        return self._client

    async def _reset_connection(self) -> None:
        """Drop the connection so the next call reconnects."""
        client, self._client = self._client, None
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Error disconnecting from %s: %s", self.address, err)

    async def _ensure_debounce(self) -> None:
        """Keep at least 200ms between writes."""
        now = time.monotonic()
        if now - self._last_command_time < 0.2:
            await asyncio.sleep(0.2)
        self._last_command_time = now

    async def _authenticate(self) -> None:
        """Send the init sequence."""
        _LOGGER.debug("Writing init sequence to characteristic %s", self.char_uuid)
        await self._ensure_debounce()
        await self._client.write_gatt_char(self.char_uuid, self.init_sequence)

    def _create_command(self, command_type: int, value: int) -> bytes:
        """Build a command frame.

        - Bytes 0-1: Magic (0xef, 0xdd)
        - Byte 2: Command flag (0x0a)
        - Byte 3: Sequence number
        - Byte 4: Command type (0=power, 1=temp)
        - Byte 5: Value
        - Byte 6: Checksum 1 (sequence + value)
        - Byte 7: Checksum 2 (command type)
        """
        command = bytes([
            0xef, 0xdd,
            0x0a,
            self._sequence,
            command_type,
            value,
            (self._sequence + value) & 0xFF,
            command_type,
        ])
        self._sequence = (self._sequence + 1) & 0xFF
        return command

    async def async_poll(self, ble_device) -> dict[str, Any]:
        """Connect, listen for state notifications and return the parsed state.

        Listens for NOTIFY_WINDOW, extending up to NOTIFY_TIMEOUT while
        REQUIRED_STATE_KEYS are missing. The result may be partial.
        Raises KettleError on connection failure or if nothing was received.
        """
        notifications: list[bytes] = []
        complete = asyncio.Event()

        def notification_handler(_sender, data: bytearray) -> None:
            notifications.append(bytes(data))
            if REQUIRED_STATE_KEYS.issubset(self.parse_notifications(notifications)):
                complete.set()

        async with self._lock:
            try:
                client = await self._ensure_connected(ble_device)
                await client.start_notify(self.char_uuid, notification_handler)
            except Exception as err:
                await self._reset_connection()
                raise KettleError(f"Poll failed: {err}") from err

            try:
                await asyncio.sleep(NOTIFY_WINDOW)
                if not complete.is_set():
                    await asyncio.wait_for(complete.wait(), NOTIFY_TIMEOUT - NOTIFY_WINDOW)
            except TimeoutError:
                _LOGGER.debug(
                    "Incomplete state from %s after %ss: %s",
                    self.address, NOTIFY_TIMEOUT, [f.hex() for f in notifications],
                )

            try:
                await client.stop_notify(self.char_uuid)
            except Exception as err:  # noqa: BLE001
                # Frames already received are still valid; reconnect on the next call.
                _LOGGER.debug("stop_notify failed for %s: %s", self.address, err)
                await self._reset_connection()

        state = self.parse_notifications(notifications)
        if not state:
            raise KettleError("No state frames received")
        return state

    async def _async_write_command(self, ble_device, command: bytes) -> None:
        async with self._lock:
            try:
                client = await self._ensure_connected(ble_device)
                await self._ensure_debounce()
                await client.write_gatt_char(self.char_uuid, command)
            except Exception as err:
                await self._reset_connection()
                raise KettleError(f"Command failed: {err}") from err

    async def async_set_power(self, ble_device, power_on: bool) -> None:
        """Turn the kettle on or off."""
        await self._async_write_command(ble_device, self._create_command(0, 1 if power_on else 0))

    async def async_set_temperature(self, ble_device, temp: int, fahrenheit: bool = True) -> None:
        """Set the target temperature, clamped to the kettle's range for the unit."""
        low, high = (MIN_TEMP_F, MAX_TEMP_F) if fahrenheit else (MIN_TEMP_C, MAX_TEMP_C)
        temp = max(low, min(high, temp))
        await self._async_write_command(ble_device, self._create_command(1, temp))

    async def disconnect(self) -> None:
        """Disconnect from the kettle."""
        async with self._lock:
            await self._reset_connection()

    def parse_notifications(self, notifications: list[bytes]) -> dict[str, Any]:
        """Parse notification data into kettle state.

        Frames are ``ef dd <type> <payload>``; header and payload may arrive as
        separate or coalesced notifications, so the data is joined and split on
        the magic. Types:
          0x00 power [on]
          0x01 hold button [on]           (slider position, pulses at setpoint)
          0x02 target temperature [temp, unit]   unit 1 = F, else C
          0x03 current temperature [temp, unit]  temp 0x20 = no reading
          0x04 auto-off countdown [lo, hi]       seconds
          0x06 hold engaged [on]
          0x08 position [on_base, ...]           3 bytes; 0 = lifted
        """
        state: dict[str, Any] = {}
        for msg_type, payload in split_frames(b"".join(notifications)):
            if not payload:
                continue
            if msg_type == FRAME_POWER:
                state["power"] = payload[0] == 1
            elif msg_type == FRAME_HOLD_BUTTON:
                state["hold_button"] = payload[0] == 1
            elif msg_type == FRAME_TARGET_TEMP and len(payload) >= 2:
                state["target_temp"] = payload[0]
                state["units"] = "F" if payload[1] == 1 else "C"
            elif msg_type == FRAME_CURRENT_TEMP and len(payload) >= 2:
                state["current_temp"] = None if payload[0] == CURRENT_TEMP_NO_READING else payload[0]
                state["units"] = "F" if payload[1] == 1 else "C"
            elif msg_type == FRAME_COUNTDOWN and len(payload) >= 2:
                state["countdown"] = payload[0] | payload[1] << 8
            elif msg_type == FRAME_HOLD:
                state["hold"] = payload[0] == 1
            elif msg_type == FRAME_LIFTED and len(payload) <= LIFTED_MAX_PAYLOAD:
                state["lifted"] = payload[0] == 0
        return state
