# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from collections import deque

from abralia.interaction.errors import AmbiguousDeviceError, DeviceNotFoundError
from abralia.interaction.transport import (
    HidApiInteractionTransport,
    HidDeviceInfo,
    find_keychron_v3_8k_interface,
)


class FakeHidDevice:
    def __init__(self, reports: list[bytes]):
        self.reports = deque(reports)
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, report: bytes) -> int:
        self.writes.append(report)
        return len(report)

    def read(self, length: int, timeout_ms: int) -> list[int]:
        del length, timeout_ms
        return list(self.reports.popleft()) if self.reports else []

    def close(self) -> None:
        self.closed = True


def device(path: str) -> HidDeviceInfo:
    return HidDeviceInfo(
        path=path,
        vendor_id=0x3434,
        product_id=0x0F30,
        usage_page=0xFF60,
        usage=0x61,
        interface_number=1,
        product="Keychron V3 8K",
        manufacturer="Keychron",
        serial_number="",
    )


class InteractionTransportTests(unittest.TestCase):
    def test_transaction_preserves_unmatched_event_and_report_id(self) -> None:
        event = bytes([0xF0]) + bytes(31)
        response = bytes([0x08]) + bytes(31)
        fake = FakeHidDevice([event, response])
        transport = HidApiInteractionTransport(fake)

        received = transport.transact([0x08], lambda report: report[0] == 0x08)
        self.assertEqual(received, response)
        self.assertEqual(transport.pop_unmatched(), event)
        self.assertEqual(len(fake.writes[0]), 33)
        self.assertEqual(fake.writes[0][0], 0)
        transport.close()
        self.assertTrue(fake.closed)

    def test_device_discovery_rejects_zero_and_ambiguous_matches(self) -> None:
        with self.assertRaises(DeviceNotFoundError):
            find_keychron_v3_8k_interface([])
        with self.assertRaises(AmbiguousDeviceError):
            find_keychron_v3_8k_interface([device("a"), device("b")])
        self.assertEqual(find_keychron_v3_8k_interface([device("a")]).path, "a")


if __name__ == "__main__":
    unittest.main()
