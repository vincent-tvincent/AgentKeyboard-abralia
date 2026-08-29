# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

from abralia.interaction.client import (
    HostInteractionController,
    HostInteractionProtocolClient,
)
from abralia.interaction.keymap import KeycodeMatch
from abralia.interaction.protocol import (
    BindingEntry,
    BindingPolicy,
    ControlId,
    Edge,
    EventType,
    ForceScope,
    Lifetime,
    Opcode,
    ResetReason,
    Result,
    Routing,
)
from abralia.layout import load_compatibility_layout
from abralia.rgb import load_profile


class FakeFirmwareTransport:
    def __init__(self) -> None:
        self.session_token = 0
        self.binding_generation = 0
        self.force_generation = 0
        self.heartbeat_sequence = 0
        self.bindings: list[tuple[int, int]] = []
        self.staging_bindings: list[tuple[int, int]] = []
        self.forced: list[int] = []
        self.staging_forced: list[int] = []
        self.force_scope = 0
        self.requests: list[bytes] = []
        self.unmatched: deque[bytes] = deque()
        self.incoming: deque[bytes] = deque()
        self.closed = False

    def _common_response(self, request: bytes) -> bytearray:
        response = bytearray(32)
        response[:5] = request[:5]
        response[5] = Result.OK
        struct.pack_into("<I", response, 6, self.session_token)
        struct.pack_into("<H", response, 10, self.binding_generation)
        response[12] = 1 if self.session_token else 0
        response[13] = len(self.bindings)
        response[14] = len(self.forced)
        response[15] = len(self.incoming)
        response[16] = ResetReason.NONE
        struct.pack_into("<H", response, 17, self.force_generation)
        struct.pack_into("<H", response, 19, self.heartbeat_sequence)
        return response

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        del timeout_ms
        packet = bytes(request) + bytes(32 - len(request))
        self.requests.append(packet)
        opcode = Opcode(packet[4])

        if opcode is Opcode.CLAIM_SESSION:
            self.session_token = struct.unpack_from("<I", packet, 5)[0]
        elif opcode is Opcode.KEEPALIVE:
            self.heartbeat_sequence = struct.unpack_from("<H", packet, 9)[0]
        elif opcode is Opcode.RELEASE_SESSION:
            self.session_token = 0
            self.binding_generation = 0
            self.force_generation = 0
            self.bindings = []
            self.forced = []
        elif opcode is Opcode.BEGIN_BINDING_REPLACE:
            self.staging_bindings = []
        elif opcode is Opcode.WRITE_BINDINGS:
            count = packet[17]
            for index in range(count):
                self.staging_bindings.append(
                    struct.unpack_from("<HH", packet, 18 + index * 4)
                )
        elif opcode is Opcode.COMMIT_BINDINGS:
            self.binding_generation = struct.unpack_from("<H", packet, 9)[0]
            self.bindings = list(self.staging_bindings)
            self.forced = []
        elif opcode is Opcode.CLEAR_BINDINGS:
            self.binding_generation = struct.unpack_from("<H", packet, 9)[0]
            self.bindings = []
            self.forced = []
        elif opcode is Opcode.BEGIN_FORCE_SCOPE:
            self.force_scope = packet[13]
            self.staging_forced = []
        elif opcode is Opcode.WRITE_FORCE_KEYS:
            count = packet[11]
            self.staging_forced.extend(struct.unpack_from(f"<{count}H", packet, 12))
        elif opcode is Opcode.COMMIT_FORCE_SCOPE:
            self.force_generation = struct.unpack_from("<H", packet, 9)[0]
            self.forced = (
                [control for control, _binding in self.bindings]
                if self.force_scope == ForceScope.ALL_CONFIGURED
                else list(self.staging_forced)
            )
        elif opcode is Opcode.CLEAR_FORCE_SCOPE:
            self.forced = []

        response = self._common_response(packet)
        if opcode is Opcode.GET_CAPABILITIES:
            response[12:16] = bytes([2, 2, 1, 32])
            struct.pack_into("<HHHH", response, 16, 6, 300, 4000, 30000)
            response[24] = 7
            response[25] = 7
        if not response_matches(bytes(response)):
            raise AssertionError("Fake firmware response did not match request.")
        return bytes(response)

    def write(self, request: Sequence[int] | bytes) -> None:
        del request

    def read(self, timeout_ms: int) -> bytes:
        del timeout_ms
        return self.incoming.popleft() if self.incoming else b""

    def pop_unmatched(self) -> bytes | None:
        return self.unmatched.popleft() if self.unmatched else None

    def close(self) -> None:
        self.closed = True


class StubKeymapReader:
    def __init__(self) -> None:
        self.first = ControlId.key(0, 0)
        self.second = ControlId.key(0, 1)

    def resolve(self, *_args: object, **_kwargs: object) -> tuple[KeycodeMatch, ...]:
        # The first physical key carries the keycode on two layers. Fan-out must
        # deduplicate it while retaining both matches in the returned evidence.
        return (
            KeycodeMatch(0x0004, 0, self.first),
            KeycodeMatch(0x0004, 1, self.first),
            KeycodeMatch(0x0004, 0, self.second),
        )


