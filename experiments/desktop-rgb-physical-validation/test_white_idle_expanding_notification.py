# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import unittest

from abralia.rgb import Srgb8, load_profile
from main_area_notification_flash import DEFAULT_REGION, FLASH_COLOR, PEAK_BRIGHTNESS
from white_idle_expanding_notification import (
    edge_to_center_fill_scenes,
    expanding_notification_scenes,
    idle_scene,
    notification_base_value,
    notification_fade_scenes,
    notification_scene,
    percent_to_value,
    selected_wait_seconds,
    validate_args,
    white_transition_scenes,
)


class WhiteIdleExpandingNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")

    def test_default_idle_is_white_at_half_value(self) -> None:
        value = percent_to_value(50)
        scene = idle_scene(value, "test-idle")

        self.assertEqual(value, 128)
        self.assertEqual(scene.payload.background, Srgb8(128, 128, 128))
        self.assertEqual(scene.payload.colors, {})

    def test_idle_adjustment_does_not_reduce_notification_peak(self) -> None:
        for idle_percent in (10, 50, 100):
            base_value = notification_base_value(idle_percent)
            pulse = notification_scene(
                self.profile,
                DEFAULT_REGION,
                base_value,
                1.0,
                "test-pulse",
            )

            self.assertEqual(PEAK_BRIGHTNESS, 255)
            self.assertTrue(
                all(color == FLASH_COLOR for color in pulse.payload.colors.values())
            )

    def test_idle_lowers_to_ten_percent_then_restores(self) -> None:
        idle_value = percent_to_value(80)
        base_value = notification_base_value(80)
        lower = white_transition_scenes(
            idle_value,
            base_value,
            duration_ms=180,
            fps=30,
            scene_prefix="lower",
        )
        restore = white_transition_scenes(
            base_value,
            idle_value,
            duration_ms=180,
            fps=30,
            scene_prefix="restore",
        )

        self.assertEqual(base_value, 26)
        self.assertEqual(lower[-1].payload.background, Srgb8(26, 26, 26))
        self.assertEqual(restore[-1].payload.background, Srgb8(204, 204, 204))

    def test_expansion_and_fade_return_to_white_base(self) -> None:
        expansion = expanding_notification_scenes(
            self.profile,
            DEFAULT_REGION,
            26,
            duration_ms=350,
            fps=30,
            tail_decay=0.5,
        )
        fade = notification_fade_scenes(
            self.profile,
            DEFAULT_REGION,
            26,
            duration_ms=150,
            fps=30,
        )

        self.assertEqual(len(expansion), 11)
        self.assertTrue(
            all(
                color == Srgb8(26, 26, 26)
                for color in fade[-1].payload.colors.values()
            )
        )

    def test_expanding_front_leaves_a_visible_tail(self) -> None:
        expansion = expanding_notification_scenes(
            self.profile,
            DEFAULT_REGION,
            26,
            duration_ms=350,
            fps=30,
            tail_decay=0.5,
        )
        first_frame_ids = [
            element_id
            for element_id, color in expansion[0].payload.colors.items()
            if color == FLASH_COLOR
        ]
        element_id = first_frame_ids[0]

        self.assertEqual(expansion[1].payload.colors[element_id].green, 140)
        self.assertEqual(expansion[2].payload.colors[element_id].green, 83)

    def test_edge_fill_climbs_from_each_keys_current_tail_value(self) -> None:
        expansion = expanding_notification_scenes(
            self.profile,
            DEFAULT_REGION,
            26,
            duration_ms=350,
            fps=30,
            tail_decay=0.5,
        )
        starting_colors = expansion[-1].payload.colors
        fill = edge_to_center_fill_scenes(
            self.profile,
            DEFAULT_REGION,
            26,
            duration_ms=220,
            fps=30,
            starting_colors=starting_colors,
        )

        self.assertEqual(len(fill), 7)
        self.assertTrue(
            any(color != FLASH_COLOR for color in fill[0].payload.colors.values())
        )
        for element_id, starting_color in starting_colors.items():
            first_color = fill[0].payload.colors[element_id]
            self.assertGreaterEqual(first_color.red, starting_color.red)
            self.assertGreaterEqual(first_color.green, starting_color.green)
            self.assertLessEqual(first_color.blue, starting_color.blue)
        self.assertTrue(
            all(color == FLASH_COLOR for color in fill[-1].payload.colors.values())
        )

    def test_preview_option_uses_exactly_five_seconds(self) -> None:
        args = argparse.Namespace(
            preview_after_5s=True,
            minimum_wait_seconds=200.0,
            random_wait_max_seconds=30.0,
        )

        self.assertEqual(selected_wait_seconds(args), 5.0)

    def test_default_arguments_are_valid(self) -> None:
        validate_args(
            argparse.Namespace(
                idle_brightness_percent=50.0,
                expand_ms=350.0,
                edge_fill_ms=220.0,
                pulse_ms=100.0,
                notification_fade_ms=150.0,
                idle_transition_ms=180.0,
                flashes=3,
                flash_gap_ms=120.0,
                tail_decay=0.5,
                fps=30.0,
                minimum_wait_seconds=5.0,
                random_wait_max_seconds=30.0,
                preview_after_5s=False,
                post_seconds=3.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
