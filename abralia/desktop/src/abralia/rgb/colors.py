# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Tagged public colors and canonical linear-RGB conversion."""

from __future__ import annotations

import colorsys
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

from .errors import ColorValidationError


def _byte(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ColorValidationError(f"{name} must be an integer in 0...255.")
    return value


@dataclass(frozen=True, slots=True)
class Srgb8:
    red: int
    green: int
    blue: int

    def __post_init__(self) -> None:
        _byte("red", self.red)
        _byte("green", self.green)
        _byte("blue", self.blue)

    def to_json(self) -> dict[str, int | str]:
        return {
            "space": "srgb",
            "red": self.red,
            "green": self.green,
            "blue": self.blue,
        }


@dataclass(frozen=True, slots=True)
class Hsv8:
    hue: int
    saturation: int
    value: int

    def __post_init__(self) -> None:
        _byte("hue", self.hue)
        _byte("saturation", self.saturation)
        _byte("value", self.value)

    def to_json(self) -> dict[str, int | str]:
        return {
            "space": "hsv",
            "hue": self.hue,
            "saturation": self.saturation,
            "value": self.value,
        }


Color: TypeAlias = Srgb8 | Hsv8
BLACK = Srgb8(0, 0, 0)


@dataclass(frozen=True, slots=True)
class LinearRgb:
    red: float
    green: float
    blue: float

    def __post_init__(self) -> None:
        for name, value in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ColorValidationError(
                    f"linear {name} must be finite and in 0...1."
                )


def parse_color(value: Color | Mapping[str, object]) -> Color:
    if isinstance(value, (Srgb8, Hsv8)):
        return value
    if not isinstance(value, Mapping):
        raise ColorValidationError("Color must be Srgb8, Hsv8, or a tagged object.")
    space = value.get("space")
    try:
        if space == "srgb":
            return Srgb8(
                _byte("red", value["red"]),
                _byte("green", value["green"]),
                _byte("blue", value["blue"]),
            )
        if space == "hsv":
            return Hsv8(
                _byte("hue", value["hue"]),
                _byte("saturation", value["saturation"]),
                _byte("value", value["value"]),
            )
    except (KeyError, TypeError) as error:
        raise ColorValidationError(f"Malformed {space!r} color.") from error
    raise ColorValidationError("Color space must be 'srgb' or 'hsv'.")


def to_srgb8(color: Color) -> Srgb8:
    if isinstance(color, Srgb8):
        return color
    red, green, blue = colorsys.hsv_to_rgb(
        color.hue / 256.0, color.saturation / 255.0, color.value / 255.0
    )
    return Srgb8(round(red * 255), round(green * 255), round(blue * 255))


def to_hsv8(color: Color) -> Hsv8:
    if isinstance(color, Hsv8):
        return color
    hue, saturation, value = colorsys.rgb_to_hsv(
        color.red / 255.0, color.green / 255.0, color.blue / 255.0
    )
    return Hsv8(round(hue * 255) & 0xFF, round(saturation * 255), round(value * 255))


def _to_linear_component(component: int) -> float:
    srgb = component / 255.0
    return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4


def _to_srgb_component(component: float) -> int:
    srgb = (
        12.92 * component
        if component <= 0.0031308
        else 1.055 * component ** (1 / 2.4) - 0.055
    )
    return round(min(1.0, max(0.0, srgb)) * 255)


def to_linear_rgb(color: Color) -> LinearRgb:
    srgb = to_srgb8(color)
    return LinearRgb(
        _to_linear_component(srgb.red),
        _to_linear_component(srgb.green),
        _to_linear_component(srgb.blue),
    )


def linear_to_srgb8(color: LinearRgb) -> Srgb8:
    return Srgb8(
        _to_srgb_component(color.red),
        _to_srgb_component(color.green),
        _to_srgb_component(color.blue),
    )


def weighted_average(colors: list[tuple[Color, float]]) -> Srgb8:
    positive = [
        (to_linear_rgb(color), weight) for color, weight in colors if weight > 0
    ]
    total = sum(weight for _color, weight in positive)
    if total <= 0:
        return BLACK
    return linear_to_srgb8(
        LinearRgb(
            sum(color.red * weight for color, weight in positive) / total,
            sum(color.green * weight for color, weight in positive) / total,
            sum(color.blue * weight for color, weight in positive) / total,
        )
    )
