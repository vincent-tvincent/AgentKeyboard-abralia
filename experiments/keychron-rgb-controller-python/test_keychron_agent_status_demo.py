from __future__ import annotations

import unittest

from keychron_agent_status_demo import (
    AGENT_KEYS,
    STATUS_ORDER,
    STATUS_VISUALS,
    AgentStatus,
    desired_status_frame,
    normalize_for_effect_25,
    status_value,
    statuses_for_step,
)
from keychron_rgb_demo import HSV, V3_8K_ANSI


class AgentStatusDemoTests(unittest.TestCase):
    def test_f1_through_f12_use_first_row_led_indices(self) -> None:
        self.assertEqual(
            AGENT_KEYS,
            tuple((f"F{number}", number) for number in range(1, 13)),
        )

    def test_initial_gallery_contains_every_status_once(self) -> None:
        self.assertEqual(statuses_for_step(0), STATUS_ORDER)
        self.assertEqual(len(set(STATUS_ORDER)), len(AGENT_KEYS))

    def test_full_rotation_gives_every_agent_every_status(self) -> None:
        for agent_index in range(len(AGENT_KEYS)):
            observed = {
                statuses_for_step(step)[agent_index]
                for step in range(len(STATUS_ORDER))
            }
            self.assertEqual(observed, set(STATUS_ORDER))

    def test_only_f1_through_f12_are_lit(self) -> None:
        frame = desired_status_frame(
            statuses_for_step(0),
            elapsed_seconds=0.4,
        )
        lit_indices = {index for index, color in enumerate(frame) if color.value}
        self.assertEqual(lit_indices, set(range(1, 13)))
        self.assertEqual(len(frame), V3_8K_ANSI.expected_led_count)

    def test_steady_and_breathing_states_differ(self) -> None:
        self.assertEqual(
            status_value(AgentStatus.IDLE, 0.0),
            status_value(AgentStatus.IDLE, 9.0),
        )
        self.assertNotEqual(
            status_value(AgentStatus.RUNNING, 0.0),
            status_value(AgentStatus.RUNNING, 0.6),
        )
        self.assertLess(
            STATUS_VISUALS[AgentStatus.WAITING_APPROVAL].breathe_seconds,
            STATUS_VISUALS[AgentStatus.WAITING_USER].breathe_seconds,
        )

    def test_effect_25_normalization_preserves_ratios(self) -> None:
        desired = [HSV(0, 0, 0), HSV(10, 200, 32), HSV(90, 200, 128)]
        normalized, global_brightness = normalize_for_effect_25(desired, 96)

        self.assertEqual([color.value for color in normalized], [0, 64, 255])
        self.assertEqual(global_brightness, 48)

    def test_all_black_frame_keeps_global_brightness_zero(self) -> None:
        desired = [HSV(0, 0, 0) for _ in range(87)]
        normalized, global_brightness = normalize_for_effect_25(desired, 96)
        self.assertEqual(normalized, desired)
        self.assertEqual(global_brightness, 0)


if __name__ == "__main__":
    unittest.main()
