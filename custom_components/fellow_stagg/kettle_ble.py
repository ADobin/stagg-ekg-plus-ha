"""BLE client for the Fellow Stagg EKG+ kettle.

The kettle streams its state (~1 frame/s per field) over a single notify
characteristic once the init sequence has been written. The client keeps
the connection open and reports state changes through a callback.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import time
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

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

# Time to wait for the first full state after connecting
INITIAL_STATE_TIMEOUT = 5.0  # seconds
# Minimum spacing between writes
WRITE_DEBOUNCE = 0.2  # seconds

FRAME_MAGIC = b"\xef\xdd"
# The state is complete once these keys have been received; others are best-effort.
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
# Payload lengths at which a frame not yet followed by another magic can be decoded
EXPECTED_PAYLOAD_LENGTHS: dict[int, tuple[int, ...]] = {
    FRAME_POWER: (1,),
    FRAME_HOLD_BUTTON: (1,),
    FRAME_TARGET_TEMP: (2,),
    FRAME_CURRENT_TEMP: (2,),
    FRAME_COUNTDOWN: (2, 4),
    FRAME_HOLD: (1,),
    FRAME_LIFTED: (3,),
}
# Bound on buffered bytes without a decodable frame
MAX_BUFFER = 256

StateCallback = Callable[[dict[str, Any]], None]


class KettleError(Exception):
    """Communication with the kettle failed."""


def split_frames(data: bytes | bytearray) -> list[tuple[int, bytes]]:
    """Split magic-delimited data into (type, payload) frames; the last frame may be partial."""
    return [(chunk[0], bytes(chunk[1:])) for chunk in bytes(data).split(FRAME_MAGIC)[1:] if chunk]


def parse_frame(msg_type: int, payload: bytes) -> dict[str, Any]:
    """Decode one frame into state keys; empty for unknown or incomplete frames.

    0x00 power [on]
    0x01 hold button [on]                  slider position, pulses at setpoint
    0x02 target temperature [temp, unit]   unit 1 = F, else C
    0x03 current temperature [temp, unit]  temp 0x20 = no reading
    0x04 auto-off countdown [lo, hi]       seconds
    0x06 hold engaged [on]
    0x08 position [on_base, ...]           3 bytes; 0 = lifted
    """
    if not payload:
        return {}
    if msg_type == FRAME_POWER:
        return {"power": payload[0] == 1}
    if msg_type == FRAME_HOLD_BUTTON:
        return {"hold_button": payload[0] == 1}
    if msg_type == FRAME_TARGET_TEMP and len(payload) >= 2:
        return {"target_temp": payload[0], "units": "F" if payload[1] == 1 else "C"}
    if msg_type == FRAME_CURRENT_TEMP and len(payload) >= 2:
        temp = None if payload[0] == CURRENT_TEMP_NO_READING else payload[0]
        return {"current_temp": temp, "units": "F" if payload[1] == 1 else "C"}
    if msg_type == FRAME_COUNTDOWN and len(payload) >= 2:
        return {"countdown": payload[0] | payload[1] << 8}
    if msg_type == FRAME_HOLD:
        return {"hold": payload[0] == 1}
    if msg_type == FRAME_LIFTED and len(payload) <= LIFTED_MAX_PAYLOAD:
        return {"lifted": payload[0] == 0}
    return {}


def parse_notifications(notifications: list[bytes]) -> dict[str, Any]:
    """Decode a batch of notifications into a state dict."""
    state: dict[str, Any] = {}
    for msg_type, payload in split_frames(b"".join(notifications)):
        state.update(parse_frame(msg_type, payload))
    return state


class KettleBLEClient:
    """Holds the connection to the kettle and decodes its state stream."""

    def __init__(
        self,
        address: str,
        on_update: StateCallback | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.address = address
        self.on_update = on_update
        self.on_disconnect = on_disconnect
        self.state: dict[str, Any] = {}
        self.last_frame_at = 0.0  # monotonic
        self._client: BleakClient | None = None
        self._characteristic: Any = None
        self._write_response = True
        self._buffer = bytearray()
        self._received: set[str] = set()  # keys seen on the current connection
        self._complete = asyncio.Event()
        self._sequence = 0  # Command sequence number
        self._last_write_at = 0.0  # monotonic
        self._lock = asyncio.Lock()
        self._expect_disconnect = False

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def async_connect(
        self, ble_device: BLEDevice | None, ble_device_callback: Callable[[], BLEDevice | None] | None = None
    ) -> None:
        """Connect, subscribe to state frames and authenticate."""
        if ble_device is None:
            raise KettleError("Kettle not reachable: no Bluetooth advertisement seen")
        async with self._lock:
            if self.connected:
                return
            self._buffer.clear()
            self._received.clear()
            self._complete.clear()
            self._expect_disconnect = False
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.address,
                    disconnected_callback=self._on_disconnected,
                    ble_device_callback=ble_device_callback,
                    max_attempts=3,
                )
                self._client = client
                characteristic = client.services.get_characteristic(CHAR_UUID)
                if characteristic is None:
                    raise KettleError(f"Characteristic {CHAR_UUID} not found; not a Stagg EKG+?")
                self._characteristic = characteristic
                self._write_response = "write" in characteristic.properties
                _LOGGER.debug("Kettle %s characteristic properties: %s", self.address, characteristic.properties)
                await client.start_notify(characteristic, self._on_notify)
                await self._write(client, INIT_SEQUENCE)
            except Exception as err:
                await self._reset_connection()
                raise KettleError(f"Connect failed: {err}") from err
            self.last_frame_at = time.monotonic()
            _LOGGER.debug("Connected to kettle %s", self.address)

    async def async_wait_for_state(self, timeout: float = INITIAL_STATE_TIMEOUT) -> bool:
        """Wait until the required state keys have been received on this connection."""
        if REQUIRED_STATE_KEYS.issubset(self._received):
            return True
        try:
            await asyncio.wait_for(self._complete.wait(), timeout)
        except TimeoutError:
            _LOGGER.debug("Incomplete state from %s after %ss: %s", self.address, timeout, self.state)
            return False
        return True

    async def async_disconnect(self) -> None:
        """Disconnect without reporting it as a connection loss."""
        async with self._lock:
            self._expect_disconnect = True
            await self._reset_connection()

    async def async_set_power(self, power_on: bool) -> None:
        """Turn the kettle on or off."""
        await self._async_write_command(self._create_command(0, 1 if power_on else 0))

    async def async_set_temperature(self, temp: int, fahrenheit: bool = True) -> None:
        """Set the target temperature, clamped to the kettle's range for the unit."""
        low, high = (MIN_TEMP_F, MAX_TEMP_F) if fahrenheit else (MIN_TEMP_C, MAX_TEMP_C)
        await self._async_write_command(self._create_command(1, max(low, min(high, temp))))

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._buffer += data
        frames = split_frames(self._buffer)
        # The last frame is complete only if its payload has a length this type is known to use
        if frames and len(frames[-1][1]) not in EXPECTED_PAYLOAD_LENGTHS.get(frames[-1][0], ()):
            frames.pop()
        delta: dict[str, Any] = {}
        for msg_type, payload in frames:
            parsed = parse_frame(msg_type, payload)
            if "units" in parsed and parsed["units"] != self.state.get("units"):
                # Temperatures are in the old unit until re-read
                for key in ("target_temp", "current_temp"):
                    self.state.pop(key, None)
            for key, value in parsed.items():
                self._received.add(key)
                if key not in self.state or self.state[key] != value:
                    delta[key] = value
                    self.state[key] = value
        self._trim_buffer()
        self.last_frame_at = time.monotonic()
        if REQUIRED_STATE_KEYS.issubset(self._received):
            self._complete.set()
        if delta and self.on_update is not None:
            self.on_update(delta)

    def _trim_buffer(self) -> None:
        """Keep only the trailing, possibly partial, frame and bound its size."""
        last = self._buffer.rfind(FRAME_MAGIC)
        if last > 0:
            del self._buffer[:last]
        if len(self._buffer) > MAX_BUFFER:
            newer = self._buffer.rfind(FRAME_MAGIC, 1)
            if newer > 0:
                del self._buffer[:newer]
            else:
                self._buffer.clear()

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        self._client = None
        _LOGGER.debug("Kettle %s disconnected", self.address)
        self._report_connection_lost()

    def _report_connection_lost(self) -> None:
        if not self._expect_disconnect and self.on_disconnect is not None:
            self.on_disconnect()

    async def _reset_connection(self) -> None:
        """Drop the connection so the next call reconnects."""
        client, self._client = self._client, None
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Error disconnecting from %s: %s", self.address, err)

    async def _write(self, client: BleakClient, data: bytes) -> None:
        """Write with at least WRITE_DEBOUNCE between writes."""
        wait = self._last_write_at + WRITE_DEBOUNCE - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await client.write_gatt_char(self._characteristic, data, response=self._write_response)
        self._last_write_at = time.monotonic()

    async def _async_write_command(self, command: bytes) -> None:
        async with self._lock:
            if self._client is None or not self._client.is_connected:
                raise KettleError("Not connected to the kettle")
            try:
                await self._write(self._client, command)
            except Exception as err:
                await self._reset_connection()
                self._report_connection_lost()
                raise KettleError(f"Command failed: {err}") from err

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
