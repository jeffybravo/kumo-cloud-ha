"""Polling coordinator for Kumo Cloud."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KumoAuthError, KumoCloudApi, KumoError
from .const import DOMAIN, SCAN_INTERVAL, SITE_REFRESH_SECONDS

_LOGGER = logging.getLogger(__name__)

type KumoConfigEntry = ConfigEntry[KumoCoordinator]


def _raise_fatal(results: list[Any]) -> None:
    """Re-raise errors from a gather(return_exceptions=True) that affect every unit.

    Auth failures and unexpected exceptions (bugs) are re-raised; ordinary
    per-unit cloud errors (KumoError) are left for the caller to skip.
    """
    for result in results:
        if isinstance(result, KumoAuthError):
            raise result
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, KumoError):
            raise result


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
        self._sites_fetched_at = 0.0
        # Capabilities don't change, so fetch once per unit.
        self.profiles: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            return await self._fetch()
        except KumoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (KumoError, TimeoutError, OSError) as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> dict[str, dict[str, Any]]:
        # Re-read the site list now and then so new sites show up without a restart.
        now = time.monotonic()
        if not self.site_ids or now - self._sites_fetched_at > SITE_REFRESH_SECONDS:
            self.site_ids = [site["id"] for site in await self.api.get_sites() or []]
            self._sites_fetched_at = now

        zone_lists = await asyncio.gather(
            *(self.api.get_zones(site_id) for site_id in self.site_ids)
        )
        zones = {
            zone["adapter"]["deviceSerial"]: zone
            for zone_list in zone_lists
            for zone in zone_list or []
            if zone.get("adapter") and zone.get("isActive", True)
        }
        serials = list(zones)

        # One bad unit shouldn't take every other unit down with it.
        missing = [s for s in serials if s not in self.profiles]
        details, profiles = await asyncio.gather(
            asyncio.gather(
                *(self.api.get_device(s) for s in serials), return_exceptions=True
            ),
            asyncio.gather(
                *(self.api.get_profile(s) for s in missing), return_exceptions=True
            ),
        )
        _raise_fatal([*details, *profiles])

        for serial, profile in zip(missing, profiles, strict=True):
            if isinstance(profile, KumoError):
                _LOGGER.warning("Could not read profile for %s: %s", serial, profile)
            else:
                self.profiles[serial] = profile

        data: dict[str, dict[str, Any]] = {}
        for serial, detail in zip(serials, details, strict=True):
            if isinstance(detail, KumoError):
                _LOGGER.warning("Could not read unit %s: %s", serial, detail)
                continue
            if serial not in self.profiles:
                # Entities are built from the profile; retry on the next poll.
                continue
            zone = zones[serial]
            # The device endpoint has fan/vane info; the zone has the room name.
            data[serial] = {
                **zone["adapter"],
                **(detail or {}),
                "zoneName": zone["name"],
            }

        if serials and not data:
            raise UpdateFailed("Could not read any unit")
        return data
