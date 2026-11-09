# Mitsubishi Kumo Cloud for Home Assistant

A custom integration that controls Mitsubishi mini splits through the Kumo Cloud
(Comfort app) **v3** cloud API. Each indoor unit becomes a thermostat entity.

**What it supports:** mode (off / cool / heat / dry / fan / auto), setpoints (dual
heat/cool range in auto, or a single auto setpoint on units that use one), fan
speed and vane direction. Modes, fan speeds, vane options and setpoint limits come
from each unit's profile.

**Entities per unit:** the thermostat, plus room temperature, humidity, a filter
warning and defrost (where the unit reports them), and a Wi-Fi signal sensor
(diagnostic, disabled by default). Each site also gets an outdoor temperature
sensor, from the weather report Kumo Cloud provides for its location.

**How it works:** it keeps a live Socket.IO connection to Kumo Cloud, the same
channel the phone app uses. Changes made on the IR remote, an MHK2 wall controller
or the app show up within seconds, and the socket reports when a unit goes
offline. Polling runs underneath as a backstop: every 5 minutes while the socket
is connected, every 60 s when it isn't. Each poll costs 1 request per site plus
1 per unit (plus 1 per site for weather every 15 minutes), well under the API
limit of 50 requests a minute. You can turn the live connection off under
**Configure** on the integration and use polling only.

**Commands** show in HA right away. Changes made within a second of each other,
such as tapping the setpoint arrow several times, are sent as one request.

**Fahrenheit:** if HA uses °F, setpoints are whole degrees, and each one maps
to a 0.5 °C step the unit accepts, so 72 °F stays 72 °F instead of turning
into 71.6 °F.

## Install

1. Copy `custom_components/kumo_cloud` into your Home Assistant `config/custom_components/`
   folder. You can also add this repo to HACS as a custom repository.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**, search for
   **Mitsubishi Kumo Cloud**, and enter your Kumo Cloud app email and password.

This needs Home Assistant 2025.2 or newer.

## Check your account first (optional)

```
pip install aiohttp
python scripts/check_account.py you@example.com
```

The script logs in and prints each unit's current state and capabilities.
If something looks wrong in HA, this output helps with debugging.

## Known limitations

- **Cloud only.** If the internet or Kumo Cloud is down, you can't control the units.
- **Without the live connection, changes made elsewhere can lag**, because the
  REST API can serve cached values, and a unit that drops off Wi-Fi may still
  show as connected.
- **Outdoor temperature is a weather report**, not a reading from your outdoor unit.
- **The API is unofficial.** It was reverse-engineered by the community
  (see [ventz/mitsubishi-comfort-api-kumo](https://github.com/ventz/mitsubishi-comfort-api-kumo)),
  so Mitsubishi can change it without notice.

## Reporting a problem

Download diagnostics from **Settings → Devices & services → Mitsubishi Kumo Cloud
→ ⋮ → Download diagnostics** and attach the file. Your email, password, serial
numbers and network details are removed from it.

## Development

```
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

CI runs the same checks plus Home Assistant's `hassfest` and the HACS validator.
`strings.json` and `translations/en.json` must stay identical (a test checks this).
