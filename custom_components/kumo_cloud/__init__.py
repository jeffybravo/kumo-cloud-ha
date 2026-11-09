"""The Mitsubishi Kumo Cloud integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import KumoCloudApi
from .const import CONF_REALTIME, DEFAULT_REALTIME, DOMAIN
from .coordinator import KumoConfigEntry, KumoCoordinator
from .realtime import KumoRealtime

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: KumoConfigEntry) -> bool:
    api = KumoCloudApi(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = KumoCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    if entry.options.get(CONF_REALTIME, DEFAULT_REALTIME):
        coordinator.realtime = KumoRealtime(hass, coordinator, api)
        coordinator.realtime.start()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: KumoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KumoConfigEntry) -> bool:
    if realtime := entry.runtime_data.realtime:
        await realtime.stop()
        entry.runtime_data.realtime = None
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: KumoConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting a unit that is no longer on the Kumo Cloud account."""
    return not any(
        domain == DOMAIN and serial in entry.runtime_data.data
        for domain, serial in device.identifiers
    )
