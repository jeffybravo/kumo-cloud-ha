"""Climate entities for Kumo Cloud indoor units."""

from __future__ import annotations

import asyncio
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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import const
from .api import KumoError
from .const import COMMAND_REFRESH_DELAY
from .coordinator import KumoConfigEntry, KumoCoordinator
from .entity import KumoEntity, async_add_per_unit
from .temperature import room_c_to_f, setpoint_c_to_f, setpoint_f_to_c

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
    async_add_per_unit(
        entry, async_add_entities, lambda c, serial: [KumoClimate(c, serial)]
    )


def _half(value: float) -> float:
    """Kumo units take setpoints in 0.5 °C steps."""
    return round(value * 2) / 2


class KumoClimate(KumoEntity, ClimateEntity):
    """One Mitsubishi indoor unit."""

    _attr_name = None
    _attr_translation_key = "kumo"

    def __init__(self, coordinator: KumoCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._pending: dict[str, Any] = {}
        self._pending_until = 0.0
        self._queued: dict[str, Any] = {}
        self._generation = 0
        self._cancel_refresh: CALLBACK_TYPE | None = None
        self._attr_unique_id = serial

        # Fahrenheit users get whole-degree setpoints that map onto the unit's
        # 0.5 °C steps exactly; see temperature.py.
        self._fahrenheit = (
            coordinator.hass.config.units.temperature_unit
            == UnitOfTemperature.FAHRENHEIT
        )
        if self._fahrenheit:
            self._attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
            self._attr_target_temperature_step = 1.0
        else:
            self._attr_temperature_unit = UnitOfTemperature.CELSIUS
            self._attr_target_temperature_step = 0.5

        profile = coordinator.profiles.get(serial, {})
        self._profile = profile

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

    # ---- state helpers -------------------------------------------------

    def _get(self, key: str) -> Any:
        if key in self._pending and time.monotonic() < self._pending_until:
            return self._pending[key]
        return self.device.get(key)

    def _show(self, celsius: float | None) -> float | None:
        """A setpoint in the entity's unit."""
        if celsius is None or not self._fahrenheit:
            return celsius
        return setpoint_c_to_f(celsius)

    def _to_c(self, value: float) -> float:
        """A requested setpoint, in the entity's unit, as Celsius."""
        return setpoint_f_to_c(value) if self._fahrenheit else value

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

    # ---- climate properties --------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        temp = self._get("roomTemp")
        if temp is None or not self._fahrenheit:
            return temp
        return room_c_to_f(temp)

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
            return self._show(self._get("spHeat"))
        if mode in ("cool", "dry"):
            return self._show(self._get("spCool"))
        if mode == "auto" and self._single_auto_setpoint:
            return self._show(self._get("spAuto"))
        return None

    @property
    def target_temperature_low(self) -> float | None:
        if self._mode == "auto" and not self._single_auto_setpoint:
            return self._show(self._get("spHeat"))
        return None

    @property
    def target_temperature_high(self) -> float | None:
        if self._mode == "auto" and not self._single_auto_setpoint:
            return self._show(self._get("spCool"))
        return None

    @property
    def min_temp(self) -> float:
        mode = self._mode
        if mode == "auto" and not self._single_auto_setpoint:
            mode = "heat"
        return self._show(self._limit("minimumSetPoints", mode) or DEFAULT_MIN_TEMP)

    @property
    def max_temp(self) -> float:
        mode = self._mode
        if mode == "auto" and not self._single_auto_setpoint:
            mode = "cool"
        return self._show(self._limit("maximumSetPoints", mode) or DEFAULT_MAX_TEMP)

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
        device = self.device
        self._pending = {k: v for k, v in self._pending.items() if device.get(k) != v}
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_refresh:
            self._cancel_refresh()
            self._cancel_refresh = None
        self._generation += 1  # drop any command still waiting to be sent
        await super().async_will_remove_from_hass()

    # ---- commands ------------------------------------------------------

    @callback
    def _set_optimistic(self, commands: dict[str, Any]) -> None:
        if time.monotonic() >= self._pending_until:
            self._pending = {}
        self._pending.update(commands)
        if "operationMode" in commands:
            self._pending["power"] = 0 if commands["operationMode"] == "off" else 1
        self._pending_until = time.monotonic() + OPTIMISTIC_SECONDS
        self.async_write_ha_state()

    async def _send(self, commands: dict[str, Any]) -> None:
        """Show the change now; send it once calls stop arriving.

        Calls within COMMAND_DEBOUNCE seconds are merged into one request. Only
        the call that sends waits for the cloud and sees its errors.
        """
        self._queued.update(commands)
        self._set_optimistic(commands)
        self._generation += 1
        generation = self._generation
        await asyncio.sleep(const.COMMAND_DEBOUNCE)
        if generation != self._generation:
            return  # superseded; a later call sends the merged commands

        queued, self._queued = self._queued, {}
        try:
            await self.coordinator.api.send_command(self._serial, queued)
        except KumoError as err:
            for key in queued:
                self._pending.pop(key, None)
            if "operationMode" in queued:
                self._pending.pop("power", None)
            self.async_write_ha_state()
            raise HomeAssistantError(f"Kumo Cloud rejected the command: {err}") from err

        async def _refresh(_now: Any) -> None:
            self._cancel_refresh = None
            realtime = self.coordinator.realtime
            if realtime and realtime.connected:
                # The unit pushes its new state over the socket; no REST needed.
                await realtime.async_request_status(self._serial)
            else:
                await self.coordinator.async_request_refresh()

        if self._cancel_refresh:
            self._cancel_refresh()
        self._cancel_refresh = async_call_later(
            self.hass, COMMAND_REFRESH_DELAY, _refresh
        )

    def _clamp(self, value: float, kind: str) -> float:
        """A requested setpoint (entity unit) as a valid Celsius command value."""
        low = self._limit("minimumSetPoints", kind) or DEFAULT_MIN_TEMP
        high = self._limit("maximumSetPoints", kind) or DEFAULT_MAX_TEMP
        return min(max(_half(self._to_c(value)), low), high)

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
