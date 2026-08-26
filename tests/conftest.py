"""Fixtures for the Fellow Stagg integration tests."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
import dataclasses
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fellow_stagg_ble import (
    SERVICE_UUID,
    FellowStaggTimeoutError,
    KettleState,
    TemperatureUnit,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.fellow_stagg.const import DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"


def frame(msg_type: int, payload: list[int]) -> list[bytes]:
    """Header/payload notification pair for one kettle message."""
    return [bytes([0xEF, 0xDD, msg_type]), bytes(payload)]


FULL_STATE_F = KettleState(
    power=False,
    hold=False,
    hold_button=False,
    target_temperature=195,
    current_temperature=150,
    unit=TemperatureUnit.FAHRENHEIT,
    countdown=0,
    on_base=True,
)
FULL_STATE_C = dataclasses.replace(
    FULL_STATE_F, target_temperature=91, current_temperature=65, unit=TemperatureUnit.CELSIUS
)


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
            "custom_components.fellow_stagg.async_ble_device_from_address",
            new=lookup,
        ),
        patch(
            "custom_components.fellow_stagg.coordinator.async_last_service_info",
            return_value=service_info,
        ),
        patch("custom_components.fellow_stagg.coordinator.async_scanner_by_source", return_value=None),
    ):
        yield lookup


class FakeKettle:
    """Stands in for FellowStaggKettle: connects instantly and lets tests push state or drop the link."""

    def __init__(self, harness: KettleHarness, ble_device, *, state_callback=None, disconnected_callback=None) -> None:
        self.harness = harness
        self.ble_device = ble_device
        self.state_callback = state_callback
        self.disconnected_callback = disconnected_callback
        self.state = KettleState()
        self.connected = False
        self.last_frame_at: float | None = None
        self.connect_calls: list[Any] = []
        self.disconnect_calls = 0

    @property
    def address(self) -> str:
        return self.ble_device.address

    def set_ble_device(self, ble_device) -> None:
        self.ble_device = ble_device

    async def connect(self, *, state_timeout: float | None = 5.0) -> None:
        self.connect_calls.append(self.ble_device)
        if self.harness.connect_error is not None:
            raise self.harness.connect_error
        self.connected = True
        self.last_frame_at = time.monotonic()
        if self.harness.initial_state is None or not self.harness.initial_state.complete:
            # A connection that never delivers a full state is torn down by the library
            if self.harness.initial_state is not None:
                self.push(self.harness.initial_state)
            await asyncio.sleep(0.01)
            self.connected = False
            raise FellowStaggTimeoutError("No complete state")
        self.push(self.harness.initial_state)

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    async def set_power(self, on: bool) -> None:
        await self.harness.set_power(on)

    async def set_target_temperature(self, value: int, unit: TemperatureUnit) -> None:
        await self.harness.set_temperature(value, unit)

    def push(self, changes: KettleState | dict[str, Any]) -> None:
        """Deliver a state change as the kettle would."""
        if isinstance(changes, KettleState):
            self.state = changes
        else:
            self.state = dataclasses.replace(self.state, **changes)
        self.last_frame_at = time.monotonic()
        if self.state_callback is not None:
            self.state_callback(self.state)

    def drop(self) -> None:
        """Lose the connection."""
        self.connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback()


class KettleHarness:
    """Configuration shared by FakeKettle instances plus handles to drive them."""

    def __init__(self) -> None:
        self.initial_state: KettleState | None = FULL_STATE_F
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

    def make(ble_device, *, state_callback=None, disconnected_callback=None):
        instance = FakeKettle(
            harness, ble_device, state_callback=state_callback, disconnected_callback=disconnected_callback
        )
        harness.instances.append(instance)
        return instance

    def register_callback(hass, cb, matcher, mode):
        harness.advertisement_callbacks.append(cb)
        return lambda: harness.advertisement_callbacks.remove(cb)

    with (
        patch("custom_components.fellow_stagg.coordinator.FellowStaggKettle", side_effect=make),
        patch("custom_components.fellow_stagg.config_flow.FellowStaggKettle", side_effect=make),
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
