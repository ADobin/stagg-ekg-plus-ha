"""Data update coordinator for Fellow Stagg EKG+ kettles."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import (
  BluetoothServiceInfoBleak,
  async_ble_device_from_address,
  async_last_service_info,
  async_scanner_by_source,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
  DataUpdateCoordinator,
  UpdateFailed,
)

from .const import (
  COMMAND_SETTLE_DELAY,
  CONF_TEMPERATURE_UNIT,
  DOMAIN,
  MAX_FAILED_POLLS,
  MAX_TEMP_C,
  MAX_TEMP_F,
  MIN_TEMP_C,
  MIN_TEMP_F,
  UNIT_AUTO,
  UNIT_CELSIUS,
  UNIT_FAHRENHEIT,
)
from .kettle_ble import KettleBLEClient, KettleError

_LOGGER = logging.getLogger(__name__)

# Keys reported in the kettle's current unit; dropped when the unit changes
TEMPERATURE_KEYS = ("target_temp", "current_temp")

type FellowStaggConfigEntry = ConfigEntry[FellowStaggDataUpdateCoordinator]


class FellowStaggDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
  """Class to manage fetching Fellow Stagg data."""

  def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, polling_interval: timedelta) -> None:
    """Initialize the coordinator."""
    super().__init__(
      hass,
      _LOGGER,
      config_entry=entry,
      name=f"Fellow Stagg {address}",
      update_interval=polling_interval,
    )
    self.kettle = KettleBLEClient(address)
    self.address = address
    self._last_service_info: BluetoothServiceInfoBleak | None = None  # for idle-kettle directed connect
    self._failed_polls = 0

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, address)},
      connections={(dr.CONNECTION_BLUETOOTH, address)},
      name=f"Fellow Stagg EKG+ {address}",
      manufacturer="Fellow",
      model="Stagg EKG+",
    )

  @property
  def fallback_temperature_unit(self) -> str:
    """Unit to assume until the kettle reports one: the configured option, else HA's unit system."""
    option = self.config_entry.options.get(CONF_TEMPERATURE_UNIT, UNIT_AUTO)
    if option == UNIT_FAHRENHEIT:
      return UnitOfTemperature.FAHRENHEIT
    if option == UNIT_CELSIUS:
      return UnitOfTemperature.CELSIUS
    return self.hass.config.units.temperature_unit

  @property
  def temperature_unit(self) -> str:
    """Unit reported by the kettle, or the fallback while unknown."""
    units = (self.data or {}).get("units")
    if units == "F":
      return UnitOfTemperature.FAHRENHEIT
    if units == "C":
      return UnitOfTemperature.CELSIUS
    return self.fallback_temperature_unit

  @property
  def min_temp(self) -> float:
    """Get the minimum temperature based on current units."""
    return MIN_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MIN_TEMP_C

  @property
  def max_temp(self) -> float:
    """Get the maximum temperature based on current units."""
    return MAX_TEMP_F if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else MAX_TEMP_C

  def _inject_cached_ble_device(self) -> None:
    """Re-insert the last known service info into the BLE scanner cache.

    The kettle stops advertising after ~3 min idle but accepts directed connections.
    HA's BLE routing requires a live scanner cache entry to route through the proxy.
    Injecting the cached entry with a current timestamp unblocks routing; the proxy
    then initiates the directed BLE connection by address.
    """
    service_info = self._last_service_info
    if service_info is None:
      return
    try:
      from bluetooth_data_tools import monotonic_time_coarse
      service_info.time = monotonic_time_coarse()
    except Exception:
      pass
    scanner = async_scanner_by_source(self.hass, service_info.source)
    if scanner is not None:
      scanner._previous_service_info[self.address] = service_info
      _LOGGER.debug(
        "Injected cached BLE device for %s via scanner %s for directed connect",
        self.address, service_info.source,
      )

  def get_ble_device_for_connect(self) -> BLEDevice | None:
    """Return the best available BLEDevice, injecting cached state if needed.

    Returns the live device if present, the cached device after cache injection
    if available, or None if no prior advertisement has ever been seen.
    """
    ble_device = async_ble_device_from_address(self.hass, self.address, True)
    if ble_device is not None:
      return ble_device
    if self._last_service_info is not None:
      self._inject_cached_ble_device()
      return self._last_service_info.device
    return None

  async def async_shutdown(self) -> None:
    """Stop polling and drop the BLE connection."""
    await super().async_shutdown()
    await self.kettle.disconnect()

  async def _async_update_data(self) -> dict[str, Any]:
    """Poll the kettle and merge the result into the last known state."""
    _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self.address)
    try:
      state = await self.kettle.async_poll(self.get_ble_device_for_connect())
    except KettleError as err:
      self._failed_polls += 1
      if self.data is not None and self._failed_polls < MAX_FAILED_POLLS:
        _LOGGER.debug(
          "Poll %d/%d failed for %s, keeping last state: %s",
          self._failed_polls, MAX_FAILED_POLLS, self.address, err,
        )
        return self.data
      raise UpdateFailed(f"Error polling Fellow Stagg kettle {self.address}: {err}") from err

    self._failed_polls = 0
    fresh_info = async_last_service_info(self.hass, self.address, True)
    if fresh_info is not None:
      self._last_service_info = fresh_info

    data = self._merge_state(state)
    _LOGGER.debug("Polled kettle %s: %s -> %s", self.address, state, data)
    return data

  def _merge_state(self, state: dict[str, Any]) -> dict[str, Any]:
    """Overlay a possibly partial poll on the last known state."""
    previous = dict(self.data or {})
    if "units" in state and state["units"] != previous.get("units"):
      for key in TEMPERATURE_KEYS:
        previous.pop(key, None)
    return {**previous, **state}

  async def _async_command(self, command: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any) -> None:
    """Send a command then refresh so entities reflect the kettle's response."""
    try:
      await command(self.get_ble_device_for_connect(), *args, **kwargs)
    except KettleError as err:
      raise HomeAssistantError(f"Fellow Stagg kettle {self.address}: {err}") from err
    await asyncio.sleep(COMMAND_SETTLE_DELAY)
    await self.async_request_refresh()

  async def async_set_power(self, power_on: bool) -> None:
    """Turn the kettle on or off."""
    await self._async_command(self.kettle.async_set_power, power_on)

  async def async_set_temperature(self, temperature: float) -> None:
    """Set the target temperature in the current unit."""
    await self._async_command(
      self.kettle.async_set_temperature,
      int(temperature),
      fahrenheit=self.temperature_unit == UnitOfTemperature.FAHRENHEIT,
    )
