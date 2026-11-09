"""Fake cloud and helpers shared by the tests."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kumo_cloud.api import BASE_URL
from custom_components.kumo_cloud.const import DOMAIN

SERIAL = "1234ABCD"
SERIAL2 = "5678EFGH"
LOGIN = {"token": {"access": "acc", "refresh": "ref"}, "userId": "u1"}


def _zone(serial: str, name: str) -> dict:
    return {
        "id": f"z-{serial}",
        "name": name,
        "isActive": True,
        "adapter": {"deviceSerial": serial, "connected": True},
    }


ZONES = [_zone(SERIAL, "Living Room")]
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
    "rssi": -43,
    "displayConfig": {"filter": False, "defrost": False},
}
WEATHER = {"main": {"temp": -1.5, "humidity": 81}, "name": "Somewhere"}
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
ENTITY2 = "climate.bedroom"


def _mock_cloud(
    mock, *, zones=None, devices=None, profiles=None, weather=WEATHER
) -> None:
    """(Re)register every cloud endpoint.

    `devices` / `profiles` map serial -> JSON body, or -> an exception to raise.
    """
    zones = ZONES if zones is None else zones
    devices = devices or {SERIAL: DEVICE}
    profiles = profiles or {}
    mock.clear_requests()
    mock.post(f"{BASE_URL}/v3/login", json=LOGIN)
    mock.get(f"{BASE_URL}/v3/sites", json=[{"id": "s1", "name": "Home"}])
    mock.get(f"{BASE_URL}/v3/sites/s1/zones", json=zones)
    if isinstance(weather, Exception):
        mock.get(f"{BASE_URL}/v3/sites/s1/weather", exc=weather)
    else:
        mock.get(f"{BASE_URL}/v3/sites/s1/weather", json=weather)
    for serial, body in devices.items():
        profile = profiles.get(serial, PROFILE)
        if isinstance(profile, Exception):
            mock.get(f"{BASE_URL}/v3/devices/{serial}/profile", exc=profile)
        else:
            mock.get(f"{BASE_URL}/v3/devices/{serial}/profile", json=profile)
        if isinstance(body, Exception):
            mock.get(f"{BASE_URL}/v3/devices/{serial}", exc=body)
        else:
            mock.get(f"{BASE_URL}/v3/devices/{serial}", json=body)
    mock.post(f"{BASE_URL}/v3/devices/send-command", json={"devices": [SERIAL]})


async def _setup(hass: HomeAssistant, *, realtime: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={"username": "me@example.com", "password": "pw"},
        options={"realtime": realtime},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _commands(cloud):
    return [
        call[2]["commands"]
        for call in cloud.mock_calls
        if str(call[1]).endswith("/send-command")
    ]


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "climate", service, {"entity_id": ENTITY, **data}, blocking=True
    )
