# Stagg EKG+ Home Assistant Integration

A Home Assistant integration for the Fellow Stagg EKG+ electric kettle. Control and monitor your kettle directly from Home Assistant.

## Features

- Control kettle power (on/off)
- Set target temperature
- Monitor current temperature
- Live state updates pushed by the kettle
- Bluetooth discovery support

## Installation

### Option 1: HACS (Recommended)

1. Make sure you have [HACS](https://hacs.xyz) installed
2. Add this repository as a custom repository in HACS:
   - Click the menu icon in the top right of HACS
   - Select "Custom repositories"
   - Add `levi/stagg-ekg-plus-ha` with category "Integration"
3. Click "Download" on the Stagg EKG+ integration
4. Restart Home Assistant
5. Go to Settings -> Devices & Services -> Add Integration
6. Search for "Stagg EKG+"
7. Follow the configuration steps

### Option 2: Manual Installation

1. Copy the `custom_components/fellow_stagg` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant
3. Go to Settings -> Devices & Services -> Add Integration
4. Search for "Stagg EKG+"
5. Follow the configuration steps

## Configuration

The kettle is discovered automatically while it advertises (it stops a few minutes
after use, so lift it or press a button): Home Assistant shows it under
**Settings → Devices & services → Discovered**. You can also add it manually via
**Add Integration → Fellow Stagg EKG+**, which lists discovered kettles or asks for
the Bluetooth address if none is visible. Setup connects to the kettle once to verify
it, so the kettle must be plugged in and not connected to the Fellow app.

A kettle that was unreachable when Home Assistant started is set up as soon as it
advertises again.

## Usage

Once configured, the kettle appears as one device with these entities:

| Entity | Purpose |
| ------ | ------- |
| `water_heater.*` | Power (`off` / `electric`) and target temperature in one entity |
| `switch.*_power` | Kettle power |
| `number.*_target_temperature` | Target temperature |
| `sensor.*_current_temperature` | Water temperature; `unknown` while the kettle is off or lifted |
| `sensor.*_auto_off_countdown` | Seconds until the kettle switches itself off (3600 with hold, 300 without) |
| `binary_sensor.*_on_base` | Kettle sitting on its base |
| `binary_sensor.*_hold` | Keep-warm engaged |
| `binary_sensor.*_hold_button` | Position of the hold slider (diagnostic, disabled by default) |

Temperatures are reported in the kettle's unit and converted by Home Assistant to
your unit system.

### Temperature units

The kettle reports whether it is set to °F or °C and the integration follows it:
the target temperature range is 104–212 °F or 40–100 °C.

### Connection and availability

Home Assistant keeps a Bluetooth connection to the kettle open and the kettle
streams its state about once a second, so entities reflect a change within a
second of the kettle reporting it. A command returns once it has been sent; the
resulting state shows up when the kettle reports it.

If the connection drops, the entities become `unavailable` and Home Assistant
reconnects immediately, then every 15 seconds and whenever the kettle advertises;
they are available again once the kettle has reported its full state. A connection
that stops delivering state is reset. If the kettle cannot be reached, or does not
report its state, when Home Assistant starts, setup is retried and completes as
soon as the kettle advertises.

The kettle stops advertising a few minutes after it is last used but still accepts
connections, and it only accepts one connection at a time: the Fellow app cannot
connect while Home Assistant is connected. After a Home Assistant restart, or if
the connection was lost while the kettle was idle for a while, lift the kettle or
press a button so it advertises again; Home Assistant then connects within seconds.

## Requirements

- Home Assistant 2026.3.0 or newer
- Home Assistant Community Store (HACS) for easy installation
- Bluetooth support in your Home Assistant instance
- A Fellow Stagg EKG+ kettle

## Removing the integration

Remove the kettle under **Settings → Devices & services → Fellow Stagg EKG+**, then
remove the integration in HACS and restart Home Assistant. The kettle keeps no
pairing state.

## Troubleshooting

If you experience connection issues:
1. Ensure the kettle is within Bluetooth range of your Home Assistant device
2. Check that Bluetooth is enabled and working in Home Assistant
3. Verify the MAC address if manually configured
4. Check the Home Assistant logs for detailed error messages

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

The Bluetooth protocol and connection handling live in the
[`fellow-stagg-ble`](https://github.com/ADobin/fellow-stagg-ble) library on PyPI; this
integration only contains the Home Assistant side. Protocol fixes go there first, then the
pin in `manifest.json` and `requirements-test.txt` is bumped here.

### Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/python -m pytest -q
```

`pytest-homeassistant-custom-component` is pinned in `requirements-test.txt` and
brings the matching Home Assistant release with it.

## License

MIT License - see LICENSE file for details
