# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from abralia.rgb.transport import HidApiTransport


class FakeHidDevice:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.reads = [bytes([0xB0]) + bytes(31), bytes([0xA8, 0x05, 0, 87]) + bytes(28)]
        self.closed = False

    def write(self, report: bytes) -> int:
        self.writes.append(report)
        return len(report)

    def read(self, length: int, timeout_ms: int) -> bytes:
        del length, timeout_ms
        return self.reads.pop(0)

    def close(self) -> None:
        self.closed = True


class TransportTests(unittest.TestCase):
    def test_transaction_preserves_unmatched_reports(self) -> None:
        device = FakeHidDevice()
        transport = HidApiTransport(device)

        response = transport.transact(
            [0xA8, 0x05],
            lambda report: report[:2] == bytes([0xA8, 0x05]),
        )

        self.assertEqual(response[3], 87)
        self.assertEqual(len(device.writes[0]), 33)
        self.assertEqual(device.writes[0][:3], bytes([0, 0xA8, 0x05]))
        self.assertEqual(transport.pop_unmatched(), bytes([0xB0]) + bytes(31))
        self.assertIsNone(transport.pop_unmatched())


if __name__ == "__main__":
    unittest.main()
