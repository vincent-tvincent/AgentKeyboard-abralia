# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import struct
import unittest

from abralia.interaction.protocol import (
    BindingEntry,
    BindingFlags,
    BindingPolicy,
    ControlId,
    Edge,
    EventFlags,
    EventType,
    ForceScope,
    Lifetime,
    Opcode,
    ProtocolError,
    ResetReason,
    Result,
    Routing,
    StatusFlags,
    ack_event_packet,
    begin_force_scope_packet,
    parse_capabilities,
    parse_device_event,
    parse_response,
    write_bindings_packet,
    write_force_controls_packet,
)


class InteractionProtocolTests(unittest.TestCase):
    def test_control_ids_cover_matrix_and_both_encoder_directions(self) -> None:
        self.assertEqual(int(ControlId.key(5, 16)), 0x0510)
        self.assertEqual(int(ControlId.encoder_clockwise(0)), 0x4000)
        self.assertEqual(int(ControlId.encoder_counterclockwise(0)), 0x8000)
        self.assertEqual(ControlId(0x010F).primary, 1)
        self.assertEqual(ControlId(0x010F).secondary, 15)

    def test_binding_and_force_packets_contain_every_policy_field(self) -> None:
        token = 0x11223344
        policy = BindingPolicy(
            routing=Routing.MIRROR,
            lifetime=Lifetime.ONE_SHOT,
            duration_ms=30_000,
            emit_down=True,
            emit_up=False,
        )
        packet = write_bindings_packet(
            token,
            17,
            policy,
            [BindingEntry(ControlId.key(1, 15), 1001)],
        )
        self.assertEqual(packet[:5], bytes([0x07, 0x00, 0x02, 0x02, 0x11]))
        self.assertEqual(packet[11], int(BindingFlags.MIRROR | BindingFlags.EVENT_DOWN))
        self.assertEqual(packet[12], Lifetime.ONE_SHOT)
        self.assertEqual(struct.unpack_from("<I", packet, 13)[0], 30_000)
        self.assertEqual(struct.unpack_from("<HH", packet, 18), (0x010F, 1001))

        begin = begin_force_scope_packet(
            token,
            binding_generation=17,
            force_generation=9,
            scope=ForceScope.SELECTED,
            lease_ms=12_345,
        )
        self.assertEqual(struct.unpack_from("<HH", begin, 9), (17, 9))
        self.assertEqual(begin[13], ForceScope.SELECTED)
        self.assertEqual(struct.unpack_from("<I", begin, 14)[0], 12_345)

        force = write_force_controls_packet(
            token,
            9,
            [ControlId.key(1, 15), ControlId.encoder_clockwise(0)],
        )
        self.assertEqual(force[11], 2)
        self.assertEqual(struct.unpack_from("<HH", force, 12), (0x010F, 0x4000))

    def test_common_status_response_preserves_all_implemented_payload(self) -> None:
        report = bytearray(32)
        report[:6] = bytes([0x07, 0x00, 0x02, 0x02, Opcode.GET_STATUS, Result.OK])
        struct.pack_into("<I", report, 6, 0x11223344)
        struct.pack_into("<H", report, 10, 17)
        report[12] = int(
            StatusFlags.SESSION_VALID
            | StatusFlags.MANUAL_ACTIVE
            | StatusFlags.FORCE_SELECTED
            | StatusFlags.RGB_EFFECT_25_SELECTED
        )
        report[13:17] = bytes([4, 2, 3, ResetReason.EVENT_OVERFLOW])
        struct.pack_into("<HH", report, 17, 9, 81)

        response = parse_response(bytes(report), Opcode.GET_STATUS)
        self.assertEqual(response.session_token, 0x11223344)
        self.assertEqual(response.binding_generation, 17)
        self.assertEqual(response.binding_count, 4)
        self.assertEqual(response.forced_control_count, 2)
        self.assertEqual(response.queued_event_count, 3)
        self.assertEqual(response.last_reset_reason, ResetReason.EVENT_OVERFLOW)
        self.assertEqual(response.force_generation, 9)
        self.assertEqual(response.heartbeat_sequence, 81)

    def test_capabilities_response_uses_its_special_payload_layout(self) -> None:
        report = bytearray(32)
        report[:6] = bytes([0x08, 0x00, 0x02, 0x02, Opcode.GET_CAPABILITIES, Result.OK])
        report[12:16] = bytes([6, 17, 1, 32])
        struct.pack_into("<HHHH", report, 16, 104, 300, 4000, 30000)
        report[24] = int(
            BindingFlags.MIRROR | BindingFlags.EVENT_DOWN | BindingFlags.EVENT_UP
        )
        report[25] = (
            (1 << Lifetime.SESSION) | (1 << Lifetime.TTL) | (1 << Lifetime.ONE_SHOT)
        )

        capabilities = parse_capabilities(bytes(report))
        self.assertEqual(capabilities.matrix_rows, 6)
        self.assertEqual(capabilities.matrix_columns, 17)
        self.assertEqual(capabilities.total_control_slots, 104)
        self.assertEqual(capabilities.maximum_force_lease_ms, 30_000)
        self.assertEqual(
            capabilities.supported_lifetimes,
            frozenset({Lifetime.SESSION, Lifetime.TTL, Lifetime.ONE_SHOT}),
        )

    def test_every_device_event_payload_is_decoded(self) -> None:
        for event_type, edge_or_state in (
            (EventType.CONTROL_EDGE, Edge.DOWN),
            (EventType.MODE_CHANGED, 1),
            (EventType.QUEUE_OVERFLOW, 0),
            (EventType.RGB_EFFECT_CHANGED, 1),
        ):
            report = bytearray(32)
            report[:5] = bytes([0xF0, 0, 2, 2, event_type])
            struct.pack_into("<IHHHH", report, 5, 0x11223344, 9, 17, 1001, 0x010F)
            report[17] = edge_or_state
            report[18] = int(EventFlags.CAPTURED | EventFlags.RETRANSMISSION)
            struct.pack_into("<I", report, 19, 12345)
            event = parse_device_event(bytes(report))
            self.assertEqual(event.event_type, event_type)
            self.assertEqual(event.binding_id, 1001)
            self.assertEqual(event.control_id, ControlId.key(1, 15))
            self.assertEqual(event.timestamp_ms, 12345)
            self.assertEqual(
                ack_event_packet(event.session_token, event.sequence)[9:11],
                b"\x09\x00",
            )
            if event_type is EventType.RGB_EFFECT_CHANGED:
                self.assertTrue(event.rgb_effect25_selected)

    def test_protocol_v1_reports_are_rejected_explicitly(self) -> None:
        report = bytearray(32)
        report[:6] = bytes([0x07, 0, 2, 1, Opcode.GET_STATUS, Result.OK])
        with self.assertRaisesRegex(ProtocolError, "requires.*version 2"):
            parse_response(bytes(report))


if __name__ == "__main__":
    unittest.main()
