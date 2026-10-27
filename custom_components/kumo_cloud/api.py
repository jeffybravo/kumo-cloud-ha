"""Minimal async client for the Mitsubishi Kumo Cloud v3 API."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

BASE_URL = "https://app-prod.kumocloud.com"
APP_VERSION = "3.2.4"

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
        async with self._session.post(
            f"{BASE_URL}/v3/login",
            headers=HEADERS,
            json={
                "username": self._username,
                "password": self._password,
                "appVersion": APP_VERSION,
            },
        ) as resp:
            if resp.status in (400, 401, 403):
                raise KumoAuthError(await resp.text())
            if resp.status >= 400:
                raise KumoError(f"Login failed: HTTP {resp.status}")
            data = await resp.json()
        self._access = data["token"]["access"]
        self._refresh = data["token"]["refresh"]
        return data

    async def _refresh_tokens(self) -> None:
        """Get a new access token, falling back to a full login."""
        if self._refresh:
            async with self._session.post(
                f"{BASE_URL}/v3/refresh",
                headers={**HEADERS, "Authorization": f"Bearer {self._refresh}"},
                json={"refresh": self._refresh},
            ) as resp:
                if resp.status < 400:
                    data = await resp.json()
                    self._access = data["access"]
                    self._refresh = data.get("refresh", self._refresh)
                    return
        await self.login()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._access is None:
            async with self._auth_lock:
                if self._access is None:
                    await self.login()

        for attempt in range(2):
            token = self._access
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                headers={**HEADERS, "Authorization": f"Bearer {token}"},
                **kwargs,
            ) as resp:
                if resp.status == 401 and attempt == 0:
                    async with self._auth_lock:
                        # Another request may already have refreshed it.
                        if self._access == token:
                            await self._refresh_tokens()
                    continue
                if resp.status == 401:
                    raise KumoAuthError("Unauthorized")
                if resp.status >= 400:
                    raise KumoError(
                        f"{method} {path} failed: HTTP {resp.status} {await resp.text()}"
                    )
                return await resp.json()
        raise KumoError("Unreachable")

    async def get_sites(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v3/sites")

    async def get_zones(self, site_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/v3/sites/{site_id}/zones")

    async def get_device(self, serial: str) -> dict[str, Any]:
        return await self._request("GET", f"/v3/devices/{serial}")

    async def get_profile(self, serial: str) -> dict[str, Any]:
        profile = await self._request("GET", f"/v3/devices/{serial}/profile")
        # Some accounts get a single-element list instead of an object.
        if isinstance(profile, list):
            profile = profile[0] if profile else {}
        return profile

    async def send_command(self, serial: str, commands: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "/v3/devices/send-command",
            json={"deviceSerial": serial, "commands": commands},
        )
