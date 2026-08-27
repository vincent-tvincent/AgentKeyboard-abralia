from __future__ import annotations

import argparse
import unittest

from keychron_rgb_fps_sweep import (
    parse_camera_size,
    parse_fraction,
    parse_rates,
    percentile,
    smooth_gradient_frame,
    summarize_rate,
)


class RgbFpsSweepTests(unittest.TestCase):
    def test_parse_rates(self) -> None:
        self.assertEqual(parse_rates("20, 30,62.5"), (20.0, 30.0, 62.5))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_rates("20,zero")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_rates("20,0")

    def test_parse_camera_size(self) -> None:
        self.assertEqual(parse_camera_size("1920X1080"), "1920x1080")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_size("1080p")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_camera_size("0x1080")

    def test_parse_fraction(self) -> None:
        self.assertAlmostEqual(parse_fraction("30000/1001"), 29.97002997)
        self.assertEqual(parse_fraction("30"), 30.0)

    def test_gradient_is_full_length_bounded_and_moves(self) -> None:
        first = smooth_gradient_frame(0.0, 87, 0.5)
        second = smooth_gradient_frame(0.1, 87, 0.5)
        self.assertEqual(len(first), 87)
        self.assertTrue(all(0 <= color.value <= 255 for color in first))
        self.assertTrue(all(color.value >= 64 for color in first))
        self.assertNotEqual(first, second)

    def test_nearest_rank_percentile(self) -> None:
        self.assertEqual(percentile([0.001, 0.002, 0.003, 0.004], 0.95), 0.004)
        self.assertEqual(percentile([], 0.95), 0.0)

    def test_summary_marks_sustained_and_saturated_rates(self) -> None:
        sustained = summarize_rate(
            50.0,
            frame_starts=[0.00, 0.02, 0.04, 0.06],
            update_durations=[0.010, 0.011, 0.010, 0.012],
            schedule_overruns=0,
        )
        saturated = summarize_rate(
            70.0,
            frame_starts=[0.000, 0.017, 0.034, 0.051],
            update_durations=[0.016, 0.017, 0.016, 0.017],
            schedule_overruns=3,
        )
        jittered = summarize_rate(
            50.0,
            frame_starts=[0.00, 0.02, 0.04, 0.06],
            update_durations=[0.010, 0.011, 0.025, 0.012],
            schedule_overruns=1,
        )
        self.assertTrue(sustained.sustained)
        self.assertEqual(sustained.verdict, "SUSTAINED")
        self.assertFalse(saturated.sustained)
        self.assertEqual(saturated.verdict, "SATURATED")
        self.assertEqual(jittered.verdict, "JITTERED")


if __name__ == "__main__":
    unittest.main()
