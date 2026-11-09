"""Real-time updates over the Kumo Cloud Socket.IO channel.

The phone app keeps a Socket.IO connection open. Over it the cloud pushes
`device_update` events when a unit changes (including changes made on the IR
remote or an MHK2 wall controller), answers `device_status_v2` with whether
the adapter is actually online, and `force_adapter_request` makes a unit
report its current state instead of whatever the REST API has cached.

Polling keeps running underneath, more slowly while the socket is up, so the
integration still works if the socket can't connect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import socketio
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KumoCloudApi

if TYPE_CHECKING:
    from .coordinator import KumoCoordinator

_LOGGER = logging.getLogger(__name__)

SOCKET_URL = "https://socket-prod.kumocloud.com"
CONNECT_TIMEOUT = 15
RETRY_MIN = 5
RETRY_MAX = 300
# A connection that lasts this long counts as healthy and resets the backoff.
STABLE_SECONDS = 60


class KumoRealtime:
    """Keeps one Socket.IO connection open and feeds pushes to the coordinator."""

    def __init__(
        self, hass: HomeAssistant, coordinator: KumoCoordinator, api: KumoCloudApi
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._api = api
        self._sio: socketio.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._subscribed: set[str] = set()
        self.connected = False

    # ---- lifecycle -----------------------------------------------------

    @callback
    def start(self) -> None:
        self._task = self._coordinator.config_entry.async_create_background_task(
            self._hass, self._run(), "kumo_cloud realtime"
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._disconnect()

    def _make_client(self) -> socketio.AsyncClient:
        sio = socketio.AsyncClient(
            reconnection=False,  # _run reconnects, with a fresh token
            handle_sigint=False,
            http_session=async_get_clientsession(self._hass),
        )
        sio.on("device_update", self._on_device_update)
        sio.on("device_status_v2", self._on_device_status)
        return sio

    async def _run(self) -> None:
        delay = RETRY_MIN
        refresh_token = False
        while True:
            sio = self._make_client()
            try:
                token = await self._api.access_token(refresh=refresh_token)
                await sio.connect(
                    SOCKET_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    auth={"token": token},
                    transports=["websocket", "polling"],
                    wait_timeout=CONNECT_TIMEOUT,
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - any failure means "retry later"
                _LOGGER.debug("Kumo Cloud socket connect failed: %r", err)
                # The token may have been rejected; get a new one next time.
                refresh_token = True
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX)
                continue

            refresh_token = False
            self._sio = sio
            started = time.monotonic()
            self._set_connected(True)
            try:
                await self.async_sync()
                await sio.wait()
            finally:
                await self._disconnect()
            _LOGGER.debug("Kumo Cloud socket disconnected")
            if time.monotonic() - started > STABLE_SECONDS:
                delay = RETRY_MIN
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX)

    async def _disconnect(self) -> None:
        sio, self._sio = self._sio, None
        self._subscribed.clear()
        self._set_connected(False)
        if sio is not None:
            with contextlib.suppress(Exception):
                await sio.disconnect()

    @callback
    def _set_connected(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        self._coordinator.async_realtime_changed(connected)

    # ---- outgoing ------------------------------------------------------

    async def _emit(self, event: str, data: Any) -> None:
        if self._sio is None:
            return
        try:
            await self._sio.emit(event, data)
        except Exception as err:  # noqa: BLE001 - a lost socket is handled by _run
            _LOGGER.debug("Kumo Cloud socket emit %s failed: %r", event, err)

    async def async_sync(self) -> None:
        """Subscribe to new units and ask every unit for fresh state.

        Called on connect and after every poll.
        """
        if self._sio is None:
            return
        for serial in list(self._coordinator.data or {}):
            if serial not in self._subscribed:
                await self._emit("subscribe", serial)
                self._subscribed.add(serial)
            await self._emit("device_status_v2", serial)
            await self._emit("force_adapter_request", (serial, "iuStatus"))

    async def async_request_status(self, serial: str) -> None:
        """Ask one unit to report its current state (e.g. after a command)."""
        await self._emit("force_adapter_request", (serial, "iuStatus"))

    # ---- incoming ------------------------------------------------------

    async def _on_device_update(self, data: Any) -> None:
        if isinstance(data, dict) and (serial := data.get("deviceSerial")):
            self._coordinator.async_apply_push(serial, data)

    async def _on_device_status(self, data: Any) -> None:
        # The account-level form only acks with an empty serial and no status.
        if (
            isinstance(data, dict)
            and (serial := data.get("deviceSerial"))
            and (status := data.get("status"))
        ):
            self._coordinator.async_set_reachable(serial, status == "connected")
