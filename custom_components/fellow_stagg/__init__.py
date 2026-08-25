"""Support for Fellow Stagg EKG+ kettles."""
import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.bluetooth import (
  async_ble_device_from_address,
  async_last_service_info,
  async_scanner_by_source,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
  DataUpdateCoordinator,
  UpdateFailed,
)

from .const import (
  COMMAND_SETTLE_DELAY,
  CONF_POLLING_INTERVAL,
  CONF_TEMPERATURE_UNIT,
  DEFAULT_POLLING_INTERVAL,
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

PLATFORMS: list[Platform] = [
  Platform.SENSOR,
  Platform.SWITCH,
  Platform.NUMBER,
  Platform.SELECT,
  Platform.WATER_HEATER,
]

# Keys reported in the kettle's current unit; dropped when the unit changes
TEMPERATURE_KEYS = ("target_temp", "current_temp")


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
    self._address = address
    self.entry_id = entry.entry_id
    self._last_service_info = None  # cached for idle-kettle directed connect
    self._failed_polls = 0

    self.device_info = DeviceInfo(
      identifiers={(DOMAIN, address)},
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
      scanner._previous_service_info[self._address] = service_info
      _LOGGER.debug(
        "Injected cached BLE device for %s via scanner %s for directed connect",
        self._address, service_info.source,
      )

  def get_ble_device_for_connect(self):
    """Return the best available BLEDevice, injecting cached state if needed.

    Returns the live device if present, the cached device after cache injection
    if available, or None if no prior advertisement has ever been seen.
    """
    ble_device = async_ble_device_from_address(self.hass, self._address, True)
    if ble_device is not None:
      return ble_device
    if self._last_service_info is not None:
      self._inject_cached_ble_device()
      return self._last_service_info.device
    return None

  async def _async_update_data(self) -> dict[str, Any]:
    """Poll the kettle and merge the result into the last known state."""
    _LOGGER.debug("Starting poll for Fellow Stagg kettle %s", self._address)
    try:
      state = await self.kettle.async_poll(self.get_ble_device_for_connect())
    except KettleError as err:
      self._failed_polls += 1
      if self.data is not None and self._failed_polls < MAX_FAILED_POLLS:
        _LOGGER.debug(
          "Poll %d/%d failed for %s, keeping last state: %s",
          self._failed_polls, MAX_FAILED_POLLS, self._address, err,
        )
        return self.data
      raise UpdateFailed(f"Error polling Fellow Stagg kettle {self._address}: {err}") from err

    self._failed_polls = 0
    fresh_info = async_last_service_info(self.hass, self._address, True)
    if fresh_info is not None:
      self._last_service_info = fresh_info

    data = self._merge_state(state)
    _LOGGER.debug("Polled kettle %s: %s -> %s", self._address, state, data)
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
      raise HomeAssistantError(f"Fellow Stagg kettle {self._address}: {err}") from err
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


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
  """Set up the Fellow Stagg integration."""
  return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Set up Fellow Stagg integration from a config entry."""
  address = entry.unique_id
  if address is None:
    _LOGGER.error("No unique ID provided in config entry")
    return False

  _LOGGER.debug("Setting up Fellow Stagg integration for device: %s", address)
  interval_seconds = entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
  coordinator = FellowStaggDataUpdateCoordinator(hass, entry, address, timedelta(seconds=interval_seconds))

  # Raises ConfigEntryNotReady (HA retries setup) if the kettle can't be reached
  await coordinator.async_config_entry_first_refresh()

  hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

  await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

  _LOGGER.debug("Setup complete for Fellow Stagg device: %s", address)
  return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
  """Unload a config entry."""
  _LOGGER.debug("Unloading Fellow Stagg integration for entry: %s", entry.entry_id)
  if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    coordinator: FellowStaggDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.kettle.disconnect()
  return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
  """Migrate old entry."""
  return True
