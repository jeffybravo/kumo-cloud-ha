"""Polling coordinator for Kumo Cloud."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KumoAuthError, KumoCloudApi, KumoError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type KumoConfigEntry = ConfigEntry[KumoCoordinator]


class KumoCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetches every unit on the account; data is keyed by device serial."""

    config_entry: KumoConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: KumoConfigEntry, api: KumoCloudApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self.site_ids: list[str] = []
        # Capabilities don't change, so fetch once per unit.
        self.profiles: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            if not self.site_ids:
                self.site_ids = [site["id"] for site in await self.api.get_sites()]

            zone_lists = await asyncio.gather(
                *(self.api.get_zones(site_id) for site_id in self.site_ids)
            )
            zones = {
                zone["adapter"]["deviceSerial"]: zone
                for zone_list in zone_lists
                for zone in zone_list
                if zone.get("adapter") and zone.get("isActive", True)
            }
            serials = list(zones)

            details = await asyncio.gather(*(self.api.get_device(s) for s in serials))

            missing = [s for s in serials if s not in self.profiles]
            for serial, profile in zip(
                missing,
                await asyncio.gather(*(self.api.get_profile(s) for s in missing)),
            ):
                self.profiles[serial] = profile
        except KumoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (KumoError, TimeoutError, OSError) as err:
            raise UpdateFailed(str(err)) from err

        data: dict[str, dict[str, Any]] = {}
        for serial, detail in zip(serials, details):
            zone = zones[serial]
            # The device endpoint has fan/vane info; the zone has the room name.
            data[serial] = {**zone["adapter"], **detail, "zoneName": zone["name"]}
        return data
