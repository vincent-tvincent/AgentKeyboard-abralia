# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from collections.abc import Callable, Sequence

from abralia.rgb.adapters.keychron_effect25 import (
    EFFECT_25,
    EXPECTED_LED_COUNT,
    EffectSelectionPolicy,
    FrameFlags,
    FrameOperation,
    FrameResult,
    FrameState,
    KeychronEffect25Adapter,
)
from abralia.rgb.colors import Hsv8, Srgb8
from abralia.rgb.errors import CapabilityError, EffectUnavailableError
from abralia.rgb.led_mapper import DeviceFrame, LedColor
from abralia.rgb.profiles import EncoderDirection, EncoderPosition
from abralia.rgb.transport import HidDeviceInfo


class FakeTransport:
    def __init__(self) -> None:
        self.effect = EFFECT_25
        self.brightness = 77
        self.per_key_type = 3
        self.colors = [Hsv8(index, 200, 100) for index in range(EXPECTED_LED_COUNT)]
        self.frame_state = FrameState.DIRECT
        self.active_sequence = 0
        self.active_valid = False
        self.pending_sequence = 0
        self.pending = False
        self.defer_commit_once = False
        self.keymap = [
            [[0 for _column in range(17)] for _row in range(6)] for _layer in range(4)
        ]
        self.encoder_map: dict[tuple[int, int, bool], int] = {}
        self.requests: list[list[int]] = []
        self.closed = False

    @staticmethod
    def _report(values: Sequence[int]) -> bytes:
        return bytes(values) + bytes(32 - len(values))

    def _flags(self) -> FrameFlags:
        flags = FrameFlags(0)
        if self.active_valid:
            flags |= FrameFlags.ACTIVE_VALID
        if self.pending:
            flags |= FrameFlags.PENDING_VALID
        if self.frame_state is FrameState.GUARDED and not self.pending:
            flags |= FrameFlags.BACK_BUFFER_FREE
        return flags

    def _status(self, command: int, result: FrameResult = FrameResult.OK) -> bytes:
        return self._report(
            [
                command,
                0,
                1,
                int(self.frame_state),
                self.active_sequence,
                self.pending_sequence,
                int(self._flags()),
                int(result),
            ]
        )

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        del timeout_ms
        values = list(request)
        self.requests.append(values)
        if values[0] == 0xA0:
            response = self._report([0xA0, 1, 0, 1])
        elif values == [0x11]:
            response = self._report([0x11, len(self.keymap)])
        elif values[0] == 0x12:
            offset = values[1] << 8 | values[2]
            size = values[3]
            encoded = bytes(
                component
                for layer in self.keymap
                for row in layer
                for keycode in row
                for component in (keycode >> 8, keycode & 0xFF)
            )
            response = self._report(
                [0x12, values[1], values[2], size, *encoded[offset : offset + size]]
            )
        elif values[0] == 0x14:
            keycode = self.encoder_map.get((values[1], values[2], bool(values[3])), 0)
            response = self._report([*values[:4], keycode >> 8, keycode & 0xFF])
        elif values[:3] == [0x08, 0x00, 0x01]:
            response = self._status(0x08)
            if self.pending:
                if self.defer_commit_once:
                    self.defer_commit_once = False
                else:
                    self.active_sequence = self.pending_sequence
                    self.active_valid = True
                    self.pending = False
        elif values[:3] == [0x07, 0x00, 0x01]:
            operation = FrameOperation(values[3])
            if self.effect != EFFECT_25:
                response = self._status(0x07, FrameResult.INVALID_STATE)
            elif operation is FrameOperation.AWAIT:
                self.frame_state = FrameState.AWAITING
                self.pending = False
                self.active_valid = False
            elif operation is FrameOperation.DIRECT:
                self.frame_state = FrameState.DIRECT
            elif operation is FrameOperation.BEGIN:
                self.frame_state = FrameState.GUARDED
            else:
                self.pending_sequence = values[4]
                self.pending = True
                self.defer_commit_once = True
            if self.effect == EFFECT_25:
                response = self._status(0x07)
        elif values[:3] == [0x08, 0x03, 0x02]:
            response = self._report([0x08, 0x03, 0x02, self.effect])
        elif values[:3] == [0x08, 0x03, 0x01]:
            response = self._report([0x08, 0x03, 0x01, self.brightness])
        elif values[:3] == [0x07, 0x03, 0x02]:
            self.effect = values[3]
            self.frame_state = FrameState.AWAITING
            self.pending = False
            self.active_valid = False
            response = self._report(values)
        elif values[:3] == [0x07, 0x03, 0x01]:
            self.brightness = values[3]
            response = self._report(values)
        elif values[:2] == [0xA8, 0x05]:
            response = self._report([0xA8, 0x05, 0, EXPECTED_LED_COUNT])
        elif values[:2] == [0xA8, 0x07]:
            response = self._report([0xA8, 0x07, 0, self.per_key_type])
        elif values[:2] == [0xA8, 0x08]:
            self.per_key_type = values[2]
            response = self._report([0xA8, 0x08, 0])
        elif values[:2] == [0xA8, 0x09]:
            start, count = values[2:4]
            payload = [
                component
                for color in self.colors[start : start + count]
                for component in (color.hue, color.saturation, color.value)
            ]
            response = self._report([0xA8, 0x09, 0, *payload])
        elif values[:2] == [0xA8, 0x0A]:
            start, count = values[2:4]
            for offset in range(count):
                base = 4 + offset * 3
                self.colors[start + offset] = Hsv8(*values[base : base + 3])
            response = self._report([0xA8, 0x0A, 0])
        else:
            raise AssertionError(f"Unexpected request {values!r}")
        if not response_matches(response):
            raise AssertionError(
                f"Response did not match request {values!r}: {response!r}"
            )
        return response

    def pop_unmatched(self) -> bytes | None:
        return None

    def close(self) -> None:
        self.closed = True


