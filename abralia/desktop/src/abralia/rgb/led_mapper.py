# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Physical element to device LED address mapper."""

from __future__ import annotations

from dataclasses import dataclass

from .colors import BLACK, Srgb8
from .errors import MappingError
from .profiles import LayoutProfile
from .scene import PhysicalFrame, ResolvedScene


@dataclass(frozen=True, slots=True)
class LedColor:
    address: int
    color: Srgb8


@dataclass(frozen=True, slots=True)
class DeviceFrame:
    leds: tuple[LedColor, ...]


class PhysicalElementLedMapper:
    def map(
        self, scene: ResolvedScene, profile: LayoutProfile
    ) -> tuple[PhysicalFrame, DeviceFrame]:
        selected = {visual.element_id: visual.color for visual in scene.visuals}
        colors = {
            element.element_id: selected.get(element.element_id, BLACK)
            for element in profile.rgb_elements
        }
        addresses = sorted(
            (
                LedColor(element.led_address, colors[element.element_id])
                for element in profile.rgb_elements
                if element.led_address is not None
            ),
            key=lambda item: item.address,
        )
        if [item.address for item in addresses] != list(
            range(profile.expected_led_count)
        ):
            raise MappingError(
                "Mapped frame does not cover every device LED exactly once."
            )
        return PhysicalFrame(colors), DeviceFrame(tuple(addresses))
