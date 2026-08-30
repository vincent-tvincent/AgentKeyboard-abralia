# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from abralia.interaction import (
    BindingFlags,
    Capabilities,
    ControlId,
    Lifetime,
    Opcode,
    ResetReason,
    Response,
    Result,
    StatusFlags,
)
from inspect_controls import controls_from_capabilities, inspection_bindings


def capabilities(rows: int, columns: int, encoders: int) -> Capabilities:
    response = Response(
        verb=0x08,
        opcode=Opcode.GET_CAPABILITIES,
        result=Result.OK,
        session_token=0,
        binding_generation=0,
        status_flags=StatusFlags(0),
        binding_count=0,
        forced_control_count=0,
        queued_event_count=0,
        last_reset_reason=ResetReason.NONE,
        force_generation=0,
        heartbeat_sequence=0,
    )
    return Capabilities(
        response=response,
        matrix_rows=rows,
        matrix_columns=columns,
        encoder_count=encoders,
        event_queue_capacity=32,
        total_control_slots=rows * columns + encoders * 2,
        double_tap_window_ms=300,
        heartbeat_timeout_ms=4000,
        maximum_force_lease_ms=30000,
        supported_binding_flags=BindingFlags(7),
        supported_lifetimes=frozenset(Lifetime),
    )


class ControlInspectorTests(unittest.TestCase):
    def test_enumeration_uses_only_capabilities_and_excludes_reserved_pause(
        self,
    ) -> None:
        controls = controls_from_capabilities(
            capabilities(6, 17, 1), ControlId.key(0, 16)
        )
        self.assertEqual(len(controls), 103)
        self.assertNotIn(ControlId.key(0, 16), controls)
        self.assertIn(ControlId.key(5, 16), controls)
        self.assertIn(ControlId.encoder_clockwise(0), controls)
        self.assertIn(ControlId.encoder_counterclockwise(0), controls)

    def test_unique_binding_ids_use_mirror_down_only_session_policy(self) -> None:
        bindings = inspection_bindings(capabilities(2, 2, 1), ControlId.key(1, 1))
        self.assertEqual(
            [binding.entry.binding_id for binding in bindings], list(range(1, 6))
        )
        self.assertEqual(len({binding.entry.control_id for binding in bindings}), 5)
        for binding in bindings:
            self.assertEqual(binding.policy.lifetime, Lifetime.SESSION)
            self.assertEqual(
                binding.policy.flags,
                BindingFlags.MIRROR | BindingFlags.EVENT_DOWN,
            )


if __name__ == "__main__":
    unittest.main()