def event_packet(token: int) -> bytes:
    report = bytearray(32)
    report[:5] = bytes([0xF0, 0, 2, 1, EventType.CONTROL_EDGE])
    struct.pack_into("<IHHHH", report, 5, token, 9, 3, 77, 0)
    report[17] = Edge.DOWN
    report[18] = 2
    struct.pack_into("<I", report, 19, 1234)
    return bytes(report)


class InteractionClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeFirmwareTransport()
        self.client = HostInteractionProtocolClient(self.transport)

    def test_low_level_client_exposes_every_firmware_command_family(self) -> None:
        capabilities = self.client.get_capabilities()
        self.assertEqual(capabilities.total_control_slots, 6)
        self.client.claim_session(0x11223344)
        self.client.get_status()
        self.client.keepalive(7)
        self.client.begin_binding_replace(1)
        self.client.write_bindings(
            1,
            BindingPolicy(),
            [BindingEntry(ControlId.key(0, 0), 1001)],
        )
        self.client.commit_bindings(1)
        self.client.begin_force_scope(
            binding_generation=1,
            force_generation=1,
            scope=ForceScope.SELECTED,
            lease_ms=1000,
        )
        self.client.write_force_controls(1, [ControlId.key(0, 0)])
        self.client.commit_force_scope(1)
        self.client.clear_force_scope()
        self.client.ack_event(9)
        self.client.clear_bindings(2)
        self.client.release_session()

        opcodes = {Opcode(request[4]) for request in self.transport.requests}
        self.assertEqual(opcodes, set(Opcode))

    def test_high_level_keycode_operations_fan_out_to_every_matching_control(
        self,
    ) -> None:
        self.client.get_capabilities()
        self.client.claim_session(0x11223344)
        controller = HostInteractionController(
            self.client,
            keymap_reader=StubKeymapReader(),
        )
        policy = BindingPolicy(
            routing=Routing.MIRROR,
            lifetime=Lifetime.TTL,
            duration_ms=5000,
        )
        update = controller.set_keycode_controls("KC_A", binding_id=77, policy=policy)
        self.assertEqual(
            set(update.controls), {ControlId.key(0, 0), ControlId.key(0, 1)}
        )
        self.assertEqual(len(update.keycode_matches), 3)
        self.assertEqual(set(self.transport.bindings), {(0x0000, 77), (0x0001, 77)})

        activation = controller.activate_keycode_controls("KC_A", lease_ms=2000)
        self.assertEqual(set(activation.controls), set(update.controls))
        self.assertEqual(set(self.transport.forced), {0x0000, 0x0001})

        removed = controller.remove_keycode_controls("KC_A")
        self.assertEqual(set(removed.controls), set(update.controls))
        self.assertEqual(self.transport.bindings, [])

    def test_direct_control_id_activation_and_complete_turn_off_are_separate(
        self,
    ) -> None:
        self.client.get_capabilities()
        self.client.claim_session(0x11223344)
        controller = HostInteractionController(
            self.client,
            keymap_reader=StubKeymapReader(),
        )
        controller.set_controls([ControlId.key(0, 0)], binding_id=1)
        controller.set_controls([ControlId.key(0, 1)], binding_id=2)
        controller.activate_all(lease_ms=30_000)
        self.assertEqual(set(self.transport.forced), {0x0000, 0x0001})
        controller.deactivate_forced()
        self.assertEqual(self.transport.forced, [])

        controller.turn_off_all()
        self.assertEqual(self.client.session_token, 0)
        self.assertEqual(controller.bindings, ())

    def test_compatibility_region_uses_shared_resolved_control_ids(self) -> None:
        self.client.get_capabilities()
        self.client.claim_session(0x11223344)
        profile = load_profile()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "layout.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_id": profile.profile_id,
                        "matrix_aliases": {"FIRST": [0, 0]},
                        "regions": [
                            {
                                "id": "actions",
                                "rows": [["FIRST", {"matrix": [0, 1]}]],
                                "strategies": ["row_key_index"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            compatibility = load_compatibility_layout(profile, source)

        controller = HostInteractionController(
            self.client,
            keymap_reader=StubKeymapReader(),
            compatibility=compatibility,
        )
        update = controller.set_region_controls("actions", binding_id=91)
        self.assertEqual(update.controls, (ControlId.key(0, 0), ControlId.key(0, 1)))
        self.assertEqual(set(self.transport.bindings), {(0x0000, 91), (0x0001, 91)})
        activation = controller.activate_region("actions", lease_ms=2000)
        self.assertEqual(activation.controls, update.controls)

    def test_event_payload_is_received_and_acknowledged(self) -> None:
        self.client.get_capabilities()
        self.client.claim_session(0x11223344)
        self.transport.incoming.append(event_packet(self.client.session_token))
        event = self.client.read_event(10)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.binding_id, 77)
        self.assertEqual(event.edge, Edge.DOWN)
        self.assertEqual(self.transport.requests[-1][4], Opcode.ACK_EVENT)

    def test_service_sends_the_required_keepalive_when_due(self) -> None:
        self.client.get_capabilities()
        self.client.claim_session(0x11223344)
        self.client._next_heartbeat_at = 0
        self.assertEqual(self.client.service(timeout_ms=0), ())
        self.assertEqual(self.transport.requests[-1][4], Opcode.KEEPALIVE)
        self.assertEqual(self.transport.heartbeat_sequence, 1)


if __name__ == "__main__":
    unittest.main()
