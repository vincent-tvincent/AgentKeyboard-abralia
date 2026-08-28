# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from keychron_codex_logo_frame import (
    CHEVRON,
    CHEVRON_LEDS,
    UNDERSCORE,
    UNDERSCORE_LEDS,
    build_frame,
)
from keychron_rgb_demo import V3_8K_ANSI


class CodexLogoFrameTests(unittest.TestCase):
    def test_exact_logo_and_black_background(self) -> None:
        frame = build_frame()

        self.assertEqual(len(frame), V3_8K_ANSI.expected_led_count)
        self.assertEqual(
            {index for index, color in enumerate(frame) if color == CHEVRON},
            set(CHEVRON_LEDS),
        )
        self.assertEqual(
            {index for index, color in enumerate(frame) if color == UNDERSCORE},
            set(UNDERSCORE_LEDS),
        )
        self.assertEqual(
            sum(color.value == 0 for color in frame),
            V3_8K_ANSI.expected_led_count
            - len(CHEVRON_LEDS)
            - len(UNDERSCORE_LEDS),
        )


if __name__ == "__main__":
    unittest.main()
