"""Sensors for Kumo Cloud: room temperature, humidity, Wi-Fi, outdoor temp."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KumoConfigEntry, KumoCoordinator
from .entity import KumoEntity, async_add_per_unit


@dataclass(frozen=True, kw_only=True)
class KumoSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]


UNIT_SENSORS = (
    KumoSensorDescription(
        key="room_temperature",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=lambda d: d.get("roomTemp"),
    ),
    KumoSensorDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value=lambda d: d.get("humidity"),
    ),
    KumoSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda d: d.get("rssi"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def _unit_sensors(coordinator: KumoCoordinator, serial: str):
        device = coordinator.data[serial]
        # Only create a sensor if the unit reports that value at all.
        return [
            KumoUnitSensor(coordinator, serial, description)
            for description in UNIT_SENSORS
            if description.value(device) is not None
        ]

    async_add_per_unit(entry, async_add_entities, _unit_sensors)

    coordinator = entry.runtime_data
    async_add_entities(
        KumoOutdoorTemperature(coordinator, site_id)
        for site_id in coordinator.site_ids
        if site_id in coordinator.weather
    )


class KumoUnitSensor(KumoEntity, SensorEntity):
    entity_description: KumoSensorDescription

    def __init__(
        self,
        coordinator: KumoCoordinator,
        serial: str,
        description: KumoSensorDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.device)


class KumoOutdoorTemperature(CoordinatorEntity[KumoCoordinator], SensorEntity):
    """Outdoor temperature at the site, from the weather service Kumo Cloud uses.

    This is a weather report for the site's location, not the outdoor unit's
    own sensor.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "outdoor_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: KumoCoordinator, site_id: str) -> None:
        super().__init__(coordinator)
        self._site_id = site_id
        self._attr_unique_id = f"site_{site_id}_outdoor_temperature"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"site_{site_id}")},
            name=coordinator.site_names.get(site_id) or "Kumo Cloud site",
            manufacturer="Mitsubishi Electric",
            model="Kumo Cloud site",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | None:
        weather = self.coordinator.weather.get(self._site_id) or {}
        return (weather.get("main") or {}).get("temp")

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
