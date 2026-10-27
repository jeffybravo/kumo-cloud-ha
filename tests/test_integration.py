"""Tests for the Kumo Cloud integration against a fake cloud."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.kumo_cloud.api import BASE_URL
from custom_components.kumo_cloud.const import DOMAIN

SERIAL = "1234ABCD"
LOGIN = {"token": {"access": "acc", "refresh": "ref"}, "userId": "u1"}
ZONES = [
    {
        "id": "z1",
        "name": "Living Room",
        "isActive": True,
        "adapter": {"deviceSerial": SERIAL, "connected": True},
    }
]
DEVICE = {
    "deviceSerial": SERIAL,
    "power": 1,
    "operationMode": "heat",
    "previousOperationMode": "heat",
    "roomTemp": 20,
    "humidity": 37,
    "spCool": 24.5,
    "spHeat": 18.5,
    "spAuto": None,
    "fanSpeed": "auto",
    "airDirection": "swing",
    "connected": True,
    "serialNumber": "UNIT1",
    "modelNumber": "MSZ-GL12NA",
}
PROFILE = [
    {
        "hasModeDry": True,
        "hasModeHeat": True,
        "hasModeVent": True,
        "hasVaneDir": True,
        "hasVaneSwing": True,
        "hasFanSpeedAuto": True,
        "numberOfFanSpeeds": 3,
        "minimumSetPoints": {"cool": 19, "heat": 10, "auto": 19},
        "maximumSetPoints": {"cool": 30, "heat": 28, "auto": 28},
    }
]
ENTITY = "climate.living_room"


@pytest.fixture
def cloud(aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    aioclient_mock.get(f"{BASE_URL}/v3/sites", json=[{"id": "s1", "name": "Home"}])
    aioclient_mock.get(f"{BASE_URL}/v3/sites/s1/zones", json=ZONES)
    aioclient_mock.get(f"{BASE_URL}/v3/devices/{SERIAL}", json=DEVICE)
    aioclient_mock.get(f"{BASE_URL}/v3/devices/{SERIAL}/profile", json=PROFILE)
    aioclient_mock.post(
        f"{BASE_URL}/v3/devices/send-command", json={"devices": [SERIAL]}
    )
    return aioclient_mock


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={"username": "me@example.com", "password": "pw"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _commands(cloud):
    return [
        call[2]["commands"]
        for call in cloud.mock_calls
        if str(call[1]).endswith("/send-command")
    ]


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


async def test_commands(hass: HomeAssistant, cloud) -> None:
    await _setup(hass)

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": ENTITY, "temperature": 21.3},
        blocking=True,
    )
    assert hass.states.get(ENTITY).attributes["temperature"] == 21.5

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": ENTITY, "hvac_mode": "cool"},
        blocking=True,
    )
    assert hass.states.get(ENTITY).state == HVACMode.COOL

    # Below this unit's cooling minimum (19 °C) is refused before reaching the cloud.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": ENTITY, "temperature": 5},
            blocking=True,
        )
    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": ENTITY, "fan_mode": "powerful"},
        blocking=True,
    )
    await hass.services.async_call(
        "climate", "set_swing_mode", {"entity_id": ENTITY, "swing_mode": "mid_vertical"},
        blocking=True,
    )
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": ENTITY}, blocking=True
    )
    assert hass.states.get(ENTITY).state == HVACMode.OFF

    assert _commands(cloud) == [
        {"spHeat": 21.5},
        {"operationMode": "cool"},
        {"fanSpeed": "powerful"},
        {"airDirection": "midvertical"},
        {"operationMode": "off"},
    ]


async def test_auto_mode_uses_range(hass: HomeAssistant, cloud) -> None:
    cloud.clear_requests()
    cloud.post(f"{BASE_URL}/v3/login", json=LOGIN)
    cloud.get(f"{BASE_URL}/v3/sites", json=[{"id": "s1"}])
    cloud.get(f"{BASE_URL}/v3/sites/s1/zones", json=ZONES)
    cloud.get(
        f"{BASE_URL}/v3/devices/{SERIAL}", json={**DEVICE, "operationMode": "auto"}
    )
    cloud.get(f"{BASE_URL}/v3/devices/{SERIAL}/profile", json=PROFILE)
    cloud.post(f"{BASE_URL}/v3/devices/send-command", json={})
    await _setup(hass)

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes["target_temp_low"] == 18.5
    assert state.attributes["target_temp_high"] == 24.5

    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": ENTITY, "target_temp_low": 19, "target_temp_high": 25},
        blocking=True,
    )
    assert _commands(cloud) == [{"spHeat": 19, "spCool": 25}]


async def test_expired_token_is_refreshed(hass: HomeAssistant, aioclient_mock) -> None:
    from custom_components.kumo_cloud.api import KumoCloudApi
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    aioclient_mock.post(f"{BASE_URL}/v3/refresh", json={"access": "new", "refresh": "r2"})
    aioclient_mock.get(f"{BASE_URL}/v3/sites", status=401)
    api = KumoCloudApi(async_get_clientsession(hass), "me", "pw")
    await api.login()
    with pytest.raises(Exception):
        await api.get_sites()
    urls = [str(c[1]) for c in aioclient_mock.mock_calls]
    assert urls == [
        f"{BASE_URL}/v3/login",
        f"{BASE_URL}/v3/sites",
        f"{BASE_URL}/v3/refresh",
        f"{BASE_URL}/v3/sites",
    ]
    assert api._access == "new"


async def test_unavailable_when_disconnected(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    aioclient_mock.get(f"{BASE_URL}/v3/sites", json=[{"id": "s1"}])
    aioclient_mock.get(f"{BASE_URL}/v3/sites/s1/zones", json=ZONES)
    aioclient_mock.get(
        f"{BASE_URL}/v3/devices/{SERIAL}", json={**DEVICE, "connected": False}
    )
    aioclient_mock.get(f"{BASE_URL}/v3/devices/{SERIAL}/profile", json=PROFILE)
    await _setup(hass)
    assert hass.states.get(ENTITY).state == "unavailable"
