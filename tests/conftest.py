"""Fixtures for the Fellow Stagg integration tests."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.fellow_stagg.const import DOMAIN
from custom_components.fellow_stagg.kettle_ble import SERVICE_UUID

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


@pytest.fixture(autouse=True)
def _no_settle_delay():
    with patch("custom_components.fellow_stagg.coordinator.COMMAND_SETTLE_DELAY", 0):
        yield


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


@pytest.fixture
def kettle(ble_lookup: MagicMock) -> MagicMock:
    """Mock KettleBLEClient I/O. kettle.async_poll.return_value is the polled state."""
    client = "custom_components.fellow_stagg.coordinator.KettleBLEClient"
    with (
        patch(f"{client}.async_poll", new_callable=AsyncMock) as poll,
        patch(f"{client}.async_set_power", new_callable=AsyncMock) as power,
        patch(f"{client}.async_set_temperature", new_callable=AsyncMock) as temperature,
        patch(f"{client}.disconnect", new_callable=AsyncMock) as disconnect,
    ):
        poll.return_value = dict(FULL_STATE_F)
        mock = MagicMock()
        mock.async_poll = poll
        mock.async_set_power = power
        mock.async_set_temperature = temperature
        mock.disconnect = disconnect
        yield mock


@pytest.fixture
def setup_entry(hass: HomeAssistant, kettle: MagicMock) -> Callable[..., Any]:
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
