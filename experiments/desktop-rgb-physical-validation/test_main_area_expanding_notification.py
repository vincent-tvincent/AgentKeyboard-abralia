# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import unittest

from abralia.rgb import BLACK, load_profile
from main_area_expanding_notification import (
    expanding_scenes,
    normalized_region_points,
    temporal_tail_levels,
    validate_args,
)
from main_area_notification_flash import DEFAULT_REGION, FLASH_COLOR, PEAK_BRIGHTNESS


class MainAreaExpandingNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")

    def test_wave_expands_from_center_to_outer_keys(self) -> None:
        points = normalized_region_points(self.profile, DEFAULT_REGION)
        scenes = expanding_scenes(
            self.profile,
            DEFAULT_REGION,
            duration_ms=350,
            fps=30,
            tail_decay=0.5,
        )
        radius_by_id = dict(points)

        self.assertEqual(len(scenes), 11)
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
            sum(radius_by_id[element_id] for element_id in first_lit) / len(first_lit),
            sum(radius_by_id[element_id] for element_id in last_lit) / len(last_lit),
        )

    def test_animation_retains_maximum_yellow_green_peak(self) -> None:
        self.assertEqual(PEAK_BRIGHTNESS, 255)
        self.assertEqual((FLASH_COLOR.red, FLASH_COLOR.green, FLASH_COLOR.blue), (160, 255, 0))

    def test_each_movement_frame_halves_the_previous_keys_tail(self) -> None:
        frames = temporal_tail_levels(
            (("K1", 0.0), ("K2", 1.0), ("K3", 2.0)),
            steps=3,
            tail_decay=0.5,
        )

        self.assertEqual(frames[0], {"K1": 1.0, "K2": 0.0, "K3": 0.0})
        self.assertEqual(frames[1], {"K1": 0.5, "K2": 1.0, "K3": 0.0})
        self.assertEqual(frames[2], {"K1": 0.25, "K2": 0.5, "K3": 1.0})

    def test_default_duration_and_wait_contract_are_valid(self) -> None:
        validate_args(
            argparse.Namespace(
                expand_ms=350.0,
                pulse_ms=100.0,
                fade_ms=150.0,
                flashes=3,
                flash_gap_ms=120.0,
                tail_decay=0.5,
                fps=30.0,
                minimum_wait_seconds=5.0,
                random_wait_max_seconds=30.0,
            )
        )

        with self.assertRaisesRegex(ValueError, "1...3"):
            validate_args(
                argparse.Namespace(
                    expand_ms=350.0,
                    pulse_ms=100.0,
                    fade_ms=150.0,
                    flashes=4,
                    flash_gap_ms=120.0,
                    tail_decay=0.5,
                    fps=30.0,
                    minimum_wait_seconds=5.0,
                    random_wait_max_seconds=30.0,
                )
            )


if __name__ == "__main__":
    unittest.main()
