"""Fixtures for the Fellow Stagg integration tests."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.fellow_stagg.const import DOMAIN
from custom_components.fellow_stagg.kettle_ble import REQUIRED_STATE_KEYS, SERVICE_UUID, KettleError

ADDRESS = "AA:BB:CC:DD:EE:FF"


def frame(msg_type: int, payload: list[int]) -> list[bytes]:
    """Header/payload notification pair for one kettle message."""
    return [bytes([0xEF, 0xDD, msg_type]), bytes(payload)]


FULL_STATE_F = {
    "power": False,
    "hold": False,
    "hold_button": False,
    "target_temp": 195,
    "current_temp": 150,
    "units": "F",
    "countdown": 0,
    "lifted": False,
}
FULL_STATE_C = {**FULL_STATE_F, "target_temp": 91, "current_temp": 65, "units": "C"}


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Serialize HA states/registry entries without volatile fields."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components from the repo."""


@pytest.fixture(autouse=True)
def _mock_bluetooth(mock_bluetooth: None):
    """Let the bluetooth dependency set up without hardware or a system D-Bus."""
    with patch(
        "bleak_retry_connector.bluez.get_global_bluez_manager_with_timeout",
        AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def _us_customary_units(hass: HomeAssistant) -> None:
    """Default to °F so native kettle values read unchanged; tests set METRIC_SYSTEM where relevant."""
    hass.config.units = US_CUSTOMARY_SYSTEM


def service_info(address: str = ADDRESS, name: str = "FELLOW46B9", service_uuids: list[str] | None = None):
    """Advertisement as HA's bluetooth integration would deliver it."""
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from habluetooth import BluetoothServiceInfoBleak

    uuids = [SERVICE_UUID] if service_uuids is None else service_uuids
    return BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        BLEDevice(address, name, {}),
        AdvertisementData(name, {}, {}, uuids, None, -60, ()),
        "hci0",
        0.0,
        True,
    )


@pytest.fixture
def ble_device() -> MagicMock:
    device = MagicMock(name="BLEDevice")
    device.address = ADDRESS
    return device


@pytest.fixture
def ble_lookup(ble_device: MagicMock):
    """Patch HA bluetooth lookups; set .return_value = None to simulate no advertisement."""
    service_info = MagicMock(name="ServiceInfo", device=ble_device, source="hci0")
    with (
        patch(
            "custom_components.fellow_stagg.coordinator.async_ble_device_from_address",
            return_value=ble_device,
        ) as lookup,
        patch(
            "custom_components.fellow_stagg.coordinator.async_last_service_info",
            return_value=service_info,
        ),
        patch("custom_components.fellow_stagg.coordinator.async_scanner_by_source", return_value=None),
    ):
        yield lookup


class FakeKettle:
    """Stands in for KettleBLEClient: connects instantly and lets tests push state or drop the link."""

    def __init__(self, harness: KettleHarness, address: str, on_update, on_disconnect) -> None:
        self.harness = harness
        self.address = address
        self.on_update = on_update
        self.on_disconnect = on_disconnect
        self.state: dict[str, Any] = {}
        self.received: set[str] = set()  # keys seen on the current connection
        self.connected = False
        self.last_frame_at = 0.0
        self.connect_calls: list[Any] = []
        self.disconnect_calls = 0

    async def async_connect(self, ble_device) -> None:
        self.connect_calls.append(ble_device)
        if ble_device is None:
            raise KettleError("Kettle not reachable: no Bluetooth advertisement seen")
        if self.harness.connect_error is not None:
            raise self.harness.connect_error
        self.connected = True
        self.received.clear()
        self.last_frame_at = time.monotonic()
        if self.harness.initial_state:
            self.push(self.harness.initial_state)

    async def async_wait_for_state(self, timeout: float = 0) -> bool:
        if not REQUIRED_STATE_KEYS.issubset(self.received):
            await asyncio.sleep(0.01)  # let a failing reconnect loop yield instead of spinning
        return REQUIRED_STATE_KEYS.issubset(self.received)

    async def async_disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    async def async_set_power(self, power_on: bool) -> None:
        await self.harness.set_power(power_on)

    async def async_set_temperature(self, temp: int, fahrenheit: bool = True) -> None:
        await self.harness.set_temperature(temp, fahrenheit=fahrenheit)

    def push(self, delta: dict[str, Any]) -> None:
        """Deliver a state change as the kettle would."""
        self.state.update(delta)
        self.received.update(delta)
        self.last_frame_at = time.monotonic()
        if self.on_update is not None:
            self.on_update(dict(delta))

    def drop(self) -> None:
        """Lose the connection."""
        self.connected = False
        if self.on_disconnect is not None:
            self.on_disconnect()


class KettleHarness:
    """Configuration shared by FakeKettle instances plus handles to drive them."""

    def __init__(self) -> None:
        self.initial_state: dict[str, Any] | None = dict(FULL_STATE_F)
        self.connect_error: Exception | None = None
        self.instances: list[FakeKettle] = []
        self.set_power = AsyncMock()
        self.set_temperature = AsyncMock()
        self.advertisement_callbacks: list[Callable[..., Any]] = []

    @property
    def kettle(self) -> FakeKettle:
        return self.instances[-1]

    def advertise(self, info=None) -> None:
        """Deliver an advertisement to the coordinator's bluetooth callback."""
        for cb in self.advertisement_callbacks:
            cb(info or service_info(), None)


@pytest.fixture
def kettle(ble_lookup: MagicMock) -> KettleHarness:
    harness = KettleHarness()

    def make(address, on_update=None, on_disconnect=None):
        instance = FakeKettle(harness, address, on_update, on_disconnect)
        harness.instances.append(instance)
        return instance

    def register_callback(hass, cb, matcher, mode):
        harness.advertisement_callbacks.append(cb)
        return lambda: harness.advertisement_callbacks.remove(cb)

    with (
        patch("custom_components.fellow_stagg.coordinator.KettleBLEClient", side_effect=make),
        patch("custom_components.fellow_stagg.config_flow.KettleBLEClient", side_effect=make),
        patch("custom_components.fellow_stagg.coordinator.async_register_callback", side_effect=register_callback),
    ):
        yield harness


@pytest.fixture
def setup_entry(hass: HomeAssistant, kettle: KettleHarness) -> Callable[..., Any]:
    """Create and set up a config entry; returns the entry."""

    async def _setup(options: dict[str, Any] | None = None) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=ADDRESS,
            data={"bluetooth_address": ADDRESS},
            options=options or {},
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup
