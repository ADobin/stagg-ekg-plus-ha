"""Config flow for the Fellow Stagg EKG+ integration."""
from __future__ import annotations

import logging
import re
from typing import Any

from bleak.backends.device import BLEDevice
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN
from .kettle_ble import LOCAL_NAME_PREFIX, SERVICE_UUID, KettleBLEClient, KettleError, KettleNotSupportedError

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def is_kettle(info: BluetoothServiceInfoBleak) -> bool:
    """Whether an advertisement belongs to a Stagg EKG+."""
    return SERVICE_UUID in info.service_uuids or info.name.startswith(LOCAL_NAME_PREFIX)


def entry_title(name: str | None, address: str) -> str:
    return f"{name or 'Fellow Stagg'} ({address})"


class FellowStaggConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Fellow Stagg integration."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Handle a kettle discovered by Home Assistant."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": entry_title(discovery_info.name, discovery_info.address)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm setting up a discovered kettle."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_probe(self._discovery_info.device)
            if error == "not_supported":
                return self.async_abort(reason="not_supported")
            if error is None:
                return self._create_entry(self._discovery_info.name, self._discovery_info.address)
            errors["base"] = error
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": entry_title(self._discovery_info.name, self._discovery_info.address)},
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a discovered kettle, or fall back to a manual address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered_devices[address]
            error = await self._async_probe(info.device)
            if error == "not_supported":
                return self.async_abort(reason="not_supported")
            if error is None:
                return self._create_entry(info.name, address)
            errors["base"] = error

        current_addresses = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current_addresses and is_kettle(info):
                self._discovered_devices[info.address] = info
        if not self._discovered_devices:
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {address: entry_title(info.name, address) for address, info in self._discovered_devices.items()}
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter the kettle's Bluetooth address when it was not discovered."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
                error = "not_found" if ble_device is None else await self._async_probe(ble_device)
                if error == "not_supported":
                    return self.async_abort(reason="not_supported")
                if error is None:
                    return self._create_entry(ble_device.name, address)
                errors["base"] = error

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    async def _async_probe(self, ble_device: BLEDevice) -> str | None:
        """Connect once and wait for the kettle's state; returns an error key or None."""
        kettle = KettleBLEClient(ble_device.address)
        try:
            await kettle.async_connect(ble_device)
            if not await kettle.async_wait_for_state():
                return "cannot_connect"
        except KettleNotSupportedError:
            return "not_supported"
        except KettleError as err:
            _LOGGER.debug("Probe of %s failed: %s", ble_device.address, err)
            return "cannot_connect"
        finally:
            await kettle.async_disconnect()
        return None

    def _create_entry(self, name: str | None, address: str) -> ConfigFlowResult:
        return self.async_create_entry(title=entry_title(name, address), data={CONF_ADDRESS: address})
