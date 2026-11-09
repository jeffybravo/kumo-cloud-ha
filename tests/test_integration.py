"""Tests for the Kumo Cloud integration against a fake cloud."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
)

from custom_components.kumo_cloud import async_remove_config_entry_device
from custom_components.kumo_cloud.api import BASE_URL, KumoAuthError, KumoCloudApi
from custom_components.kumo_cloud.const import COMMAND_REFRESH_DELAY, DOMAIN

from .common import (
    DEVICE,
    ENTITY,
    ENTITY2,
    LOGIN,
    PROFILE,
    SERIAL,
    SERIAL2,
    ZONES,
    _call,
    _commands,
    _mock_cloud,
    _poll,
    _setup,
    _zone,
)

# ---- config flow -------------------------------------------------------


async def test_config_flow(hass: HomeAssistant, cloud) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": " Me@Example.com ", "password": "pw"}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["username"] == "Me@Example.com"


async def test_config_flow_bad_password(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", status=401, text="nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    "exc",
    [
        aiohttp.ServerDisconnectedError(),
        aiohttp.ClientPayloadError("truncated"),
        TimeoutError(),
    ],
)
async def test_config_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock, exc
) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", exc=exc)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_html_response(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", text="<html>maintenance</html>")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "me@example.com", "password": "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_unknown_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(KumoCloudApi, "login", side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "me@example.com", "password": "pw"}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_config_flow_already_configured(hass: HomeAssistant, cloud) -> None:
    await _setup(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "ME@example.com", "password": "pw"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reauth_flow(hass: HomeAssistant, cloud) -> None:
    entry = await _setup(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch.object(KumoCloudApi, "login", side_effect=KumoAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with patch.object(KumoCloudApi, "login", side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "wrong"}
        )
    assert result["errors"] == {"base": "unknown"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new-pw"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-pw"


async def test_auth_failure_during_poll_starts_reauth(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    aioclient_mock.post(f"{BASE_URL}/v3/refresh", status=401)
    aioclient_mock.get(f"{BASE_URL}/v3/sites", status=401)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


# ---- state -------------------------------------------------------------


async def test_climate_state(hass: HomeAssistant, cloud) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT
    assert state.attributes["current_temperature"] == 20
    assert state.attributes["temperature"] == 18.5
    assert state.attributes["current_humidity"] == 37
    assert state.attributes["min_temp"] == 10
    assert state.attributes["max_temp"] == 28
    assert state.attributes["fan_modes"] == ["quiet", "low", "powerful", "auto"]
    assert state.attributes["fan_mode"] == "auto"
    assert state.attributes["swing_mode"] == "swing"
    assert HVACMode.FAN_ONLY in state.attributes["hvac_modes"]
    assert (
        state.attributes["supported_features"]
        & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    )


async def test_profile_as_object(hass: HomeAssistant, aioclient_mock) -> None:
    profile = {**PROFILE[0], "hasModeVent": False, "numberOfFanSpeeds": 5}
    _mock_cloud(aioclient_mock, profiles={SERIAL: profile})
    await _setup(hass)
    state = hass.states.get(ENTITY)
    assert HVACMode.FAN_ONLY not in state.attributes["hvac_modes"]
    assert state.attributes["fan_modes"][0] == "super_quiet"


async def test_unavailable_when_disconnected(
    hass: HomeAssistant, aioclient_mock
) -> None:
    _mock_cloud(aioclient_mock, devices={SERIAL: {**DEVICE, "connected": False}})
    await _setup(hass)
    assert hass.states.get(ENTITY).state == "unavailable"


@pytest.mark.parametrize(
    "exc",
    [
        aiohttp.ServerDisconnectedError(),
        aiohttp.ClientPayloadError("x"),
        TimeoutError(),
    ],
)
async def test_poll_error_marks_unavailable_and_recovers(
    hass: HomeAssistant, cloud, exc
) -> None:
    entry = await _setup(hass)
    assert hass.states.get(ENTITY).state == HVACMode.HEAT

    cloud.clear_requests()
    cloud.get(f"{BASE_URL}/v3/sites/s1/zones", exc=exc)
    await _poll(hass, entry)
    assert hass.states.get(ENTITY).state == "unavailable"
    assert entry.state is ConfigEntryState.LOADED

    _mock_cloud(cloud)
    await _poll(hass, entry)
    assert hass.states.get(ENTITY).state == HVACMode.HEAT


async def test_one_failing_unit_does_not_affect_others(
    hass: HomeAssistant, aioclient_mock
) -> None:
    zones = [*ZONES, _zone(SERIAL2, "Bedroom")]
    good = {SERIAL: DEVICE, SERIAL2: {**DEVICE, "deviceSerial": SERIAL2}}
    _mock_cloud(aioclient_mock, zones=zones, devices=good)
    entry = await _setup(hass)
    assert hass.states.get(ENTITY2).state == HVACMode.HEAT

    _mock_cloud(
        aioclient_mock,
        zones=zones,
        devices={SERIAL: DEVICE, SERIAL2: aiohttp.ServerDisconnectedError()},
    )
    await _poll(hass, entry)
    assert hass.states.get(ENTITY).state == HVACMode.HEAT
    assert hass.states.get(ENTITY2).state == "unavailable"


async def test_profile_failure_retried_next_poll(
    hass: HomeAssistant, aioclient_mock
) -> None:
    zones = [*ZONES, _zone(SERIAL2, "Bedroom")]
    devices = {SERIAL: DEVICE, SERIAL2: {**DEVICE, "deviceSerial": SERIAL2}}
    _mock_cloud(
        aioclient_mock,
        zones=zones,
        devices=devices,
        profiles={SERIAL2: aiohttp.ServerDisconnectedError()},
    )
    entry = await _setup(hass)
    assert hass.states.get(ENTITY).state == HVACMode.HEAT
    assert hass.states.get(ENTITY2) is None

    _mock_cloud(aioclient_mock, zones=zones, devices=devices)
    await _poll(hass, entry)
    assert hass.states.get(ENTITY2).state == HVACMode.HEAT


async def test_new_unit_added_at_runtime(hass: HomeAssistant, cloud) -> None:
    entry = await _setup(hass)
    assert hass.states.get(ENTITY2) is None

    _mock_cloud(
        cloud,
        zones=[*ZONES, _zone(SERIAL2, "Bedroom")],
        devices={SERIAL: DEVICE, SERIAL2: {**DEVICE, "deviceSerial": SERIAL2}},
    )
    await _poll(hass, entry)
    assert hass.states.get(ENTITY2).state == HVACMode.HEAT


async def test_remove_stale_device(hass: HomeAssistant, aioclient_mock) -> None:
    zones = [*ZONES, _zone(SERIAL2, "Bedroom")]
    devices = {SERIAL: DEVICE, SERIAL2: {**DEVICE, "deviceSerial": SERIAL2}}
    _mock_cloud(aioclient_mock, zones=zones, devices=devices)
    entry = await _setup(hass)

    _mock_cloud(aioclient_mock)
    await _poll(hass, entry)

    registry = dr.async_get(hass)
    current = registry.async_get_device(identifiers={(DOMAIN, SERIAL)})
    stale = registry.async_get_device(identifiers={(DOMAIN, SERIAL2)})
    assert not await async_remove_config_entry_device(hass, entry, current)
    assert await async_remove_config_entry_device(hass, entry, stale)


# ---- commands ----------------------------------------------------------


async def test_commands(hass: HomeAssistant, cloud) -> None:
    await _setup(hass)

    await _call(hass, "set_temperature", temperature=21.3)
    assert hass.states.get(ENTITY).attributes["temperature"] == 21.5

    await _call(hass, "set_hvac_mode", hvac_mode="cool")
    assert hass.states.get(ENTITY).state == HVACMode.COOL

    # Below this unit's cooling minimum (19 °C) is refused before reaching the cloud.
    with pytest.raises(ServiceValidationError):
        await _call(hass, "set_temperature", temperature=5)
    await _call(hass, "set_fan_mode", fan_mode="powerful")
    await _call(hass, "set_swing_mode", swing_mode="mid_vertical")
    await _call(hass, "turn_off")
    assert hass.states.get(ENTITY).state == HVACMode.OFF

    assert _commands(cloud) == [
        {"spHeat": 21.5},
        {"operationMode": "cool"},
        {"fanSpeed": "powerful"},
        {"airDirection": "midvertical"},
        {"operationMode": "off"},
    ]


async def test_command_with_empty_response(hass: HomeAssistant, cloud) -> None:
    await _setup(hass)
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/v3/devices/send-command", text="")
    await _call(hass, "set_fan_mode", fan_mode="low")
    assert hass.states.get(ENTITY).attributes["fan_mode"] == "low"


async def test_auto_mode_uses_range(hass: HomeAssistant, aioclient_mock) -> None:
    _mock_cloud(aioclient_mock, devices={SERIAL: {**DEVICE, "operationMode": "auto"}})
    await _setup(hass)

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes["target_temp_low"] == 18.5
    assert state.attributes["target_temp_high"] == 24.5

    await _call(hass, "set_temperature", target_temp_low=19, target_temp_high=25)
    assert _commands(aioclient_mock) == [{"spHeat": 19, "spCool": 25}]

    # A single setpoint makes no sense on a dual-setpoint unit in auto.
    with pytest.raises(ServiceValidationError):
        await _call(hass, "set_temperature", temperature=22)
    assert len(_commands(aioclient_mock)) == 1


async def test_auto_mode_single_setpoint(hass: HomeAssistant, aioclient_mock) -> None:
    device = {**DEVICE, "operationMode": "auto", "spAuto": 22}
    _mock_cloud(aioclient_mock, devices={SERIAL: device})
    await _setup(hass)

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes["temperature"] == 22
    assert state.attributes.get("target_temp_low") is None
    assert not (
        state.attributes["supported_features"]
        & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    )

    await _call(hass, "set_temperature", temperature=23.2)
    assert _commands(aioclient_mock) == [{"spAuto": 23.0}]


@pytest.mark.parametrize(
    ("previous", "expected"), [("cool", "cool"), ("off", "auto"), (None, "auto")]
)
async def test_turn_on(hass: HomeAssistant, aioclient_mock, previous, expected) -> None:
    device = {
        **DEVICE,
        "power": 0,
        "operationMode": "off",
        "previousOperationMode": previous,
    }
    _mock_cloud(aioclient_mock, devices={SERIAL: device})
    await _setup(hass)
    assert hass.states.get(ENTITY).state == HVACMode.OFF

    await _call(hass, "turn_on")
    assert _commands(aioclient_mock) == [{"operationMode": expected}]
    assert hass.states.get(ENTITY).state != HVACMode.OFF


async def test_optimistic_value_released_once_confirmed(
    hass: HomeAssistant, cloud
) -> None:
    entry = await _setup(hass)
    await _call(hass, "set_temperature", temperature=21)

    # A poll that hasn't caught up yet doesn't flip the UI back.
    await _poll(hass, entry)
    assert hass.states.get(ENTITY).attributes["temperature"] == 21

    # The cloud confirms it...
    _mock_cloud(cloud, devices={SERIAL: {**DEVICE, "spHeat": 21}})
    await _poll(hass, entry)
    # ...so a later change on the remote shows up at once, not after 45 s.
    _mock_cloud(cloud, devices={SERIAL: {**DEVICE, "spHeat": 23}})
    await _poll(hass, entry)
    assert hass.states.get(ENTITY).attributes["temperature"] == 23


def _device_reads(mock) -> int:
    return sum(
        1 for call in mock.mock_calls if str(call[1]).endswith(f"/devices/{SERIAL}")
    )


async def test_refresh_after_command(hass: HomeAssistant, cloud) -> None:
    await _setup(hass, realtime=False)
    await _call(hass, "set_fan_mode", fan_mode="low")
    before = _device_reads(cloud)

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=COMMAND_REFRESH_DELAY + 1)
    )
    await hass.async_block_till_done()
    assert _device_reads(cloud) == before + 1


async def test_refresh_cancelled_on_unload(hass: HomeAssistant, cloud) -> None:
    entry = await _setup(hass)
    await _call(hass, "set_fan_mode", fan_mode="low")
    assert await hass.config_entries.async_unload(entry.entry_id)
    before = len(cloud.mock_calls)

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=COMMAND_REFRESH_DELAY + 1)
    )
    await hass.async_block_till_done()
    assert len(cloud.mock_calls) == before


# ---- API client --------------------------------------------------------


async def test_expired_token_is_refreshed(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    aioclient_mock.post(
        f"{BASE_URL}/v3/refresh", json={"access": "new", "refresh": "r2"}
    )
    aioclient_mock.get(f"{BASE_URL}/v3/sites", status=401)
    api = KumoCloudApi(async_get_clientsession(hass), "me", "pw")
    await api.login()
    with pytest.raises(KumoAuthError):
        await api.get_sites()
    urls = [str(c[1]) for c in aioclient_mock.mock_calls]
    assert urls == [
        f"{BASE_URL}/v3/login",
        f"{BASE_URL}/v3/sites",
        f"{BASE_URL}/v3/refresh",
        f"{BASE_URL}/v3/sites",
    ]
    assert api._access == "new"


# ---- repo hygiene ------------------------------------------------------


def test_translations_match_strings() -> None:
    root = Path(__file__).parent.parent / "custom_components" / DOMAIN
    strings = json.loads((root / "strings.json").read_text())
    english = json.loads((root / "translations" / "en.json").read_text())
    assert strings == english
