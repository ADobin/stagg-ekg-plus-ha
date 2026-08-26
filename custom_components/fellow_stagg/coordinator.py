"""Data update coordinator for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bluetooth_data_tools import monotonic_time_coarse
from fellow_stagg_ble import (
  MAX_TEMP_C,
  MAX_TEMP_F,
  MIN_TEMP_C,
  MIN_TEMP_F,
  FellowStaggError,
  FellowStaggKettle,
  KettleState,
  TemperatureUnit,
)
from homeassistant.components.bluetooth import (
  BluetoothCallbackMatcher,
  BluetoothChange,
  BluetoothScanningMode,
  BluetoothServiceInfoBleak,
  async_ble_device_from_address,
  async_last_service_info,
  async_register_callback,
  async_scanner_by_source,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, FRAME_TIMEOUT, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type FellowStaggConfigEntry = ConfigEntry[FellowStaggDataUpdateCoordinator]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[KettleState]):
  """Keeps a connection to the kettle and publishes its pushed state.

  Pushed state resets the refresh timer; a refresh therefore only runs after
  UPDATE_INTERVAL of silence and reconnects if the link is dead.
  """

  def __init__(self, hass: HomeAssistant, entry: ConfigEntry, ble_device: BLEDevice) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      config_entry=entry,
      name=f"Fellow Stagg {ble_device.address}",
      update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )
    self.kettle = FellowStaggKettle(
      ble_device, state_callback=self._on_state, disconnected_callback=self._on_disconnect
    )
    self.address = ble_device.address
    self.disconnects = 0
    self._last_service_info: BluetoothServiceInfoBleak | None = None  # for idle-kettle directed connect
    self._connect_lock = asyncio.Lock()

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, self.address)},
      connections={(dr.CONNECTION_BLUETOOTH, self.address)},
      name=f"Fellow Stagg EKG+ {self.address}",
      manufacturer="Fellow",
      model="Stagg EKG+",
    )

  @property
  def temperature_unit(self) -> str:
    """Unit reported by the kettle; Home Assistant's unit system until known."""
    unit = self.data.unit if self.data is not None else None
    if unit is TemperatureUnit.FAHRENHEIT:
      return UnitOfTemperature.FAHRENHEIT
    if unit is TemperatureUnit.CELSIUS:
      return UnitOfTemperature.CELSIUS
    return self.hass.config.units.temperature_unit

  @property
  def min_temp(self) -> float:
    """Get the minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Get the maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  # ---- setup / teardown

  async def _async_setup(self) -> None:
    """Reconnect when the kettle advertises; release the connection on HA stop."""
    self.config_entry.async_on_unload(
      async_register_callback(
        self.hass,
        self._on_advertisement,
        BluetoothCallbackMatcher(address=self.address, connectable=True),
        BluetoothScanningMode.PASSIVE,
      )
    )
    # Entries are not unloaded on shutdown; the kettle has a single connection slot
    self.config_entry.async_on_unload(
      self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_on_hass_stop)
    )

  async def _async_update_data(self) -> KettleState:
    """Verify the link or (re)connect; connect() returns once a full state has arrived."""
    if self.kettle.connected:
      last = self.kettle.last_frame_at
      if last is not None and time.monotonic() - last < FRAME_TIMEOUT:
        return self.kettle.state
      _LOGGER.debug("No frames from kettle %s for %ss; reconnecting", self.address, FRAME_TIMEOUT)
      await self.kettle.disconnect()
    try:
      await self._async_connect()
    except FellowStaggError as err:
      raise UpdateFailed(f"Cannot connect to kettle {self.address}: {err}") from err
    return self.kettle.state

  async def async_shutdown(self) -> None:
    """Stop refreshing and drop the BLE connection."""
    await super().async_shutdown()
    await self.kettle.disconnect()

  async def _async_on_hass_stop(self, _event: Event) -> None:
    await self.async_shutdown()

  # ---- connection management

  async def _async_connect(self) -> None:
    """Connect using the live advertisement or the cached device."""
    async with self._connect_lock:
      if self.kettle.connected:
        return
      if (ble_device := self.get_ble_device_for_connect()) is None:
        raise FellowStaggError("Kettle not reachable: no Bluetooth advertisement seen")
      self.kettle.set_ble_device(ble_device)
      await self.kettle.connect()
      if (fresh_info := async_last_service_info(self.hass, self.address, True)) is not None:
        self._last_service_info = fresh_info

  @callback
  def _on_state(self, state: KettleState) -> None:
    """Publish a pushed state change."""
    if self.data is None:
      return  # first refresh publishes the initial state
    _LOGGER.debug("Kettle %s update: %s", self.address, state)
    self.async_set_updated_data(state)

  @callback
  def _on_disconnect(self) -> None:
    """Connection lost: entities go unavailable; reconnect now and on every tick."""
    self.disconnects += 1
    _LOGGER.debug("Kettle %s connection lost", self.address)
    self.async_update_listeners()
    self.hass.async_create_task(self.async_request_refresh())

  @callback
  def _on_advertisement(self, service_info: BluetoothServiceInfoBleak, _change: BluetoothChange) -> None:
    """The kettle is awake: remember how to reach it and reconnect if needed."""
    self._last_service_info = service_info
    if not self.kettle.connected:
      self.hass.async_create_task(self.async_request_refresh())

  def get_ble_device_for_connect(self) -> BLEDevice | None:
    """Live BLEDevice if advertising, else the cached device, else None."""
    if (ble_device := async_ble_device_from_address(self.hass, self.address, True)) is not None:
      return ble_device
    if self._last_service_info is None:
      return None
    self._inject_cached_ble_device(self._last_service_info)
    return self._last_service_info.device

  def _inject_cached_ble_device(self, service_info: BluetoothServiceInfoBleak) -> None:
    """Re-insert the last advertisement into the scanner cache so a directed connect can be routed.

    The kettle stops advertising a few minutes after use but accepts connections;
    HA only routes connections to devices present in a scanner cache. This relies on
    habluetooth's private cache and is best-effort: failures are logged and ignored.
    """
    try:
      scanner = async_scanner_by_source(self.hass, service_info.source)
      if scanner is None:
        return
      refreshed = BluetoothServiceInfoBleak.from_device_and_advertisement_data(
        service_info.device,
        service_info.advertisement,
        service_info.source,
        monotonic_time_coarse(),
        service_info.connectable,
      )
      scanner._previous_service_info[self.address] = refreshed  # noqa: SLF001
    except (AttributeError, TypeError) as err:
      _LOGGER.debug("Cannot inject cached device for %s: %s", self.address, err)

  # ---- commands

  async def _async_command(self, command: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any) -> None:
    """Send a command; the kettle pushes the resulting state."""
    try:
      if not self.kettle.connected:
        await self._async_connect()
      await command(*args, **kwargs)
    except FellowStaggError as err:
      raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
      ) from err

  async def async_set_power(self, power_on: bool) -> None:
    """Turn the kettle on or off."""
    await self._async_command(self.kettle.set_power, power_on)

  async def async_set_temperature(self, temperature: float) -> None:
    """Set the target temperature in the kettle's unit."""
    unit = (
      TemperatureUnit.FAHRENHEIT
      if self.temperature_unit == UnitOfTemperature.FAHRENHEIT
      else TemperatureUnit.CELSIUS
    )
    try:
      await self._async_command(self.kettle.set_target_temperature, round(temperature), unit)
    except ValueError as err:
      raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="temperature_out_of_range",
        translation_placeholders={"min": str(self.min_temp), "max": str(self.max_temp), "unit": self.temperature_unit},
      ) from err
