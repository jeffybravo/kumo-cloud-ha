"""Diagnostics download for Kumo Cloud (Settings → Devices → ⋮ → Download)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import KumoConfigEntry

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    "deviceSerial",
    "serialNumber",
    "id",
    "mac",
    "routerSsid",
    "cryptoSerial",
    "cryptoKeySet",
    "userId",
    "imageUrl",
    "address",
    "lat",
    "lon",
    "latitude",
    "longitude",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KumoConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    realtime = coordinator.realtime
    # Serials identify hardware, so units are listed by position instead.
    units = {
        f"unit_{index}": {
            "device": async_redact_data(device, TO_REDACT),
            "profile": async_redact_data(
                coordinator.profiles.get(serial, {}), TO_REDACT
            ),
            "reachable": coordinator.reachable.get(serial),
        }
        for index, (serial, device) in enumerate((coordinator.data or {}).items(), 1)
    }
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "sites": len(coordinator.site_ids),
        "weather_sites": len(coordinator.weather),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "realtime": {
            "enabled": realtime is not None,
            "connected": bool(realtime and realtime.connected),
        },
        "units": units,
    }
