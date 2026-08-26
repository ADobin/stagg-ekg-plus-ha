"""Config flow tests: discovery, user selection, manual entry, migration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fellow_stagg_ble import FellowStaggConnectionError, FellowStaggNotSupportedError
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fellow_stagg.const import DOMAIN

from .conftest import ADDRESS, service_info

OTHER = "11:22:33:44:55:66"


@pytest.fixture(autouse=True)
def _discovered(request):
    """Patch what HA has discovered; tests override via the `discovered` marker."""
    marker = request.node.get_closest_marker("discovered")
    infos = marker.args[0] if marker else [service_info()]
    with patch("custom_components.fellow_stagg.config_flow.async_discovered_service_info", return_value=infos):
        yield


async def test_user_selects_discovered_kettle(hass: HomeAssistant, kettle) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"FELLOW46B9 ({ADDRESS})"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    entry = result["result"]
    assert entry.unique_id == ADDRESS
    assert entry.minor_version == 2
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.discovered([service_info(OTHER, "Other", []), service_info(ADDRESS, "", [])])
async def test_user_lists_only_kettles_by_name_or_service(hass: HomeAssistant) -> None:
    """A device with neither the service UUID nor the FELLOW name is not offered."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


@pytest.mark.discovered([])
async def test_manual_entry_when_nothing_discovered(hass: HomeAssistant, kettle, ble_device: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "not-a-mac"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}

    ble_device.name = "FELLOW46B9"
    with patch("custom_components.fellow_stagg.config_flow.async_ble_device_from_address", return_value=ble_device):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: " aa:bb:cc:dd:ee:ff "}
        )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"FELLOW46B9 ({ADDRESS})"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_bluetooth_discovery_confirm(hass: HomeAssistant, kettle) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": f"FELLOW46B9 ({ADDRESS})"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS


async def test_bluetooth_confirm_probe_failure_then_success(hass: HomeAssistant, kettle) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    kettle.connect_error = FellowStaggConnectionError("busy")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not kettle.kettle.connected  # probe connection released

    kettle.connect_error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_probe_of_unsupported_device_aborts(hass: HomeAssistant, kettle) -> None:
    kettle.connect_error = FellowStaggNotSupportedError("no characteristic")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_supported"


async def test_probe_without_state_is_cannot_connect(hass: HomeAssistant, kettle) -> None:
    kettle.initial_state = None
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.discovered([])
async def test_manual_entry_requires_reachable_kettle(
    hass: HomeAssistant, kettle, ble_lookup: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    ble_lookup.return_value = None
    with patch("custom_components.fellow_stagg.config_flow.async_ble_device_from_address", return_value=None):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_found"}


async def test_discovery_of_configured_kettle_aborts(hass: HomeAssistant, setup_entry) -> None:
    await setup_entry()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_discovery_reloads_entry_waiting_for_kettle(hass: HomeAssistant, setup_entry, kettle) -> None:
    """An advertisement from a kettle whose entry is retrying setup triggers an immediate reload."""
    kettle.connect_error = FellowStaggConnectionError("not reachable")
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    kettle.connect_error = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    await hass.async_block_till_done()
    assert result["reason"] == "already_configured"
    assert entry.state is ConfigEntryState.LOADED


async def test_migrates_legacy_bluetooth_address_key(hass: HomeAssistant, kettle) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, data={"bluetooth_address": ADDRESS}, minor_version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.data == {CONF_ADDRESS: ADDRESS}
    assert entry.state is ConfigEntryState.LOADED
