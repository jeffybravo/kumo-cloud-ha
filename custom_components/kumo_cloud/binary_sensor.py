"""Binary sensors for Kumo Cloud: filter warning and defrost."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KumoConfigEntry, KumoCoordinator
from .entity import KumoEntity, async_add_per_unit

# Keys of the device's displayConfig object, which drives the app's icons.
DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="filter",
        translation_key="filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="defrost",
        translation_key="defrost",
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def _factory(coordinator: KumoCoordinator, serial: str):
        display = coordinator.data[serial].get("displayConfig")
        if not isinstance(display, dict):
            return []
        return [
            KumoDisplayFlag(coordinator, serial, d)
            for d in DESCRIPTIONS
            if d.key in display
        ]

    async_add_per_unit(entry, async_add_entities, _factory)


class KumoDisplayFlag(KumoEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: KumoCoordinator,
        serial: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        display = self.device.get("displayConfig")
        if (
            not isinstance(display, dict)
            or display.get(self.entity_description.key) is None
        ):
            return None
        return bool(display[self.entity_description.key])
