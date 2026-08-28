# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import struct
import unittest
from collections import deque
from collections.abc import Sequence

from abralia.interaction import (
    BindingPolicy,
    ControlId,
    Edge,
    EventFlags,
    EventType,
    ForceScope,
    HidApiInteractionTransport,
    HostInteractionController,
    HostInteractionProtocolClient,
    Lifetime,
    Opcode,
    ResetReason,
    Result,
    Routing,
    StatusFlags,
)


VIA_GET_LAYER_COUNT = 0x11
VIA_GET_KEYMAP_BUFFER = 0x12
VIA_GET_ENCODER = 0x14


class FakeRawHidFirmware:
    """Stateful 32-byte firmware simulator behind the real HID transport."""

    def __init__(self) -> None:
        self.session_token = 0
        self.binding_generation = 0
        self.force_generation = 0
        self.heartbeat_sequence = 0
        self.bindings: dict[int, int] = {}
        self.forced_controls: set[int] = set()
        self._staging_bindings: dict[int, int] = {}
        self._staging_forced: set[int] = set()
        self._force_scope = ForceScope.ALL_CONFIGURED
        self._responses: deque[bytes] = deque()
        self._pending_event: bytes | None = None
        self._event_deliveries = 0
        self.acknowledged_sequences: list[int] = []
        self.requests: list[bytes] = []
        self.closed = False

        # Two layers, a 2x2 matrix, and one encoder. KC_A deliberately appears
        # on two layers of one key, one other key, and encoder clockwise.
        self._keymap = bytes(
            [
                0x00,
                0x04,
                0x00,
                0x04,
                0x00,
                0x05,
                0x00,
                0x06,
                0x00,
                0x04,
                0x00,
                0x07,
                0x00,
                0x08,
                0x00,
                0x09,
            ]
        )
        self._encoders = {
            (0, 0, True): 0x0004,
            (0, 0, False): 0x0081,
            (1, 0, True): 0x0080,
            (1, 0, False): 0x0081,
        }

    def _common_response(self, request: bytes) -> bytearray:
        response = bytearray(32)
        response[:5] = request[:5]
        response[5] = Result.OK
        struct.pack_into("<I", response, 6, self.session_token)
        struct.pack_into("<H", response, 10, self.binding_generation)
        flags = StatusFlags.SESSION_VALID if self.session_token else StatusFlags(0)
        if self.forced_controls:
            flags |= StatusFlags.FORCE_SELECTED
        response[12] = int(flags)
        response[13] = len(self.bindings)
        response[14] = len(self.forced_controls)
        response[15] = int(self._pending_event is not None)
        response[16] = ResetReason.NONE
        struct.pack_into("<H", response, 17, self.force_generation)
        struct.pack_into("<H", response, 19, self.heartbeat_sequence)
        return response

    def _handle_via(self, request: bytes) -> bytes:
        opcode = request[0]
        response = bytearray(32)
        response[:4] = request[:4]
        if opcode == VIA_GET_LAYER_COUNT:
            response[1] = 2
        elif opcode == VIA_GET_KEYMAP_BUFFER:
            offset = (request[1] << 8) | request[2]
            size = request[3]
            response[4 : 4 + size] = self._keymap[offset : offset + size]
        elif opcode == VIA_GET_ENCODER:
            layer, index, clockwise = request[1], request[2], bool(request[3])
            keycode = self._encoders[(layer, index, clockwise)]
            struct.pack_into(">H", response, 4, keycode)
        else:
            raise AssertionError(f"Unexpected VIA opcode 0x{opcode:02X}.")
        return bytes(response)

    def _handle_host_interaction(self, request: bytes) -> bytes:
        opcode = Opcode(request[4])
        if opcode is Opcode.CLAIM_SESSION:
            self.session_token = struct.unpack_from("<I", request, 5)[0]
        elif opcode is Opcode.KEEPALIVE:
            self.heartbeat_sequence = struct.unpack_from("<H", request, 9)[0]
        elif opcode is Opcode.RELEASE_SESSION:
            self.session_token = 0
            self.binding_generation = 0
            self.force_generation = 0
            self.bindings.clear()
            self.forced_controls.clear()
            self._pending_event = None
        elif opcode is Opcode.BEGIN_BINDING_REPLACE:
            self._staging_bindings = {}
        elif opcode is Opcode.WRITE_BINDINGS:
            for index in range(request[17]):
                control_id, binding_id = struct.unpack_from(
                    "<HH", request, 18 + index * 4
                )
                self._staging_bindings[control_id] = binding_id
        elif opcode is Opcode.COMMIT_BINDINGS:
            self.binding_generation = struct.unpack_from("<H", request, 9)[0]
            self.bindings = dict(self._staging_bindings)
            self.forced_controls.clear()
        elif opcode is Opcode.CLEAR_BINDINGS:
            self.binding_generation = struct.unpack_from("<H", request, 9)[0]
            self.bindings.clear()
            self.forced_controls.clear()
        elif opcode is Opcode.BEGIN_FORCE_SCOPE:
            self._force_scope = ForceScope(request[13])
            self._staging_forced = set()
        elif opcode is Opcode.WRITE_FORCE_KEYS:
            count = request[11]
            self._staging_forced.update(
                struct.unpack_from(f"<{count}H", request, 12)
            )
        elif opcode is Opcode.COMMIT_FORCE_SCOPE:
            self.force_generation = struct.unpack_from("<H", request, 9)[0]
            self.forced_controls = (
                set(self.bindings)
                if self._force_scope is ForceScope.ALL_CONFIGURED
                else set(self._staging_forced)
            )
        elif opcode is Opcode.CLEAR_FORCE_SCOPE:
            self.forced_controls.clear()
        elif opcode is Opcode.ACK_EVENT:
            sequence = struct.unpack_from("<H", request, 9)[0]
            self.acknowledged_sequences.append(sequence)
            self._pending_event = None

        response = self._common_response(request)
        if opcode is Opcode.GET_CAPABILITIES:
            response[12:16] = bytes([2, 2, 1, 32])
            struct.pack_into("<HHHH", response, 16, 6, 300, 4000, 30000)
            response[24:26] = bytes([7, 7])
        return bytes(response)

    def write(self, report: bytes) -> int:
        if len(report) != 33 or report[0] != 0:
            raise AssertionError("Expected a report ID followed by 32 payload bytes.")
        request = report[1:]
        self.requests.append(request)
        is_host_interaction = (
            request[0] in (0x07, 0x08)
            and request[1:4] == bytes([0x00, 0x02, 0x01])
        )
        response = (
            self._handle_host_interaction(request)
            if is_host_interaction
            else self._handle_via(request)
        )
        self._responses.append(response)
        return len(report)

    def read(self, length: int, timeout_ms: int) -> list[int]:
        del timeout_ms
        if length != 32:
            raise AssertionError("The host must request one 32-byte Raw HID report.")
        if self._responses:
            return list(self._responses.popleft())
        if self._pending_event is None:
            return []
        self._event_deliveries += 1
        report = bytearray(self._pending_event)
        if self._event_deliveries > 1:
            report[18] |= EventFlags.RETRANSMISSION
        return list(report)

    def queue_control_event(
        self, *, sequence: int, binding_id: int, control_id: ControlId
    ) -> None:
        report = bytearray(32)
        report[:5] = bytes([0xF0, 0x00, 0x02, 0x01, EventType.CONTROL_EDGE])
        struct.pack_into(
            "<IHHHH",
            report,
            5,
            self.session_token,
            sequence,
            self.binding_generation,
            binding_id,
            int(control_id),
        )
        report[17] = Edge.DOWN
        report[18] = EventFlags.MIRRORED
        struct.pack_into("<I", report, 19, 1234)
        self._pending_event = bytes(report)
        self._event_deliveries = 0

    def close(self) -> None:
        self.closed = True


