"""Celsius <-> Fahrenheit for units that only take 0.5 °C setpoints.

Kumo units store setpoints in 0.5 °C steps. Plain conversion turns a 72 °F
request into 22.2 °C, which the unit rounds to 22.0 °C and HA then shows as
71.6 °F. Instead we show setpoints as whole °F and pick, for each whole °F,
the 0.5 °C value that displays as exactly that number, so every whole-degree
setpoint round-trips.
"""

from __future__ import annotations

import math


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def setpoint_c_to_f(celsius: float) -> int:
    """A 0.5 °C setpoint as the whole °F the user sees."""
    return round_half_up(c_to_f(celsius))


def setpoint_f_to_c(fahrenheit: float) -> float:
    """The 0.5 °C setpoint that displays as `fahrenheit` (rounded to whole °F)."""
    target = round_half_up(fahrenheit)
    exact = (target - 32) * 5 / 9
    base = round(exact * 2) / 2
    candidates = [base + step / 2 for step in (-2, -1, 0, 1, 2)]
    matching = [c for c in candidates if setpoint_c_to_f(c) == target]
    return min(matching or candidates, key=lambda c: abs(c - exact))


def room_c_to_f(celsius: float) -> float:
    return round(c_to_f(celsius), 1)
