"""Data update coordinator for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from bleak.backends.device import BLEDevice
from bluetooth_data_tools import monotonic_time_coarse
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
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DISCONNECT_GRACE, DOMAIN, FRAME_TIMEOUT, LINK_CHECK_INTERVAL, RECONNECT_BACKOFF
from .kettle_ble import MAX_TEMP_C, MAX_TEMP_F, MIN_TEMP_C, MIN_TEMP_F, KettleBLEClient, KettleError

_LOGGER = logging.getLogger(__name__)

# Keys reported in the kettle's current unit; dropped when the unit changes
TEMPERATURE_KEYS = ("target_temp", "current_temp")

type FellowStaggConfigEntry = ConfigEntry[FellowStaggDataUpdateCoordinator]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
  """Keeps a connection to the kettle and publishes its pushed state."""

  def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      config_entry=entry,
      name=f"Fellow Stagg {address}",
      update_interval=None,
    )
    self.kettle = KettleBLEClient(address, on_update=self._on_update, on_disconnect=self._on_disconnect)
    self.address = address
    self.disconnects = 0
    self._state: dict[str, Any] = {}
    self._last_service_info: BluetoothServiceInfoBleak | None = None  # for idle-kettle directed connect
    self._connect_lock = asyncio.Lock()
    self._reconnect_task: asyncio.Task[None] | None = None
    self._unavailable_timer: CALLBACK_TYPE | None = None
    self._unsubscribe: list[CALLBACK_TYPE] = []

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, address)},
      connections={(dr.CONNECTION_BLUETOOTH, address)},
      name=f"Fellow Stagg EKG+ {address}",
      manufacturer="Fellow",
      model="Stagg EKG+",
    )

  @property
  def temperature_unit(self) -> str:
    """Unit reported by the kettle; Home Assistant's unit system until known."""
    units = (self.data or {}).get("units")
    if units == "F":
      return UnitOfTemperature.FAHRENHEIT
    if units == "C":
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

  @property
  def reconnecting(self) -> bool:
    return self._reconnect_task is not None and not self._reconnect_task.done()

  # ---- setup / teardown

  async def _async_setup(self) -> None:
    """Connect and wait for a full state; HA retries setup if the kettle is unreachable."""
    await self._async_connect()
    if not await self.kettle.async_wait_for_state():
      await self.kettle.async_disconnect()
      raise UpdateFailed(f"Incomplete state from kettle {self.address}: {self.kettle.state}")
    self._unsubscribe.append(
      async_register_callback(
        self.hass,
        self._on_advertisement,
        BluetoothCallbackMatcher(address=self.address, connectable=True),
        BluetoothScanningMode.PASSIVE,
      )
    )
    self._unsubscribe.append(
      async_track_time_interval(self.hass, self._async_check_link, timedelta(seconds=LINK_CHECK_INTERVAL))
    )
    # Entries are not unloaded on shutdown; release the kettle's single connection slot anyway
    self._unsubscribe.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_on_hass_stop))

  async def _async_update_data(self) -> dict[str, Any]:
    """Return the accumulated state (first refresh and manual refreshes only)."""
    if not self.kettle.connected:
      await self._async_connect()
    return dict(self._state)

  async def async_shutdown(self) -> None:
    """Stop reconnecting and drop the BLE connection."""
    await super().async_shutdown()
    for unsubscribe in self._unsubscribe:
      unsubscribe()
    self._unsubscribe.clear()
    self._cancel_unavailable_timer()
    if self._reconnect_task is not None:
      self._reconnect_task.cancel()
      self._reconnect_task = None
    await self.kettle.async_disconnect()

  async def _async_on_hass_stop(self, _event: Event) -> None:
    await self.async_shutdown()

  # ---- connection management

  async def _async_connect(self, ble_device: BLEDevice | None = None) -> None:
    """Connect using the live advertisement or the cached device."""
    async with self._connect_lock:
      if self.kettle.connected:
        return
      await self.kettle.async_connect(
        ble_device or self.get_ble_device_for_connect(), ble_device_callback=self.get_ble_device_for_connect
      )
      if (fresh_info := async_last_service_info(self.hass, self.address, True)) is not None:
        self._last_service_info = fresh_info

  @callback
  def _on_update(self, delta: dict[str, Any]) -> None:
    """Merge pushed state changes and notify entities."""
    if "units" in delta and delta["units"] != self._state.get("units"):
      for key in TEMPERATURE_KEYS:
        self._state.pop(key, None)
    self._state.update(delta)
    if self.data is None:
      return  # still setting up; _async_update_data publishes the first state
    _LOGGER.debug("Kettle %s update: %s", self.address, delta)
    self._cancel_unavailable_timer()
    self.async_set_updated_data(dict(self._state))

  @callback
  def _on_disconnect(self) -> None:
    """Connection lost: keep the last state briefly, then reconnect."""
    self.disconnects += 1
    _LOGGER.debug("Kettle %s connection lost; reconnecting", self.address)
    if self._unavailable_timer is None:
      self._unavailable_timer = async_call_later(self.hass, DISCONNECT_GRACE, self._mark_unavailable)
    self._start_reconnect()

  @callback
  def _mark_unavailable(self, _now: datetime) -> None:
    """No fresh state since the connection was lost (a frame or a completed reconnect cancels this)."""
    self._unavailable_timer = None
    self.async_set_update_error(UpdateFailed(f"Kettle {self.address} disconnected"))

  def _cancel_unavailable_timer(self) -> None:
    if self._unavailable_timer is not None:
      self._unavailable_timer()
      self._unavailable_timer = None

  def _start_reconnect(self) -> None:
    if not self.reconnecting:
      self._reconnect_task = self.config_entry.async_create_background_task(
        self.hass, self._async_reconnect(), f"{DOMAIN} reconnect {self.address}"
      )

  async def _async_reconnect(self) -> None:
    """Retry with backoff while a BLE device is known; otherwise wait for an advertisement.

    Recovery requires a full state from the new connection, not just a GATT connection.
    """
    attempt = 0
    while True:
      await asyncio.sleep(RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)])
      attempt += 1
      if (ble_device := self.get_ble_device_for_connect()) is None:
        _LOGGER.debug("Kettle %s not advertising; waiting for it", self.address)
        return
      try:
        await self._async_connect(ble_device)
      except KettleError as err:
        _LOGGER.debug("Reconnect attempt %d to %s failed: %s", attempt, self.address, err)
        continue
      if await self.kettle.async_wait_for_state():
        break
      _LOGGER.debug("Reconnected to kettle %s but received no state; retrying", self.address)
      await self.kettle.async_disconnect()
    self._cancel_unavailable_timer()
    if not self.last_update_success:
      self.async_set_updated_data(dict(self._state))

  @callback
  def _on_advertisement(self, service_info: BluetoothServiceInfoBleak, _change: BluetoothChange) -> None:
    """The kettle is awake: remember how to reach it and reconnect if needed."""
    self._last_service_info = service_info
    if not self.kettle.connected:
      self._start_reconnect()

  async def _async_check_link(self, _now: datetime) -> None:
    """Treat a silent connection as lost."""
    if self.kettle.connected and time.monotonic() - self.kettle.last_frame_at > FRAME_TIMEOUT:
      _LOGGER.debug("No frames from kettle %s for %ss; reconnecting", self.address, FRAME_TIMEOUT)
      await self.kettle.async_disconnect()
      self._on_disconnect()

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
    except KettleError as err:
      raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
      ) from err

  async def async_set_power(self, power_on: bool) -> None:
    """Turn the kettle on or off."""
    await self._async_command(self.kettle.async_set_power, power_on)

  async def async_set_temperature(self, temperature: float) -> None:
    """Set the target temperature in the kettle's unit."""
    await self._async_command(
      self.kettle.async_set_temperature,
      round(temperature),
      fahrenheit=self.temperature_unit == UnitOfTemperature.FAHRENHEIT,
    )
