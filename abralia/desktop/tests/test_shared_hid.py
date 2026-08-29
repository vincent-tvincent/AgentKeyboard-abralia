# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import struct
import threading
import time
import unittest
from collections import deque
from collections.abc import Callable

from abralia.interaction import (
    Edge,
    EventType,
    HostInteractionProtocolClient,
    Opcode,
    Result,
)
from abralia.rgb.errors import TransportError as RgbTransportError
from abralia.rgb.transport import HidDeviceInfo
from abralia.shared_hid import SharedHidMode, SharedRawHidSession

DEVICE = HidDeviceInfo(
    path=b"shared-test",
    vendor_id=0x3434,
    product_id=0x0F30,
    usage_page=0xFF60,
    usage=0x61,
    interface_number=1,
    product="Keychron V3 8K",
    manufacturer="Keychron",
    serial_number="",
)


def padded(values: list[int] | bytes) -> bytes:
    return bytes(values) + bytes(32 - len(values))


def host_response(request: bytes, token: int) -> bytes:
    response = bytearray(32)
    response[:5] = request[:5]
    response[5] = Result.OK
    struct.pack_into("<I", response, 6, token)
    return bytes(response)


def control_event(token: int, sequence: int = 9) -> bytes:
    report = bytearray(32)
    report[:5] = bytes([0xF0, 0, 2, 1, EventType.CONTROL_EDGE])
    struct.pack_into("<IHHHH", report, 5, token, sequence, 1, 77, 0x0001)
    report[17] = Edge.DOWN
    return bytes(report)


class FakeSharedHidDevice:
    def __init__(self, handler: Callable[[bytes], bytes | None]):
        self.handler = handler
        self.reports: deque[bytes] = deque()
        self.before_response: deque[bytes] = deque()
        self.writes: list[bytes] = []
        self.close_count = 0
        self.fail_reads = False
        self.condition = threading.Condition()

    def write(self, report: bytes) -> int:
        request = report[1:]
        self.writes.append(request)
        response = self.handler(request)
        with self.condition:
            self.reports.extend(self.before_response)
            self.before_response.clear()
            if response is not None:
                self.reports.append(response)
            self.condition.notify_all()
        return len(report)

    def read(self, length: int, timeout_ms: int) -> list[int]:
        if length != 32:
            raise AssertionError("Expected a 32-byte Raw HID read.")
        if self.fail_reads:
            raise OSError("injected read failure")
        deadline = time.monotonic() + timeout_ms / 1000
        with self.condition:
            while not self.reports:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self.condition.wait(remaining)
            return list(self.reports.popleft())

    def close(self) -> None:
        self.close_count += 1
        with self.condition:
            self.condition.notify_all()


