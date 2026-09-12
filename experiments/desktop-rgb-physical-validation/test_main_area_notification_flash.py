# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import unittest

from abralia.rgb import BLACK, Srgb8, load_profile
from main_area_notification_flash import (
    DEFAULT_REGION,
    FLASH_COLOR,
    MINIMUM_WAIT_SECONDS,
    PEAK_BRIGHTNESS,
    RANDOM_WAIT_MAX_SECONDS,
    hold_scene,
    notification_delay,
    notification_scenes,
    validate_args,
)


class FixedRandom:
    def uniform(self, low: float, high: float) -> float:
        if (low, high) != (0.0, 30.0):
            raise AssertionError((low, high))
        return 12.5


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeLease:
    def __init__(self) -> None:
        self.refreshes = 0
        self.closed = False

    def refresh(self) -> None:
        self.refreshes += 1

    def close(self) -> None:
        self.closed = True


class FakeController:
    def __init__(self) -> None:
        self.lease = FakeLease()

    def display(self, scenes, *, brightness_ceiling: int):
        if not scenes or brightness_ceiling != 255:
            raise AssertionError((scenes, brightness_ceiling))
        return self.lease


class MainAreaNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")

    def test_main_region_is_yellow_green_at_maximum_peak(self) -> None:
        renderable, active, dark = notification_scenes(self.profile, DEFAULT_REGION)

        self.assertEqual(PEAK_BRIGHTNESS, 255)
        self.assertEqual(FLASH_COLOR, Srgb8(160, 255, 0))
        self.assertEqual(set(renderable), set(self.profile.regions[DEFAULT_REGION].elements))
        self.assertEqual(set(active.payload.colors), set(renderable))
        self.assertTrue(all(color == FLASH_COLOR for color in active.payload.colors.values()))
        self.assertEqual(active.payload.background, BLACK)
        self.assertEqual(dark.payload.colors, {})
        self.assertEqual(dark.payload.background, BLACK)

    def test_default_timing_is_one_short_bounded_flash(self) -> None:
        args = argparse.Namespace(
            flashes=1,
            on_ms=160.0,
            off_ms=120.0,
            minimum_wait_seconds=MINIMUM_WAIT_SECONDS,
            random_wait_max_seconds=RANDOM_WAIT_MAX_SECONDS,
        )

        validate_args(args)
        self.assertEqual(notification_delay(5.0, 30.0, rng=FixedRandom()), 17.5)

    def test_repeated_or_too_fast_flashing_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "1...3"):
            validate_args(
                argparse.Namespace(
                    flashes=4,
                    on_ms=160.0,
                    off_ms=120.0,
                    minimum_wait_seconds=5.0,
                    random_wait_max_seconds=30.0,
                )
            )
        with self.assertRaisesRegex(ValueError, "40...1000"):
            validate_args(
                argparse.Namespace(
                    flashes=1,
                    on_ms=20.0,
                    off_ms=120.0,
                    minimum_wait_seconds=5.0,
                    random_wait_max_seconds=30.0,
                )
            )

    def test_wait_window_cannot_be_shorter_or_wider_than_the_demo_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "5...300"):
            validate_args(
                argparse.Namespace(
                    flashes=1,
                    on_ms=160.0,
                    off_ms=120.0,
                    minimum_wait_seconds=4.9,
                    random_wait_max_seconds=30.0,
                )
            )
        with self.assertRaisesRegex(ValueError, "0...30"):
            validate_args(
                argparse.Namespace(
                    flashes=1,
                    on_ms=160.0,
                    off_ms=120.0,
                    minimum_wait_seconds=5.0,
                    random_wait_max_seconds=30.1,
                )
            )

    def test_long_black_wait_refreshes_the_guarded_lease(self) -> None:
        clock = FakeClock()
        controller = FakeController()

        hold_scene(
            controller,
            object(),
            5.1,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )

        self.assertGreaterEqual(controller.lease.refreshes, 5)
        self.assertTrue(controller.lease.closed)


if __name__ == "__main__":
    unittest.main()
