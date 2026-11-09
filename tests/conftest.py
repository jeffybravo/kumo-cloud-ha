import asyncio
from typing import Any

import pytest

from custom_components.kumo_cloud import const, realtime

from .common import _mock_cloud


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_debounce(monkeypatch):
    """Send commands at once; tests that cover debouncing set their own delay."""
    monkeypatch.setattr(const, "COMMAND_DEBOUNCE", 0)


class FakeSocket:
    """Stands in for socketio.AsyncClient."""

    def __init__(self, hub: "SocketHub", **kwargs: Any) -> None:
        self.hub = hub
        self.kwargs = kwargs
        self.handlers: dict[str, Any] = {}
        self.emits: list[tuple[str, Any]] = []
        self.connect_kwargs: dict[str, Any] = {}
        self.connected = False
        self._closed = asyncio.Event()

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def connect(self, url: str, **kwargs: Any) -> None:
        if self.hub.fail_connects:
            self.hub.fail_connects -= 1
            raise ConnectionError("refused")
        self.connect_kwargs = kwargs
        self.connected = True

    async def emit(self, event: str, data: Any = None) -> None:
        self.emits.append((event, data))

    async def wait(self) -> None:
        await self._closed.wait()

    async def disconnect(self) -> None:
        self.connected = False
        self._closed.set()

    async def push(self, event: str, data: Any) -> None:
        await self.handlers[event](data)


class SocketHub:
    """Every FakeSocket the integration creates, newest last."""

    def __init__(self) -> None:
        self.sockets: list[FakeSocket] = []
        self.fail_connects = 0

    def __call__(self, **kwargs: Any) -> FakeSocket:
        sock = FakeSocket(self, **kwargs)
        self.sockets.append(sock)
        return sock

    @property
    def current(self) -> FakeSocket:
        return self.sockets[-1]


@pytest.fixture(autouse=True)
def socket_hub(monkeypatch) -> SocketHub:
    hub = SocketHub()
    monkeypatch.setattr(realtime.socketio, "AsyncClient", hub)
    return hub


@pytest.fixture
def cloud(aioclient_mock):
    _mock_cloud(aioclient_mock)
    return aioclient_mock
