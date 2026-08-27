from __future__ import annotations

import random
import unittest
from collections.abc import Callable, Sequence

from keychron_rgb_demo import (
    DemoError,
    FrameFlags,
    FrameOperation,
    FrameResult,
    FrameState,
    KeychronProtocol,
    PER_KEY_RGB_FRAME_VALUE_ID,
    V3_8K_ANSI,
)
from keychron_rgb_stress_test import random_frame


class FakeConnection:
    def __init__(self, responses: Sequence[bytes]):
        self.responses = list(responses)
        self.requests: list[list[int]] = []

    def transact(
        self,
        request: Sequence[int],
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        del timeout_ms
        self.requests.append(list(request))
        response = self.responses.pop(0)
        if not response_matches(response):
            raise AssertionError(f"Response did not match request: {response!r}")
        return response


def status_response(
    command: int,
    *,
    state: FrameState,
    active: int = 0,
    pending: int = 0,
    flags: FrameFlags = FrameFlags(0),
    result: FrameResult = FrameResult.OK,
) -> bytes:
    return bytes(
        [
            command,
            0x00,
            PER_KEY_RGB_FRAME_VALUE_ID,
            int(state),
            active,
            pending,
            int(flags),
            int(result),
        ]
        + [0] * 24
    )


class FrameProtocolTests(unittest.TestCase):
    def test_commit_packet_and_status(self) -> None:
        connection = FakeConnection(
            [
                status_response(
                    0x07,
                    state=FrameState.GUARDED,
                    active=6,
                    pending=7,
                    flags=FrameFlags.ACTIVE_VALID | FrameFlags.PENDING_VALID,
                )
            ]
        )
        protocol = KeychronProtocol(connection)  # type: ignore[arg-type]

        status = protocol.frame_control(FrameOperation.COMMIT, 7)

        self.assertEqual(connection.requests, [[0x07, 0x00, 0x01, 0x03, 0x07]])
        self.assertEqual(status.state, FrameState.GUARDED)
        self.assertEqual(status.active_sequence, 6)
        self.assertEqual(status.pending_sequence, 7)
        self.assertTrue(status.flags & FrameFlags.PENDING_VALID)

    def test_busy_result_can_be_inspected_or_raised(self) -> None:
        response = status_response(
            0x07,
            state=FrameState.GUARDED,
            pending=9,
            flags=FrameFlags.PENDING_VALID,
            result=FrameResult.BUSY,
        )
        connection = FakeConnection([response, response])
        protocol = KeychronProtocol(connection)  # type: ignore[arg-type]

        status = protocol.frame_control(
            FrameOperation.COMMIT, 10, require_ok=False
        )
        self.assertEqual(status.result, FrameResult.BUSY)

        with self.assertRaisesRegex(DemoError, "BUSY"):
            protocol.frame_control(FrameOperation.COMMIT, 10)

    def test_status_request_and_back_buffer_flag(self) -> None:
        connection = FakeConnection(
            [
                status_response(
                    0x08,
                    state=FrameState.GUARDED,
                    active=42,
                    flags=FrameFlags.ACTIVE_VALID
                    | FrameFlags.BACK_BUFFER_FREE,
                )
            ]
        )
        protocol = KeychronProtocol(connection)  # type: ignore[arg-type]

        status = protocol.frame_status()

        self.assertEqual(connection.requests, [[0x08, 0x00, 0x01]])
        self.assertTrue(status.back_buffer_free)
        self.assertFalse(status.transition_queued)

    def test_stock_descriptor_is_not_reclassified(self) -> None:
        self.assertEqual(V3_8K_ANSI.per_key_effect, 23)
        self.assertFalse(V3_8K_ANSI.true_per_key_value)

    def test_independent_value_frame_contains_off_and_full_keys(self) -> None:
        frame, _hues = random_frame(
            random.Random(7),
            V3_8K_ANSI.expected_led_count,
            previous_hues=None,
            independent_value=True,
        )
        values = {color.value for color in frame}
        self.assertIn(0, values)
        self.assertIn(255, values)
        self.assertTrue(any(0 < value < 255 for value in values))


if __name__ == "__main__":
    unittest.main()
