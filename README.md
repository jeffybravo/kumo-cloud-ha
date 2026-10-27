# Mitsubishi Kumo Cloud for Home Assistant

A custom integration that controls Mitsubishi mini splits through the Kumo Cloud
(Comfort app) **v3** cloud API. Each indoor unit becomes a thermostat entity.

**What it supports:** mode (off / cool / heat / dry / fan / auto), setpoints (dual
heat/cool range in auto), fan speed, vane direction, room temperature, and humidity.
Modes, fan speeds, vane options and setpoint limits come from each unit's profile.

**How it works:** it polls the cloud every 60 s. Each poll costs 1 request per site
plus 1 per unit, which stays well under the API limit of 50 requests a minute.
After you send a command, the new value shows right away and the integration
re-reads the cloud 10 s later.

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
- **Changes made elsewhere can lag.** A change made on an MHK2 wall controller or
  the IR remote may take a while to show up, because the REST API can serve cached
  values. The fix is the app's Socket.IO real-time channel, which isn't implemented yet.
- **Reachability is approximate.** When a unit drops off Wi-Fi, the cloud may keep
  reporting it as connected.
- **The API is unofficial.** It was reverse-engineered by the community
  (see [ventz/mitsubishi-comfort-api-kumo](https://github.com/ventz/mitsubishi-comfort-api-kumo)),
  so Mitsubishi can change it without notice.

## Development

```
pip install pytest-homeassistant-custom-component
pytest
```
