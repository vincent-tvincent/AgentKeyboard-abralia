from __future__ import annotations

import unittest

from keychron_rgb_demo import HSV
from keychron_rgb_idle_halo import (
    HALO_PALETTE,
    LED_POSITIONS,
    apply_mixed_color_cutoff,
    breathing_envelope,
    halo_frame,
)


class IdleHaloMathTests(unittest.TestCase):
    def test_breathing_envelope(self) -> None:
        self.assertAlmostEqual(breathing_envelope(0.0), 0.0)
        self.assertAlmostEqual(breathing_envelope(0.25), 0.5)
        self.assertAlmostEqual(breathing_envelope(0.5), 1.0)
        self.assertAlmostEqual(breathing_envelope(0.75), 0.5)
        self.assertAlmostEqual(breathing_envelope(1.0), 0.0)

    def test_halo_uses_all_leds_and_keeps_background_black(self) -> None:
        frame = halo_frame(
            center_index=0,
            radius=60.0,
            power=2.0,
            palette_index=0,
        )
        self.assertEqual(len(frame), len(LED_POSITIONS))
        self.assertEqual(frame[0].value, 255)
        self.assertEqual(frame[86].value, 0)
        self.assertTrue(any(color.value == 0 for color in frame))

    def test_halo_brightness_decreases_with_distance(self) -> None:
        frame = halo_frame(
            center_index=0,
            radius=60.0,
            power=2.0,
            palette_index=0,
        )
        self.assertGreater(frame[1].value, frame[12].value)
        self.assertEqual(frame[12].value, 0)
        self.assertEqual(frame[86].value, 0)

    def test_higher_power_tightens_the_gradient(self) -> None:
        flat = halo_frame(0, radius=60.0, power=1.0, palette_index=0)
        focused = halo_frame(0, radius=60.0, power=2.0, palette_index=0)
        self.assertEqual(flat[0].value, focused[0].value)
        self.assertLess(focused[2].value, flat[2].value)

    def test_each_halo_uses_exactly_one_soft_palette_color(self) -> None:
        for palette_index, (_name, hue, saturation) in enumerate(HALO_PALETTE):
            frame = halo_frame(
                42,
                radius=60.0,
                power=2.0,
                palette_index=palette_index,
            )
            active_colors = {
                (color.hue, color.saturation)
                for color in frame
                if color.value > 0
            }
            self.assertEqual(active_colors, {(hue, saturation)})
            self.assertLess(saturation, 255)

        self.assertGreaterEqual(HALO_PALETTE[0][2], 220)
        self.assertGreaterEqual(HALO_PALETTE[3][2], 210)

    def test_mixed_color_cutoff_removes_low_effective_values(self) -> None:
        # At global brightness 64 these become final V 0, 4, 8, and 16.
        source = [
            HSV(4, 235, 0),
            HSV(4, 235, 16),
            HSV(4, 235, 32),
            HSV(4, 235, 64),
        ]
        gated = apply_mixed_color_cutoff(source, 64, 8)
        self.assertEqual([color.value for color in gated], [0, 0, 32, 64])


if __name__ == "__main__":
    unittest.main()
