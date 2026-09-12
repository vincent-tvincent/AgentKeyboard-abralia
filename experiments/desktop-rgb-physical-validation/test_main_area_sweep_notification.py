# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import unittest

from abralia.rgb import BLACK, Srgb8, load_profile
from main_area_notification_flash import DEFAULT_REGION, FLASH_COLOR, PEAK_BRIGHTNESS
from main_area_sweep_notification import (
    fade_scenes,
    region_positions,
    scaled_color,
    sweep_scenes,
    validate_args,
)


class MainAreaSweepNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")

    def test_default_sweep_moves_left_to_right(self) -> None:
        positions = region_positions(self.profile, DEFAULT_REGION)
        scenes = sweep_scenes(
            self.profile,
            DEFAULT_REGION,
            duration_ms=250,
            fps=30,
            band_width_u=5,
        )
        x_by_id = dict(positions)

        self.assertEqual(len(scenes), 8)
        first_lit = [
            element_id
            for element_id, color in scenes[0].payload.colors.items()
            if color != BLACK
        ]
        last_lit = [
            element_id
            for element_id, color in scenes[-1].payload.colors.items()
            if color != BLACK
        ]
        self.assertLess(
            sum(x_by_id[element_id] for element_id in first_lit) / len(first_lit),
            sum(x_by_id[element_id] for element_id in last_lit) / len(last_lit),
        )

    def test_peak_and_fade_use_one_yellow_green_color(self) -> None:
        fades = fade_scenes(
            self.profile,
            DEFAULT_REGION,
            duration_ms=150,
            fps=30,
        )

        self.assertEqual(PEAK_BRIGHTNESS, 255)
        self.assertEqual(FLASH_COLOR, Srgb8(160, 255, 0))
        self.assertEqual(scaled_color(1), FLASH_COLOR)
        self.assertEqual(scaled_color(0), BLACK)
        self.assertTrue(
            all(color == BLACK for color in fades[-1].payload.colors.values())
        )

    def test_default_timing_is_short_and_wait_is_bounded(self) -> None:
        validate_args(
            argparse.Namespace(
                sweep_ms=250.0,
                pulse_ms=120.0,
                fade_ms=150.0,
                band_width_u=5.0,
                fps=30.0,
                minimum_wait_seconds=5.0,
                random_wait_max_seconds=30.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
