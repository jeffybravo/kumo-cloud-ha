"""Polling coordinator for Kumo Cloud."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KumoAuthError, KumoCloudApi, KumoError
from .const import (
    DOMAIN,
    REALTIME_SCAN_INTERVAL,
    SCAN_INTERVAL,
    SITE_REFRESH_SECONDS,
    WEATHER_REFRESH_SECONDS,
)

if TYPE_CHECKING:
    from .realtime import KumoRealtime

_LOGGER = logging.getLogger(__name__)

type KumoConfigEntry = ConfigEntry[KumoCoordinator]


def _due(last: float | None, now: float, interval: float) -> bool:
    return last is None or now - last > interval


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
        self.site_names: dict[str, str] = {}
        # None = never fetched. Not 0: monotonic() counts from boot, so it can
        # be small right after a reboot.
        self._sites_fetched_at: float | None = None
        self.weather: dict[str, dict[str, Any]] = {}
        self._weather_fetched_at: float | None = None
        # Online/offline from the socket's device_status_v2; absent = unknown.
        self.reachable: dict[str, bool] = {}
        self.realtime: KumoRealtime | None = None
        # Capabilities don't change, so fetch once per unit.
        self.profiles: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            data = await self._fetch()
        except KumoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (KumoError, TimeoutError, OSError) as err:
            raise UpdateFailed(str(err)) from err
        if self.realtime and self.realtime.connected:
            # Have the units push fresh state; REST can serve cached values.
            self.config_entry.async_create_background_task(
                self.hass, self.realtime.async_sync(), "kumo_cloud realtime sync"
            )
        return data

    async def _fetch(self) -> dict[str, dict[str, Any]]:
        # Re-read the site list now and then so new sites show up without a restart.
        now = monotonic()
        if _due(self._sites_fetched_at, now, SITE_REFRESH_SECONDS):
            sites = await self.api.get_sites() or []
            self.site_ids = [site["id"] for site in sites]
            self.site_names = {site["id"]: site.get("name") or "" for site in sites}
            self._sites_fetched_at = now
        if _due(self._weather_fetched_at, now, WEATHER_REFRESH_SECONDS):
            await self._fetch_weather()
            self._weather_fetched_at = now

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

    async def _fetch_weather(self) -> None:
        results = await asyncio.gather(
            *(self.api.get_weather(site_id) for site_id in self.site_ids),
            return_exceptions=True,
        )
        _raise_fatal(results)
        for site_id, weather in zip(self.site_ids, results, strict=True):
            if isinstance(weather, KumoError):
                _LOGGER.debug("Could not read weather for site: %s", weather)
            elif isinstance(weather, dict):
                self.weather[site_id] = weather

    # ---- real-time pushes ------------------------------------------------

    @callback
    def async_apply_push(self, serial: str, payload: dict[str, Any]) -> None:
        """Merge a device_update from the socket into the polled data."""
        if not self.data or serial not in self.data:
            return
        fields = {k: v for k, v in payload.items() if k not in ("deviceSerial", "date")}
        self.data = {**self.data, serial: {**self.data[serial], **fields}}
        # Not async_set_updated_data: that would push back the next poll.
        self.async_update_listeners()

    @callback
    def async_set_reachable(self, serial: str, reachable: bool) -> None:
        if self.reachable.get(serial) != reachable:
            self.reachable[serial] = reachable
            self.async_update_listeners()

    @callback
    def async_realtime_changed(self, connected: bool) -> None:
        self.update_interval = REALTIME_SCAN_INTERVAL if connected else SCAN_INTERVAL
        if not connected and self.reachable:
            # Reachability came from the socket; without it we don't know.
            self.reachable.clear()
            self.async_update_listeners()