class SharedHidTests(unittest.TestCase):
    def test_rgb_transaction_preserves_event_for_interaction_ack(self) -> None:
        token = 0x11223344
        acknowledged: list[int] = []

        def handler(request: bytes) -> bytes:
            if request[:2] == bytes([0xA8, 0x05]):
                return padded([0xA8, 0x05, 0, 87])
            if request[4] == Opcode.ACK_EVENT:
                acknowledged.append(struct.unpack_from("<H", request, 9)[0])
                return host_response(request, token)
            raise AssertionError(f"Unexpected request {request!r}")

        for mode in SharedHidMode:
            with self.subTest(mode=mode):
                device = FakeSharedHidDevice(handler)
                device.before_response.append(control_event(token))
                with SharedRawHidSession(device, DEVICE, mode=mode) as session:
                    rgb = session.rgb_transport()
                    interaction = session.interaction_transport()
                    protocol = HostInteractionProtocolClient(interaction)
                    protocol.session_token = token

                    response = rgb.transact(
                        [0xA8, 0x05],
                        lambda report: report[:2] == bytes([0xA8, 0x05]),
                    )
                    self.assertEqual(response[3], 87)
                    event = protocol.read_event(50)
                    self.assertIsNotNone(event)
                    assert event is not None
                    self.assertEqual(event.binding_id, 77)
                    self.assertEqual(acknowledged[-1], 9)

                self.assertEqual(device.close_count, 1)

    def test_borrowed_view_close_does_not_close_physical_handle(self) -> None:
        device = FakeSharedHidDevice(lambda request: padded([request[0]]))
        session = SharedRawHidSession(device, DEVICE)

        session.rgb_transport().close()
        session.interaction_transport().close()
        self.assertEqual(device.close_count, 0)

        session.close()
        session.close()
        self.assertEqual(device.close_count, 1)

    def test_cooperative_zero_timeout_read_never_calls_blocking_hid_read(self) -> None:
        device = FakeSharedHidDevice(lambda _request: None)
        device.fail_reads = True
        with SharedRawHidSession(device, DEVICE) as session:
            self.assertEqual(session.interaction_transport().read(0), b"")

    def test_timeout_and_queue_overflow_are_errors_in_both_modes(self) -> None:
        for mode in SharedHidMode:
            with self.subTest(mode=mode, case="timeout"):
                device = FakeSharedHidDevice(lambda _request: None)
                with (
                    SharedRawHidSession(device, DEVICE, mode=mode) as session,
                    self.assertRaisesRegex(RgbTransportError, "Timed out"),
                ):
                    session.rgb_transport().transact(
                        [0xA0], lambda report: report[0] == 0xA0, timeout_ms=30
                    )

            with self.subTest(mode=mode, case="overflow"):
                device = FakeSharedHidDevice(lambda request: padded([request[0]]))
                device.before_response.extend([padded([0xF0]), padded([0xF0])])
                session = SharedRawHidSession(
                    device, DEVICE, mode=mode, unmatched_capacity=1
                )
                try:
                    with self.assertRaisesRegex(RgbTransportError, "overflowed"):
                        session.rgb_transport().transact(
                            [0xA0], lambda report: report[0] == 0xA0
                        )
                finally:
                    session.close()

    def test_threaded_reader_error_wakes_transactions(self) -> None:
        device = FakeSharedHidDevice(lambda _request: None)
        device.fail_reads = True
        with (
            SharedRawHidSession(device, DEVICE, mode=SharedHidMode.THREADED) as session,
            self.assertRaisesRegex(RgbTransportError, "read failed"),
        ):
            session.rgb_transport().transact([0xA0], lambda report: report[0] == 0xA0)

    def test_transactions_are_serialized_in_threaded_mode(self) -> None:
        response_gate = threading.Event()
        first_write = threading.Event()

        def handler(request: bytes) -> bytes:
            if request[0] == 1:
                first_write.set()
                response_gate.wait(1)
            return padded([request[0]])

        device = FakeSharedHidDevice(handler)
        with SharedRawHidSession(
            device, DEVICE, mode=SharedHidMode.THREADED
        ) as session:
            results: list[int] = []

            def transact(value: int) -> None:
                response = session.rgb_transport().transact(
                    [value], lambda report, expected=value: report[0] == expected
                )
                results.append(response[0])

            first = threading.Thread(target=transact, args=(1,))
            second = threading.Thread(target=transact, args=(2,))
            first.start()
            self.assertTrue(first_write.wait(1))
            second.start()
            time.sleep(0.02)
            self.assertEqual([request[0] for request in device.writes], [1])
            response_gate.set()
            first.join(1)
            second.join(1)

        self.assertEqual(sorted(results), [1, 2])
        self.assertEqual([request[0] for request in device.writes], [1, 2])

    def test_stress_interleaved_rgb_status_heartbeat_and_events(self) -> None:
        token = 0x55667788
        acknowledged: list[int] = []

        def handler(request: bytes) -> bytes:
            if request[:3] == bytes([0x07, 0x00, 0x01]):
                return padded([0x07, 0x00, 0x01, 2, request[4], 0, 5, 0])
            if request[1:4] == bytes([0x00, 0x02, 0x01]):
                if request[4] == Opcode.ACK_EVENT:
                    acknowledged.append(struct.unpack_from("<H", request, 9)[0])
                return host_response(request, token)
            raise AssertionError(f"Unexpected request {request!r}")

        for mode in SharedHidMode:
            with self.subTest(mode=mode):
                device = FakeSharedHidDevice(handler)
                with SharedRawHidSession(device, DEVICE, mode=mode) as session:
                    rgb = session.rgb_transport()
                    protocol = HostInteractionProtocolClient(
                        session.interaction_transport()
                    )
                    protocol.session_token = token
                    for sequence in range(20):
                        if sequence % 5 == 0:
                            device.before_response.append(
                                control_event(token, sequence + 1)
                            )
                        rgb.transact(
                            [0x07, 0x00, 0x01, 3, sequence],
                            lambda report, expected=sequence: (
                                report[:4] == bytes([0x07, 0x00, 0x01, 2])
                                and report[4] == expected
                            ),
                        )
                        protocol.get_status()
                        protocol.keepalive(sequence + 1)
                        protocol.service(timeout_ms=0)

                self.assertEqual(acknowledged[-4:], [1, 6, 11, 16])


if __name__ == "__main__":
    unittest.main()
