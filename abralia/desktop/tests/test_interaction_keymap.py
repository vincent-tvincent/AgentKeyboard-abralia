# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from collections import deque
from collections.abc import Callable, Sequence

from abralia.interaction.keymap import (
    ViaKeymapReader,
    parse_keycode,
)
from abralia.interaction.errors import KeycodeLookupError
from abralia.interaction.protocol import (
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


class FakeViaTransport:
    def __init__(self) -> None:
        self.layers = 2
        self.rows = 2
        self.columns = 2
        # Big-endian layer/row/column keycodes, matching VIA's keymap buffer.
        values = [
            0x0004,
            0x0004,
            0x0005,
            0x0000,
            0x0004,
            0x0000,
            0x0005,
            0x0000,
        ]
        self.raw = b"".join(value.to_bytes(2, "big") for value in values)
        self.encoder = {
            (0, 0, True): 0x0004,
            (0, 0, False): 0x0006,
            (1, 0, True): 0x0006,
            (1, 0, False): 0x0006,
        }
        self.unmatched: deque[bytes] = deque()

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        del timeout_ms
        packet = bytes(request)
        response = bytearray(32)
        response[0] = packet[0]
        if packet[0] == 0x11:
            response[1] = self.layers
        elif packet[0] == 0x12:
            offset = (packet[1] << 8) | packet[2]
            size = packet[3]
            response[4 : 4 + size] = self.raw[offset : offset + size]
        elif packet[0] == 0x14:
            value = self.encoder[(packet[1], packet[2], bool(packet[3]))]
            response[4:6] = value.to_bytes(2, "big")
        self.assert_match(response_matches, bytes(response))
        return bytes(response)

    @staticmethod
    def assert_match(matcher: Callable[[bytes], bool], response: bytes) -> None:
        if not matcher(response):
            raise AssertionError("Fake response did not match the transaction predicate.")

    def write(self, request: Sequence[int] | bytes) -> None:
        del request

    def read(self, timeout_ms: int) -> bytes:
        del timeout_ms
        return b""

    def pop_unmatched(self) -> bytes | None:
        return self.unmatched.popleft() if self.unmatched else None

    def close(self) -> None:
        pass


def capabilities() -> Capabilities:
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
        matrix_rows=2,
        matrix_columns=2,
        encoder_count=1,
        event_queue_capacity=32,
        total_control_slots=6,
        double_tap_window_ms=300,
        heartbeat_timeout_ms=4000,
        maximum_force_lease_ms=30000,
        supported_binding_flags=BindingFlags(7),
        supported_lifetimes=frozenset(Lifetime),
    )


class InteractionKeymapTests(unittest.TestCase):
    def test_symbolic_keycode_parser_accepts_basic_layer_and_numeric_values(self) -> None:
        self.assertEqual(parse_keycode("KC_A"), 0x0004)
        self.assertEqual(parse_keycode("KC_HOME"), 0x004A)
        self.assertEqual(parse_keycode("MO(3)"), 0x5223)
        self.assertEqual(parse_keycode("0x7E09"), 0x7E09)

    def test_keycode_resolution_returns_all_layers_controls_and_encoder_matches(self) -> None:
        reader = ViaKeymapReader(FakeViaTransport())
        matches = reader.resolve("KC_A", capabilities())
        self.assertEqual(len(matches), 4)
        self.assertEqual(
            set(reader.resolve_controls("KC_A", capabilities())),
            {
                ControlId.key(0, 0),
                ControlId.key(0, 1),
                ControlId.encoder_clockwise(0),
            },
        )
        self.assertEqual({match.layer for match in matches}, {0, 1})

    def test_kc_no_is_rejected_without_making_layout_assumptions(self) -> None:
        reader = ViaKeymapReader(FakeViaTransport())
        with self.assertRaisesRegex(KeycodeLookupError, "layout assumptions"):
            reader.resolve("KC_NO", capabilities())


if __name__ == "__main__":
    unittest.main()
