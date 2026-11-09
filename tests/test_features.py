"""Tests for real-time updates, Fahrenheit, debouncing, sensors and diagnostics."""

import asyncio
import json
from datetime import timedelta

import aiohttp
import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.kumo_cloud import const, realtime
from custom_components.kumo_cloud.api import BASE_URL
from custom_components.kumo_cloud.const import (
    COMMAND_REFRESH_DELAY,
    REALTIME_SCAN_INTERVAL,
    SCAN_INTERVAL,
)
from custom_components.kumo_cloud.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.kumo_cloud.temperature import (
    setpoint_c_to_f,
    setpoint_f_to_c,
)

from .common import (
    DEVICE,
    ENTITY,
    LOGIN,
    SERIAL,
    _call,
    _commands,
    _mock_cloud,
    _poll,
    _setup,
)


async def _connected(hass: HomeAssistant, hub):
    """Wait for the integration's socket to connect and finish its first sync."""
    for _ in range(50):
        if hub.sockets and hub.current.connected:
            await hass.async_block_till_done()
            return hub.current
        await asyncio.sleep(0)
    raise AssertionError("socket never connected")


def _device_reads(mock) -> int:
    return sum(
        1 for call in mock.mock_calls if str(call[1]).endswith(f"/devices/{SERIAL}")
    )


# ---- real-time ---------------------------------------------------------