DEVICE = HidDeviceInfo(
    path=b"test",
    vendor_id=0x3434,
    product_id=0x0F30,
    usage_page=0xFF60,
    usage=0x61,
    interface_number=1,
    product="test",
    manufacturer="test",
    serial_number="test",
)


class KeychronAdapterTests(unittest.TestCase):
    @staticmethod
    def _frame() -> DeviceFrame:
        return DeviceFrame(
            tuple(
                LedColor(index, Srgb8(255, 0, 0) if index == 0 else Srgb8(0, 0, 0))
                for index in range(EXPECTED_LED_COUNT)
            )
        )

    def test_guarded_submit_waits_for_active_valid_even_for_sequence_zero(self) -> None:
        transport = FakeTransport()
        adapter = KeychronEffect25Adapter(transport, DEVICE)
        before = adapter.snapshot()
        frame = DeviceFrame(
            tuple(
                LedColor(index, Srgb8(255, 0, 0) if index == 0 else Srgb8(0, 0, 0))
                for index in range(EXPECTED_LED_COUNT)
            )
        )

        sequence = adapter.submit_frame(frame, brightness_ceiling=128)

        self.assertEqual(sequence, 0)
        self.assertTrue(transport.active_valid)
        self.assertFalse(transport.pending)
        self.assertEqual(transport.brightness, 128)
        self.assertGreaterEqual(transport.requests.count([0x08, 0x00, 0x01]), 2)

        adapter.restore(before)
        self.assertEqual(transport.frame_state, FrameState.DIRECT)
        self.assertEqual(transport.brightness, 77)
        self.assertEqual(adapter.snapshot(), before)

    def test_capability_probe_is_cached(self) -> None:
        transport = FakeTransport()
        adapter = KeychronEffect25Adapter(transport, DEVICE)

        adapter.capabilities()
        adapter.capabilities()

        self.assertEqual(sum(request == [0xA0] for request in transport.requests), 1)

    def test_effect_selection_policies_preserve_standalone_auto_select(self) -> None:
        strict_transport = FakeTransport()
        strict_transport.effect = 23
        strict_transport.frame_state = FrameState.AWAITING
        strict = KeychronEffect25Adapter(
            strict_transport,
            DEVICE,
            effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
        )
        with self.assertRaises(EffectUnavailableError):
            strict.submit_frame(self._frame(), brightness_ceiling=128)
        self.assertEqual(strict_transport.effect, 23)
        self.assertNotIn([0x07, 0x03, 0x02, EFFECT_25], strict_transport.requests)

        automatic_transport = FakeTransport()
        automatic_transport.effect = 23
        automatic_transport.frame_state = FrameState.AWAITING
        automatic = KeychronEffect25Adapter(automatic_transport, DEVICE)
        automatic.submit_frame(self._frame(), brightness_ceiling=128)
        self.assertEqual(automatic_transport.effect, EFFECT_25)

    def test_strict_refresh_translates_effect_change_to_standby_signal(self) -> None:
        transport = FakeTransport()
        adapter = KeychronEffect25Adapter(
            transport,
            DEVICE,
            effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
        )
        adapter.submit_frame(self._frame(), brightness_ceiling=128)
        transport.effect = 1
        transport.frame_state = FrameState.AWAITING

        with self.assertRaises(EffectUnavailableError):
            adapter.refresh()

    def test_effect_preserving_restore_rebases_snapshot_without_mode_change(
        self,
    ) -> None:
        transport = FakeTransport()
        adapter = KeychronEffect25Adapter(transport, DEVICE)
        before = adapter.snapshot()
        adapter.submit_frame(self._frame(), brightness_ceiling=128)
        transport.effect = 23
        transport.frame_state = FrameState.AWAITING

        rebased = adapter.restore_preserving_effect(before)

        self.assertEqual(transport.effect, 23)
        self.assertEqual(transport.brightness, 77)
        self.assertEqual(transport.per_key_type, 3)
        self.assertEqual(rebased.payload.effect, 23)
        self.assertEqual(rebased.payload.colors, before.payload.colors)
        brightness_writes = [
            request[3]
            for request in transport.requests
            if request[:3] == [0x07, 0x03, 0x01]
        ]
        self.assertEqual(brightness_writes[-2:], [0, 77])

        transport.effect = EFFECT_25
        resumed = adapter.rebase_current_effect(rebased)
        self.assertEqual(resumed.payload.effect, EFFECT_25)
        self.assertEqual(resumed.payload.colors, before.payload.colors)

    def test_black_frame_preserves_nonzero_brightness_for_awaiting_recovery(
        self,
    ) -> None:
        transport = FakeTransport()
        adapter = KeychronEffect25Adapter(transport, DEVICE)
        frame = DeviceFrame(
            tuple(
                LedColor(index, Srgb8(0, 0, 0)) for index in range(EXPECTED_LED_COUNT)
            )
        )

        adapter.submit_frame(frame, brightness_ceiling=160)

        self.assertEqual(transport.brightness, 160)
        self.assertTrue(all(color.value == 0 for color in transport.colors))

        adapter.clear()
        self.assertEqual(transport.brightness, 160)

    def test_existing_guarded_session_is_not_preempted(self) -> None:
        transport = FakeTransport()
        transport.frame_state = FrameState.GUARDED
        adapter = KeychronEffect25Adapter(transport, DEVICE)

        with self.assertRaisesRegex(CapabilityError, "refusing to preempt"):
            adapter.snapshot()

    def test_live_via_matrix_and_encoder_reads_are_current_and_batched(self) -> None:
        transport = FakeTransport()
        transport.keymap[0][2][2] = 0x0004
        transport.keymap[2][2][2] = 0x1234
        transport.encoder_map[(0, 0, True)] = 0x0080
        adapter = KeychronEffect25Adapter(transport, DEVICE)
        clockwise = EncoderPosition(0, EncoderDirection.CLOCKWISE)

        matrix = adapter.read_matrix_keycodes((0, 2), rows=6, columns=17)
        encoders = adapter.read_encoder_keycodes((0, 2), (clockwise,))

        self.assertEqual(adapter.keymap_layer_count(), 4)
        self.assertEqual(matrix[(0, 2, 2)], 0x0004)
        self.assertEqual(matrix[(2, 2, 2)], 0x1234)
        self.assertEqual(encoders[(0, clockwise)], 0x0080)
        self.assertEqual(encoders[(2, clockwise)], 0)
        self.assertEqual(sum(request[0] == 0x12 for request in transport.requests), 16)


if __name__ == "__main__":
    unittest.main()
