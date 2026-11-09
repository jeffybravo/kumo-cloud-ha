"""The Mitsubishi Kumo Cloud integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import KumoCloudApi
from .const import DOMAIN
from .coordinator import KumoConfigEntry, KumoCoordinator

PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: KumoConfigEntry) -> bool:
    api = KumoCloudApi(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = KumoCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KumoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: KumoConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting a unit that is no longer on the Kumo Cloud account."""
    return not any(
        domain == DOMAIN and serial in entry.runtime_data.data
        for domain, serial in device.identifiers
    )
