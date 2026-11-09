"""Climate entities for Kumo Cloud indoor units."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import KumoError
from .const import COMMAND_REFRESH_DELAY, DOMAIN
from .coordinator import KumoConfigEntry, KumoCoordinator

KUMO_TO_HVAC = {
    "off": HVACMode.OFF,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "vent": HVACMode.FAN_ONLY,
    "auto": HVACMode.HEAT_COOL,
}
HVAC_TO_KUMO = {v: k for k, v in KUMO_TO_HVAC.items()}

# HA wants snake_case mode names so they can be translated.
KUMO_TO_FAN = {
    "superQuiet": "super_quiet",
    "quiet": "quiet",
    "low": "low",
    "powerful": "powerful",
    "superPowerful": "super_powerful",
    "auto": "auto",
}
FAN_TO_KUMO = {v: k for k, v in KUMO_TO_FAN.items()}
FAN_SPEEDS_BY_COUNT = {
    3: ["quiet", "low", "powerful"],
    4: ["quiet", "low", "powerful", "superPowerful"],
    5: ["superQuiet", "quiet", "low", "powerful", "superPowerful"],
}

KUMO_TO_SWING = {
    "auto": "auto",
    "horizontal": "horizontal",
    "midhorizontal": "mid_horizontal",
    "midpoint": "midpoint",
    "midvertical": "mid_vertical",
    "vertical": "vertical",
    "swing": "swing",
}
SWING_TO_KUMO = {v: k for k, v in KUMO_TO_SWING.items()}

# How long a sent command overrides polled state, so a poll that hasn't
# caught up yet doesn't flip the UI back to the old value.
OPTIMISTIC_SECONDS = 45

DEFAULT_MIN_TEMP = 10.0
DEFAULT_MAX_TEMP = 31.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KumoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [s for s in coordinator.data if s not in known]
        known.update(new)
        async_add_entities(KumoClimate(coordinator, s) for s in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _half(value: float) -> float:
    """Kumo units take setpoints in 0.5 °C steps."""
    return round(value * 2) / 2


class KumoClimate(CoordinatorEntity[KumoCoordinator], ClimateEntity):
    """One Mitsubishi indoor unit."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "kumo"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: KumoCoordinator, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._pending: dict[str, Any] = {}
        self._pending_until = 0.0
        self._cancel_refresh: CALLBACK_TYPE | None = None
        self._attr_unique_id = serial

        profile = coordinator.profiles.get(serial, {})
        self._profile = profile
        device = coordinator.data[serial]

        modes = [HVACMode.OFF, HVACMode.COOL]
        if profile.get("hasModeHeat", True):
            modes.append(HVACMode.HEAT)
        if profile.get("hasModeDry", True):
            modes.append(HVACMode.DRY)
        if profile.get("hasModeVent", True):
            modes.append(HVACMode.FAN_ONLY)
        if profile.get("hasModeHeat", True):
            modes.append(HVACMode.HEAT_COOL)
        self._attr_hvac_modes = modes

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

        speeds = list(
            FAN_SPEEDS_BY_COUNT.get(
                profile.get("numberOfFanSpeeds"), FAN_SPEEDS_BY_COUNT[5]
            )
        )
        if profile.get("hasFanSpeedAuto", True):
            speeds.append("auto")
        self._attr_fan_modes = [KUMO_TO_FAN[s] for s in speeds]
        features |= ClimateEntityFeature.FAN_MODE

        swings: list[str] = []
        if profile.get("hasVaneDir"):
            swings += [
                "auto",
                "horizontal",
                "midhorizontal",
                "midpoint",
                "midvertical",
                "vertical",
            ]
        if profile.get("hasVaneSwing"):
            swings.append("swing")
        if swings:
            self._attr_swing_modes = [KUMO_TO_SWING[s] for s in swings]
            features |= ClimateEntityFeature.SWING_MODE

        self._base_features = features

        model = device.get("model") or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=device.get("zoneName") or serial,
            manufacturer="Mitsubishi Electric",
            model=model.get("materialDescription") or device.get("modelNumber"),
            model_id=device.get("modelNumber"),
            serial_number=device.get("serialNumber"),
        )

    # ---- state helpers -------------------------------------------------

    def _get(self, key: str) -> Any:
        if key in self._pending and time.monotonic() < self._pending_until:
            return self._pending[key]
        return self.coordinator.data.get(self._serial, {}).get(key)

    @property
    def _mode(self) -> str:
        if self._get("power") == 0:
            return "off"
        return self._get("operationMode") or "off"

    @property
    def _single_auto_setpoint(self) -> bool:
        return self._get("spAuto") is not None

    def _limit(self, kind: str, mode: str) -> float | None:
        key = "cool" if mode == "dry" else mode
        return (self._profile.get(kind) or {}).get(key)

    @property
    def supported_features(self) -> ClimateEntityFeature:
        # Units with one auto setpoint (spAuto) have no heat/cool range.
        if HVACMode.HEAT_COOL in self.hvac_modes and not self._single_auto_setpoint:
            return self._base_features | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        return self._base_features

    @property
    def available(self) -> bool:
        device = self.coordinator.data.get(self._serial)
        return super().available and bool(device) and device.get("connected", True)

    # ---- climate properties --------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        return self._get("roomTemp")

    @property
    def current_humidity(self) -> float | None:
        return self._get("humidity")

    @property
    def hvac_mode(self) -> HVACMode:
        return KUMO_TO_HVAC.get(self._mode, HVACMode.OFF)

    @property
    def target_temperature(self) -> float | None:
        mode = self._mode
        if mode == "heat":
            return self._get("spHeat")
        if mode in ("cool", "dry"):
            return self._get("spCool")
        if mode == "auto" and self._single_auto_setpoint:
            return self._get("spAuto")
        return None

    @property
    def target_temperature_low(self) -> float | None:
        if self._mode == "auto" and not self._single_auto_setpoint:
            return self._get("spHeat")
        return None

    @property
    def target_temperature_high(self) -> float | None:
        if self._mode == "auto" and not self._single_auto_setpoint:
            return self._get("spCool")
        return None

    @property
    def min_temp(self) -> float:
        mode = self._mode
        if mode == "auto" and not self._single_auto_setpoint:
            mode = "heat"
        return self._limit("minimumSetPoints", mode) or DEFAULT_MIN_TEMP

    @property
    def max_temp(self) -> float:
        mode = self._mode
        if mode == "auto" and not self._single_auto_setpoint:
            mode = "cool"
        return self._limit("maximumSetPoints", mode) or DEFAULT_MAX_TEMP

    @property
    def fan_mode(self) -> str | None:
        return KUMO_TO_FAN.get(self._get("fanSpeed"))

    @property
    def swing_mode(self) -> str | None:
        return KUMO_TO_SWING.get(self._get("airDirection"))

    # ---- coordinator / lifecycle ---------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        # Drop optimistic values the cloud has confirmed, so later changes made
        # elsewhere (remote, wall controller) show up straight away.
        device = self.coordinator.data.get(self._serial) or {}
        self._pending = {k: v for k, v in self._pending.items() if device.get(k) != v}
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_refresh:
            self._cancel_refresh()
            self._cancel_refresh = None
        await super().async_will_remove_from_hass()

    # ---- commands ------------------------------------------------------

    async def _send(self, commands: dict[str, Any]) -> None:
        try:
            await self.coordinator.api.send_command(self._serial, commands)
        except KumoError as err:
            raise HomeAssistantError(f"Kumo Cloud rejected the command: {err}") from err

        if time.monotonic() >= self._pending_until:
            self._pending = {}
        self._pending.update(commands)
        if "operationMode" in commands:
            self._pending["power"] = 0 if commands["operationMode"] == "off" else 1
        self._pending_until = time.monotonic() + OPTIMISTIC_SECONDS
        self.async_write_ha_state()

        async def _refresh(_now: Any) -> None:
            self._cancel_refresh = None
            await self.coordinator.async_request_refresh()

        if self._cancel_refresh:
            self._cancel_refresh()
        self._cancel_refresh = async_call_later(
            self.hass, COMMAND_REFRESH_DELAY, _refresh
        )

    def _clamp(self, value: float, kind: str) -> float:
        low = self._limit("minimumSetPoints", kind) or DEFAULT_MIN_TEMP
        high = self._limit("maximumSetPoints", kind) or DEFAULT_MAX_TEMP
        return min(max(_half(value), low), high)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._send({"operationMode": HVAC_TO_KUMO[hvac_mode]})

    async def async_turn_on(self) -> None:
        previous = self._get("previousOperationMode")
        if not previous or previous == "off":
            previous = "auto" if HVACMode.HEAT_COOL in self.hvac_modes else "cool"
        await self._send({"operationMode": previous})

    async def async_turn_off(self) -> None:
        await self._send({"operationMode": "off"})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        commands: dict[str, Any] = {}
        mode = self._mode
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            mode = HVAC_TO_KUMO[hvac_mode]
            commands["operationMode"] = mode

        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            if mode == "heat":
                commands["spHeat"] = self._clamp(temp, "heat")
            elif mode == "auto":
                if not self._single_auto_setpoint:
                    raise ServiceValidationError(
                        "This unit uses a heat/cool range in auto mode; "
                        "set target_temp_low and target_temp_high instead"
                    )
                commands["spAuto"] = self._clamp(temp, "auto")
            else:
                # Cool and dry use the cooling setpoint; for off/fan, pre-set it.
                commands["spCool"] = self._clamp(temp, "cool")

        if (low := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
            commands["spHeat"] = self._clamp(low, "heat")
        if (high := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
            commands["spCool"] = self._clamp(high, "cool")

        if commands:
            await self._send(commands)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._send({"fanSpeed": FAN_TO_KUMO[fan_mode]})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self._send({"airDirection": SWING_TO_KUMO[swing_mode]})
