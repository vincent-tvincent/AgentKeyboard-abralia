# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import struct
import unittest

from host_interaction_protocol import (
    CAPTURE_DOWN_UP,
    HOST_INTERACTION_EVENT_GROUP,
    HOST_INTERACTION_VALUE_ID,
    BindingEntry,
    ControlKind,
    Edge,
    EventFlags,
    EventType,
    ForceScope,
    Lifetime,
    Opcode,
    ProtocolError,
    Result,
    StatusFlags,
    ack_event_packet,
    begin_binding_replace_packet,
    begin_force_scope_packet,
    claim_session_packet,
    commit_bindings_packet,
    control_id,
    decode_control_id,
    encoder_control,
    is_device_event,
    key_control,
    parse_device_event,
    parse_response,
    write_bindings_packet,
    write_force_keys_packet,
)


class HostInteractionProtocolTests(unittest.TestCase):
    def test_control_ids_cover_keys_and_encoder_directions(self) -> None:
        key = key_control(5, 16)
        clockwise = encoder_control(0, clockwise=True)
        counterclockwise = encoder_control(0, clockwise=False)

        self.assertEqual(decode_control_id(key), (ControlKind.KEY, 5, 16))
        self.assertEqual(decode_control_id(clockwise), (ControlKind.ENCODER_CW, 0, 0))
        self.assertEqual(
            decode_control_id(counterclockwise),
            (ControlKind.ENCODER_CCW, 0, 0),
        )

    def test_claim_and_binding_transaction_packets(self) -> None:
        token = 0x11223344
        generation = 17
        self.assertEqual(claim_session_packet(token)[5:9], b"\x44\x33\x22\x11")
        self.assertEqual(
            begin_binding_replace_packet(token, generation)[9:11], b"\x11\x00"
        )

        packet = write_bindings_packet(
            token,
            generation,
            flags=CAPTURE_DOWN_UP,
            lifetime=Lifetime.ONE_SHOT,
            duration_ms=30_000,
            entries=[
                BindingEntry(key_control(1, 15), 1001),
                BindingEntry(encoder_control(0, clockwise=True), 1002),
            ],
        )
        self.assertEqual(packet[:5], bytes([0x07, 0x00, 0x02, 0x02, 0x11]))
        self.assertEqual(packet[11], int(CAPTURE_DOWN_UP))
        self.assertEqual(packet[12], int(Lifetime.ONE_SHOT))
        self.assertEqual(struct.unpack_from("<I", packet, 13)[0], 30_000)
        self.assertEqual(packet[17], 2)
        self.assertEqual(struct.unpack_from("<HH", packet, 18), (0x010F, 1001))
        self.assertEqual(
            struct.unpack_from("<HH", packet, 22),
            (control_id(ControlKind.ENCODER_CW, 0), 1002),
        )
        self.assertEqual(commit_bindings_packet(token, generation)[4], 0x12)

    def test_invalid_binding_policy_is_rejected_offline(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "duration_ms=0"):
            write_bindings_packet(
                1,
                1,
                flags=CAPTURE_DOWN_UP,
                lifetime=Lifetime.SESSION,
                duration_ms=1,
                entries=[BindingEntry(key_control(1, 1), 1)],
            )
        with self.assertRaisesRegex(ProtocolError, "one to three"):
            write_bindings_packet(
                1,
                1,
                flags=CAPTURE_DOWN_UP,
                lifetime=Lifetime.SESSION,
                duration_ms=0,
                entries=[],
            )

    def test_force_packets_are_generation_and_lease_bound(self) -> None:
        packet = begin_force_scope_packet(
            0x11223344,
            binding_generation=17,
            force_generation=3,
            scope=ForceScope.SELECTED,
            lease_ms=30_000,
        )
        self.assertEqual(struct.unpack_from("<HH", packet, 9), (17, 3))
        self.assertEqual(packet[13], int(ForceScope.SELECTED))
        self.assertEqual(struct.unpack_from("<I", packet, 14)[0], 30_000)

        controls = [key_control(1, 1), encoder_control(0, clockwise=False)]
        write = write_force_keys_packet(0x11223344, 3, controls)
        self.assertEqual(write[11], 2)
        self.assertEqual(struct.unpack_from("<HH", write, 12), tuple(controls))

    def test_response_and_event_decoding(self) -> None:
        response = bytearray(32)
        response[:6] = bytes([0x07, 0x00, 0x02, 0x02, 0x12, Result.OK])
        struct.pack_into("<I", response, 6, 0x11223344)
        struct.pack_into("<H", response, 10, 17)
        response[12] = int(StatusFlags.SESSION_VALID | StatusFlags.MANUAL_ACTIVE)
        response[13:17] = bytes([4, 2, 1, 0])
        decoded = parse_response(bytes(response))
        self.assertEqual(decoded.opcode, Opcode.COMMIT_BINDINGS)
        self.assertEqual(decoded.binding_generation, 17)
        self.assertTrue(decoded.status_flags & StatusFlags.MANUAL_ACTIVE)

        event = bytearray(32)
        event[:5] = bytes(
            [
                HOST_INTERACTION_EVENT_GROUP,
                0x00,
                HOST_INTERACTION_VALUE_ID,
                0x02,
                EventType.CONTROL_EDGE,
            ]
        )
        struct.pack_into(
            "<IHHHH", event, 5, 0x11223344, 9, 17, 1001, key_control(1, 15)
        )
        event[17] = Edge.DOWN
        event[18] = int(EventFlags.CAPTURED)
        struct.pack_into("<I", event, 19, 12345)
        self.assertTrue(is_device_event(bytes(event)))
        decoded_event = parse_device_event(bytes(event))
        self.assertEqual(decoded_event.sequence, 9)
        self.assertEqual(decoded_event.binding_id, 1001)
        self.assertEqual(decoded_event.edge_or_state, Edge.DOWN)
        self.assertEqual(
            ack_event_packet(0x11223344, decoded_event.sequence)[9:11],
            b"\x09\x00",
        )


if __name__ == "__main__":
    unittest.main()