class HostInteractionEndToEndTests(unittest.TestCase):
    def test_live_keycode_binding_event_retry_heartbeat_and_release(self) -> None:
        firmware = FakeRawHidFirmware()
        transport = HidApiInteractionTransport(firmware)
        client = HostInteractionProtocolClient(transport)

        capabilities = client.get_capabilities()
        self.assertEqual(capabilities.total_control_slots, 6)
        client.claim_session(0x11223344)
        controller = HostInteractionController(client)

        policy = BindingPolicy(
            routing=Routing.MIRROR,
            lifetime=Lifetime.TTL,
            duration_ms=5000,
        )
        binding = controller.set_keycode_controls(
            "KC_A", binding_id=77, policy=policy
        )
        expected_controls = {
            ControlId.key(0, 0),
            ControlId.key(0, 1),
            ControlId.encoder_clockwise(0),
        }
        self.assertEqual(set(binding.controls), expected_controls)
        self.assertEqual(len(binding.keycode_matches), 4)
        self.assertEqual(firmware.bindings, {int(control): 77 for control in expected_controls})

        activation = controller.activate_keycode_controls("KC_A", lease_ms=2000)
        self.assertEqual(activation.scope, ForceScope.SELECTED)
        self.assertEqual(firmware.forced_controls, {int(control) for control in expected_controls})

        firmware.queue_control_event(
            sequence=9,
            binding_id=77,
            control_id=ControlId.key(0, 0),
        )
        first = client.read_event(0, acknowledge=False)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertNotIn(EventFlags.RETRANSMISSION, first.flags)

        retry = client.read_event(0)
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertIn(EventFlags.RETRANSMISSION, retry.flags)
        self.assertEqual(firmware.acknowledged_sequences, [9])

        client._next_heartbeat_at = 0
        self.assertEqual(client.service(timeout_ms=0), ())
        self.assertEqual(firmware.heartbeat_sequence, 1)

        controller.turn_off_all()
        self.assertEqual(client.session_token, 0)
        self.assertEqual(firmware.bindings, {})
        self.assertEqual(firmware.forced_controls, set())
        client.close()
        self.assertTrue(firmware.closed)


if __name__ == "__main__":
    unittest.main()
