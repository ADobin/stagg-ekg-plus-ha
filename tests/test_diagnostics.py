"""Diagnostics and entity snapshot tests."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import snapshot_platform
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from .conftest import ADDRESS


async def test_diagnostics_redacts_identifiers(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, setup_entry, snapshot: SnapshotAssertion
) -> None:
    entry = await setup_entry()
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert ADDRESS not in str(result)
    assert result["coordinator"]["data"]["target_temp"] == 195
    assert result["entry"]["data"] == {"address": "**REDACTED**"}  # migrated from bluetooth_address
    assert result["coordinator"] == snapshot


@pytest.mark.parametrize(
    "platform", [Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH, Platform.WATER_HEATER]
)
async def test_entities_snapshot(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    setup_entry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    """Registry entries and states per platform; guards unique_id, name and unit drift."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    with patch("custom_components.fellow_stagg.PLATFORMS", [platform]):
        entry = await setup_entry()
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            if entity.disabled:
                entity_registry.async_update_entity(entity.entity_id, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)