async def test_realtime_connects_and_subscribes(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    entry = await _setup(hass)
    sock = await _connected(hass, socket_hub)

    assert sock.connect_kwargs["headers"] == {"Authorization": "Bearer acc"}
    assert sock.connect_kwargs["auth"] == {"token": "acc"}
    assert sock.kwargs["reconnection"] is False
    assert ("subscribe", SERIAL) in sock.emits
    assert ("device_status_v2", SERIAL) in sock.emits
    assert ("force_adapter_request", (SERIAL, "iuStatus")) in sock.emits
    assert entry.runtime_data.update_interval == REALTIME_SCAN_INTERVAL

    # Each poll asks again, but subscribes only once.
    sock.emits.clear()
    await _poll(hass, entry)
    assert ("subscribe", SERIAL) not in sock.emits
    assert ("force_adapter_request", (SERIAL, "iuStatus")) in sock.emits


async def test_push_updates_state_without_polling(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    await _setup(hass)
    sock = await _connected(hass, socket_hub)
    reads = _device_reads(cloud)

    await sock.push(
        "device_update",
        {
            "deviceSerial": SERIAL,
            "spHeat": 21,
            "roomTemp": 19.5,
            "displayConfig": {"filter": True, "defrost": False},
            "date": "2026-11-09T00:00:00Z",
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.attributes["temperature"] == 21
    assert state.attributes["current_temperature"] == 19.5
    assert state.attributes["fan_mode"] == "auto"  # untouched fields are kept
    assert hass.states.get("sensor.living_room_room_temperature").state == "19.5"
    assert hass.states.get("binary_sensor.living_room_filter").state == "on"
    assert _device_reads(cloud) == reads

    # Pushes for units we don't know, or junk, are ignored.
    await sock.push("device_update", {"deviceSerial": "OTHER", "spHeat": 30})
    await sock.push("device_update", "junk")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["temperature"] == 21


async def test_push_releases_optimistic_value(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    await _setup(hass)
    sock = await _connected(hass, socket_hub)
    await _call(hass, "set_temperature", temperature=21)
    await sock.push("device_update", {"deviceSerial": SERIAL, "spHeat": 21})
    await sock.push("device_update", {"deviceSerial": SERIAL, "spHeat": 23})
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["temperature"] == 23


async def test_reachability_from_socket(
    hass: HomeAssistant, cloud, socket_hub, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "RETRY_MIN", 0)
    entry = await _setup(hass)
    sock = await _connected(hass, socket_hub)

    await sock.push("device_status_v2", {"deviceSerial": SERIAL, "status": "offline"})
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unavailable"
    assert hass.states.get("sensor.living_room_humidity").state == "unavailable"

    # The account-level ack carries no serial or status.
    await sock.push("device_status_v2", {"deviceSerial": "", "date": "x"})
    await sock.push("device_status_v2", {"deviceSerial": SERIAL, "status": "connected"})
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == HVACMode.HEAT

    # Socket drops: reachability is forgotten, polling speeds up, and it reconnects.
    await sock.push("device_status_v2", {"deviceSerial": SERIAL, "status": "offline"})
    await sock.disconnect()
    for _ in range(50):
        if len(socket_hub.sockets) > 1 and socket_hub.current.connected:
            break
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert len(socket_hub.sockets) == 2
    assert hass.states.get(ENTITY).state == HVACMode.HEAT
    assert entry.runtime_data.update_interval == REALTIME_SCAN_INTERVAL


async def test_socket_failure_retries_with_fresh_token(
    hass: HomeAssistant, cloud, socket_hub, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "RETRY_MIN", 0)
    cloud.post(f"{BASE_URL}/v3/refresh", json={"access": "fresh", "refresh": "r2"})
    socket_hub.fail_connects = 1
    entry = await _setup(hass)
    sock = await _connected(hass, socket_hub)

    assert len(socket_hub.sockets) == 2
    assert sock.connect_kwargs["auth"] == {"token": "fresh"}
    assert entry.state is ConfigEntryState.LOADED


async def test_polling_only_while_socket_down(
    hass: HomeAssistant, cloud, socket_hub, monkeypatch
) -> None:
    monkeypatch.setattr(realtime, "RETRY_MIN", 3600)
    socket_hub.fail_connects = 1
    entry = await _setup(hass)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval == SCAN_INTERVAL
    assert hass.states.get(ENTITY).state == HVACMode.HEAT
    await hass.config_entries.async_unload(entry.entry_id)


async def test_command_refresh_uses_socket(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    await _setup(hass)
    sock = await _connected(hass, socket_hub)
    await _call(hass, "set_fan_mode", fan_mode="low")
    sock.emits.clear()
    reads = _device_reads(cloud)

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=COMMAND_REFRESH_DELAY + 1)
    )
    await hass.async_block_till_done()
    assert sock.emits == [("force_adapter_request", (SERIAL, "iuStatus"))]
    assert _device_reads(cloud) == reads


async def test_unload_closes_socket(hass: HomeAssistant, cloud, socket_hub) -> None:
    entry = await _setup(hass)
    sock = await _connected(hass, socket_hub)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not sock.connected


async def test_options_flow_turns_realtime_off(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    entry = await _setup(hass)
    sock = await _connected(hass, socket_hub)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({})["realtime"] is True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"realtime": False}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.options == {"realtime": False}
    assert entry.state is ConfigEntryState.LOADED
    assert not sock.connected
    assert len(socket_hub.sockets) == 1  # the reload didn't open a new one
    assert entry.runtime_data.realtime is None


# ---- Fahrenheit --------------------------------------------------------


def test_every_whole_fahrenheit_round_trips() -> None:
    for f in range(40, 100):
        c = setpoint_f_to_c(f)
        assert c * 2 == int(c * 2), f  # a 0.5 °C step
        assert setpoint_c_to_f(c) == f


async def test_fahrenheit(hass: HomeAssistant, cloud) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _setup(hass)

    state = hass.states.get(ENTITY)
    assert state.attributes["temperature"] == 65  # 18.5 °C
    assert state.attributes["current_temperature"] == 68.0  # 20 °C
    assert state.attributes["target_temp_step"] == 1
    assert state.attributes["min_temp"] == 50  # 10 °C
    assert state.attributes["max_temp"] == 82  # 28 °C

    await _call(hass, "set_temperature", temperature=72)
    await _call(hass, "set_temperature", temperature=73)
    assert _commands(cloud) == [{"spHeat": 22.0}, {"spHeat": 23.0}]
    assert hass.states.get(ENTITY).attributes["temperature"] == 73


# ---- debouncing --------------------------------------------------------


async def test_rapid_commands_are_merged(hass: HomeAssistant, cloud) -> None:
    await _setup(hass, realtime=False)
    const.COMMAND_DEBOUNCE = 0.05  # restored by the no_debounce fixture

    await asyncio.gather(
        _call(hass, "set_temperature", temperature=19),
        _call(hass, "set_temperature", temperature=19.5),
        _call(hass, "set_fan_mode", fan_mode="low"),
        _call(hass, "set_temperature", temperature=20),
    )
    assert _commands(cloud) == [{"spHeat": 20, "fanSpeed": "low"}]
    assert hass.states.get(ENTITY).attributes["temperature"] == 20


async def test_failed_command_reverts_optimistic_state(
    hass: HomeAssistant, cloud
) -> None:
    await _setup(hass, realtime=False)
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/v3/devices/send-command", status=400, text="bad")

    with pytest.raises(HomeAssistantError):
        await _call(hass, "set_hvac_mode", hvac_mode="cool")
    assert hass.states.get(ENTITY).state == HVACMode.HEAT


# ---- sensors -----------------------------------------------------------


async def test_sensors(hass: HomeAssistant, cloud) -> None:
    await _setup(hass)

    assert hass.states.get("sensor.living_room_room_temperature").state == "20"
    assert hass.states.get("sensor.living_room_humidity").state == "37"
    assert hass.states.get("sensor.home_outdoor_temperature").state == "-1.5"
    assert hass.states.get("binary_sensor.living_room_filter").state == "off"
    assert hass.states.get("binary_sensor.living_room_defrost").state == "off"

    # Wi-Fi signal is a diagnostic sensor, off until the user enables it.
    wifi = er.async_get(hass).async_get("sensor.living_room_wi_fi_signal")
    assert wifi is not None
    assert wifi.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_sensors_skip_missing_values(hass: HomeAssistant, aioclient_mock) -> None:
    device = {k: v for k, v in DEVICE.items() if k not in ("humidity", "displayConfig")}
    _mock_cloud(
        aioclient_mock,
        devices={SERIAL: device},
        weather=aiohttp.ServerDisconnectedError(),
    )
    entry = await _setup(hass, realtime=False)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.living_room_humidity") is None
    assert hass.states.get("binary_sensor.living_room_filter") is None
    assert hass.states.get("sensor.home_outdoor_temperature") is None
    assert hass.states.get(ENTITY).state == HVACMode.HEAT


async def test_weather_refreshed_rarely(hass: HomeAssistant, cloud) -> None:
    entry = await _setup(hass, realtime=False)
    await _poll(hass, entry)
    await _poll(hass, entry)
    weather_reads = sum(1 for c in cloud.mock_calls if str(c[1]).endswith("/weather"))
    assert weather_reads == 1


# ---- diagnostics -------------------------------------------------------


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, cloud, socket_hub
) -> None:
    entry = await _setup(hass)
    await _connected(hass, socket_hub)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diag)
    for secret in ("pw", "me@example.com", SERIAL, "UNIT1", LOGIN["token"]["access"]):
        assert f'"{secret}"' not in dumped, secret
    unit = diag["units"]["unit_1"]
    assert unit["device"]["spHeat"] == 18.5
    assert unit["profile"]["numberOfFanSpeeds"] == 3
    assert diag["realtime"] == {"enabled": True, "connected": True}
