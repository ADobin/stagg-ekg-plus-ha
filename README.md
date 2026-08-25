# Stagg EKG+ Home Assistant Integration

A Home Assistant integration for the Fellow Stagg EKG+ electric kettle. Control and monitor your kettle directly from Home Assistant.

## Features

- Control kettle power (on/off)
- Set target temperature
- Monitor current temperature
- Automatic temperature updates
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

The integration can be set up in two ways:

1. **Automatic Discovery**: The kettle will be automatically discovered if Bluetooth is enabled in Home Assistant
2. **Manual Configuration**: You can manually add the kettle by providing its MAC address

## Usage

Once configured, the kettle appears as one device with these entities:

| Entity | Purpose |
| ------ | ------- |
| `water_heater.*_water_heater` | Power and target temperature in one entity |
| `switch.*_power` | Kettle power |
| `number.*_target_temperature` | Target temperature in the kettle's unit |
| `sensor.*_current_temperature`, `sensor.*_target_temperature` | Temperatures reported by the kettle |
| `sensor.*_power`, `sensor.*_hold_mode`, `sensor.*_kettle_position`, `sensor.*_countdown` | Kettle status |
| `number.*_polling_interval` | Seconds between polls, 5–60 (diagnostic, disabled by default) |
| `select.*_fallback_temperature_unit` | Unit assumed before the kettle reports one (config, disabled by default) |

### Temperature units

The kettle reports whether it is set to °F or °C and the integration follows it:
the target temperature entities switch their unit and range (104–212 °F or 40–100 °C)
as soon as a poll includes it. Until the first poll that includes the unit, the
integration assumes the unit from the *Fallback Temperature Unit* select, which
defaults to *Auto* (your Home Assistant unit system). The kettle's own unit always
takes precedence once known.

### Availability and polling

The kettle is polled every 5 seconds by default. A poll keeps whatever the kettle
sent, so a partial response only updates the values it contained. After three
consecutive failed polls the entities become `unavailable` until a poll succeeds
again. If the kettle cannot be reached when Home Assistant starts, the config entry
retries setup in the background.

Commands (power, target temperature) raise an error if the kettle cannot be reached.
After a command the integration re-polls once after a short delay; the kettle can take
a few seconds to reflect a change, so the state may update on a following poll.

The kettle stops advertising a few minutes after it is last used but still accepts
directed connections; the integration keeps the last seen advertisement to connect
to an idle kettle. After a Home Assistant restart the kettle must advertise once
(lift it or press a button) before it can be reached.

## Requirements

- Home Assistant 2024.8.0 or newer
- Home Assistant Community Store (HACS) for easy installation
- Bluetooth support in your Home Assistant instance
- A Fellow Stagg EKG+ kettle

## Troubleshooting

If you experience connection issues:
1. Ensure the kettle is within Bluetooth range of your Home Assistant device
2. Check that Bluetooth is enabled and working in Home Assistant
3. Verify the MAC address if manually configured
4. Check the Home Assistant logs for detailed error messages

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

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
