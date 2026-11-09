"""Minimal async client for the Mitsubishi Kumo Cloud v3 API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

BASE_URL = "https://app-prod.kumocloud.com"
APP_VERSION = "3.2.4"

# aiohttp's default is 5 minutes, long enough to stall a whole poll.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "kumocloud/1122 CFNetwork/3860.200.71 Darwin/25.1.0",
    "x-app-version": APP_VERSION,
    "app-env": "prd",
    "x-allow-cache": "false",
    "Cache-Control": "no-cache",
}


class KumoError(Exception):
    """Generic Kumo Cloud error."""


class KumoAuthError(KumoError):
    """Credentials were rejected."""


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    """Parse a JSON body, accepting empty bodies and loose content types."""
    body = await resp.read()
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError as err:
        raise KumoError(f"Invalid JSON from Kumo Cloud: {err}") from err


class KumoCloudApi:
    """Talks to app-prod.kumocloud.com."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._auth_lock = asyncio.Lock()

    async def login(self) -> dict[str, Any]:
        """Log in with username/password and store the tokens."""
        try:
            async with self._session.post(
                f"{BASE_URL}/v3/login",
                headers=HEADERS,
                json={
                    "username": self._username,
                    "password": self._password,
                    "appVersion": APP_VERSION,
                },
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise KumoAuthError(await resp.text())
                if resp.status >= 400:
                    raise KumoError(f"Login failed: HTTP {resp.status}")
                data = await _read_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KumoError(f"Login failed: {err!r}") from err
        try:
            self._access = data["token"]["access"]
            self._refresh = data["token"]["refresh"]
        except (KeyError, TypeError) as err:
            raise KumoError("Login response had no tokens") from err
        return data

    async def _refresh_tokens(self) -> None:
        """Get a new access token, falling back to a full login."""
        if self._refresh:
            try:
                async with self._session.post(
                    f"{BASE_URL}/v3/refresh",
                    headers={**HEADERS, "Authorization": f"Bearer {self._refresh}"},
                    json={"refresh": self._refresh},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status < 400:
                        data = await _read_json(resp)
                        if isinstance(data, dict) and data.get("access"):
                            self._access = data["access"]
                            self._refresh = data.get("refresh", self._refresh)
                            return
            except (aiohttp.ClientError, TimeoutError) as err:
                raise KumoError(f"Token refresh failed: {err!r}") from err
        await self.login()

    async def access_token(self, *, refresh: bool = False) -> str:
        """Return a valid access token for the Socket.IO connection.

        With refresh=True, get a new one first (after the socket was rejected).
        """
        async with self._auth_lock:
            if self._access is None:
                await self.login()
            elif refresh:
                await self._refresh_tokens()
            if self._access is None:
                raise KumoAuthError("No access token")
            return self._access

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._access is None:
            async with self._auth_lock:
                if self._access is None:
                    await self.login()

        for attempt in range(2):
            token = self._access
            expired = False
            try:
                async with self._session.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers={**HEADERS, "Authorization": f"Bearer {token}"},
                    timeout=REQUEST_TIMEOUT,
                    **kwargs,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        expired = True
                    elif resp.status == 401:
                        raise KumoAuthError("Unauthorized")
                    elif resp.status >= 400:
                        raise KumoError(
                            f"{method} {path} failed: HTTP {resp.status} "
                            f"{await resp.text()}"
                        )
                    else:
                        return await _read_json(resp)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise KumoError(f"{method} {path} failed: {err!r}") from err
            if expired:
                async with self._auth_lock:
                    # Another request may already have refreshed it.
                    if self._access == token:
                        await self._refresh_tokens()
        raise KumoError("Unreachable")

    async def get_sites(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v3/sites")

    async def get_zones(self, site_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/v3/sites/{site_id}/zones")

    async def get_weather(self, site_id: str) -> dict[str, Any]:
        """OpenWeatherMap data for the site's location."""
        return await self._request("GET", f"/v3/sites/{site_id}/weather") or {}

    async def get_device(self, serial: str) -> dict[str, Any]:
        return await self._request("GET", f"/v3/devices/{serial}")

    async def get_profile(self, serial: str) -> dict[str, Any]:
        profile = await self._request("GET", f"/v3/devices/{serial}/profile")
        # Some accounts get a single-element list instead of an object.
        if isinstance(profile, list):
            profile = profile[0] if profile else {}
        return profile if isinstance(profile, dict) else {}

    async def send_command(self, serial: str, commands: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "/v3/devices/send-command",
            json={"deviceSerial": serial, "commands": commands},
        )
