"""Shared base for Kumo Cloud entities."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KumoConfigEntry, KumoCoordinator


def unit_device_info(serial: str, device: dict[str, Any]) -> DeviceInfo:
    model = device.get("model") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=device.get("zoneName") or serial,
        manufacturer="Mitsubishi Electric",
        model=model.get("materialDescription") or device.get("modelNumber"),
        model_id=device.get("modelNumber"),
        serial_number=device.get("serialNumber"),
    )


@callback
def async_add_per_unit(
    entry: KumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[KumoCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every unit now, and for units that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [s for s in coordinator.data if s not in known]
        known.update(new)
        async_add_entities(e for s in new for e in factory(coordinator, s))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class KumoEntity(CoordinatorEntity[KumoCoordinator]):
    """An entity that belongs to one indoor unit."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KumoCoordinator, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_device_info = unit_device_info(serial, coordinator.data[serial])

    @property
    def device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._serial) or {}

    @property
    def available(self) -> bool:
        device = self.coordinator.data.get(self._serial)
        return (
            super().available
            and bool(device)
            and device.get("connected", True)
            and self.coordinator.reachable.get(self._serial, True)
        )
