#!/usr/bin/env python3
"""Packet codec for Abralia Host Interaction firmware protocol v1.

This module contains no agent policy and performs no HID I/O by itself.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Sequence


REPORT_LENGTH = 32
CUSTOM_CHANNEL = 0x00
HOST_INTERACTION_VALUE_ID = 0x02
HOST_INTERACTION_PROTOCOL_VERSION = 0x01
HOST_INTERACTION_EVENT_GROUP = 0xF0


class ProtocolError(RuntimeError):
    """Malformed or rejected Host Interaction protocol data."""


class ControlKind(IntEnum):
    KEY = 0
    ENCODER_CW = 1
    ENCODER_CCW = 2


class Opcode(IntEnum):
    GET_CAPABILITIES = 0x00
    CLAIM_SESSION = 0x01
    KEEPALIVE = 0x02
    RELEASE_SESSION = 0x03
    BEGIN_BINDING_REPLACE = 0x10
    WRITE_BINDINGS = 0x11
    COMMIT_BINDINGS = 0x12
    CLEAR_BINDINGS = 0x13
    BEGIN_FORCE_SCOPE = 0x20
    WRITE_FORCE_KEYS = 0x21
    COMMIT_FORCE_SCOPE = 0x22
    CLEAR_FORCE_SCOPE = 0x23
    GET_STATUS = 0x24
    ACK_EVENT = 0x30


class Result(IntEnum):
    OK = 0x00
    BUSY = 0x01
    INVALID_PACKET = 0x02
    UNSUPPORTED_VERSION = 0x03
    STALE_SESSION = 0x04
    STALE_GENERATION = 0x05
    OUT_OF_RANGE = 0x06
    UNBOUND = 0x07
    QUEUE_OVERFLOW = 0x08
    RESERVED_CONTROL = 0x09
    INVALID_STATE = 0x0A


class BindingFlags(IntFlag):
    MIRROR = 1 << 0
    EVENT_DOWN = 1 << 1
    EVENT_UP = 1 << 2


CAPTURE_DOWN_UP = BindingFlags.EVENT_DOWN | BindingFlags.EVENT_UP
MIRROR_DOWN_UP = CAPTURE_DOWN_UP | BindingFlags.MIRROR


class Lifetime(IntEnum):
    SESSION = 0x00
    TTL = 0x01
    ONE_SHOT = 0x02


class ForceScope(IntEnum):
    ALL_CONFIGURED = 0x01
    SELECTED = 0x02


class EventType(IntEnum):
    CONTROL_EDGE = 0x01
    MODE_CHANGED = 0x02
    QUEUE_OVERFLOW = 0x03


class Edge(IntEnum):
    UP = 0x00
    DOWN = 0x01


class EventFlags(IntFlag):
    MIRRORED = 1 << 0
    CAPTURED = 1 << 1
    ONE_SHOT_CONSUMED = 1 << 2
    RETRANSMISSION = 1 << 3


class StatusFlags(IntFlag):
    SESSION_VALID = 1 << 0
    MANUAL_ACTIVE = 1 << 1
    FORCE_ALL = 1 << 2
    FORCE_SELECTED = 1 << 3
    BINDING_STAGING = 1 << 4
    FORCE_STAGING = 1 << 5
    EVENT_OVERFLOW = 1 << 6


@dataclass(frozen=True)
class BindingEntry:
    control_id: int
    binding_id: int


@dataclass(frozen=True)
class Response:
    verb: int
    opcode: Opcode
    result: Result
    session_token: int
    binding_generation: int
    status_flags: StatusFlags
    binding_count: int
    forced_key_count: int
    queued_event_count: int
    last_reset_reason: int
    force_generation: int
    heartbeat_sequence: int


@dataclass(frozen=True)
class DeviceEvent:
    event_type: EventType
    session_token: int
    sequence: int
    binding_generation: int
    binding_id: int
    control_id: int
    edge_or_state: int
    flags: EventFlags
    timestamp_ms: int


def control_id(kind: ControlKind, primary: int, secondary: int = 0) -> int:
    if not 0 <= primary <= 0x3F or not 0 <= secondary <= 0xFF:
        raise ProtocolError("Control address is outside the protocol range.")
    return (int(kind) << 14) | (primary << 8) | secondary


def key_control(row: int, column: int) -> int:
    return control_id(ControlKind.KEY, row, column)


def encoder_control(index: int, *, clockwise: bool) -> int:
    kind = ControlKind.ENCODER_CW if clockwise else ControlKind.ENCODER_CCW
    return control_id(kind, index)


def decode_control_id(value: int) -> tuple[ControlKind, int, int]:
    try:
        kind = ControlKind((value >> 14) & 0x03)
    except ValueError as error:
        raise ProtocolError("Unknown control kind.") from error
    return kind, (value >> 8) & 0x3F, value & 0xFF


def _base_packet(verb: int, opcode: Opcode, session_token: int = 0) -> bytearray:
    if not 0 <= session_token <= 0xFFFFFFFF:
        raise ProtocolError("Session token must fit uint32.")
    packet = bytearray(REPORT_LENGTH)
    packet[0:5] = bytes(
        [
            verb,
            CUSTOM_CHANNEL,
            HOST_INTERACTION_VALUE_ID,
            HOST_INTERACTION_PROTOCOL_VERSION,
            int(opcode),
        ]
    )
    struct.pack_into("<I", packet, 5, session_token)
    return packet


def get_capabilities_packet() -> bytes:
    return bytes(_base_packet(0x08, Opcode.GET_CAPABILITIES))


def get_status_packet(session_token: int = 0) -> bytes:
    return bytes(_base_packet(0x08, Opcode.GET_STATUS, session_token))


def claim_session_packet(session_token: int) -> bytes:
    if session_token == 0:
        raise ProtocolError("Session token zero is reserved.")
    return bytes(_base_packet(0x07, Opcode.CLAIM_SESSION, session_token))


def keepalive_packet(session_token: int, sequence: int) -> bytes:
    packet = _base_packet(0x07, Opcode.KEEPALIVE, session_token)
    struct.pack_into("<H", packet, 9, sequence)
    return bytes(packet)


def release_session_packet(session_token: int) -> bytes:
    return bytes(_base_packet(0x07, Opcode.RELEASE_SESSION, session_token))


def begin_binding_replace_packet(session_token: int, generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.BEGIN_BINDING_REPLACE, session_token)
    struct.pack_into("<H", packet, 9, generation)
    return bytes(packet)


def write_bindings_packet(
    session_token: int,
    generation: int,
    *,
    flags: BindingFlags,
    lifetime: Lifetime,
    duration_ms: int,
    entries: Sequence[BindingEntry],
) -> bytes:
    if not 1 <= len(entries) <= 3:
        raise ProtocolError("Each binding packet must contain one to three entries.")
    if int(flags) & ~int(BindingFlags.MIRROR | CAPTURE_DOWN_UP):
        raise ProtocolError("Binding flags contain reserved bits.")
    if not flags & CAPTURE_DOWN_UP:
        raise ProtocolError("A binding must request DOWN or UP events.")
    if lifetime is Lifetime.SESSION and duration_ms != 0:
        raise ProtocolError("SESSION bindings require duration_ms=0.")
    if lifetime is not Lifetime.SESSION and not 1 <= duration_ms <= 3_600_000:
        raise ProtocolError("TTL/ONE_SHOT duration must be 1...3,600,000 ms.")

    packet = _base_packet(0x07, Opcode.WRITE_BINDINGS, session_token)
    struct.pack_into("<HBBIB", packet, 9, generation, int(flags), int(lifetime), duration_ms, len(entries))
    for index, entry in enumerate(entries):
        if not 1 <= entry.binding_id <= 0xFFFF:
            raise ProtocolError("Binding IDs must be in 1...65535.")
        struct.pack_into("<HH", packet, 18 + index * 4, entry.control_id, entry.binding_id)
    return bytes(packet)


def commit_bindings_packet(session_token: int, generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.COMMIT_BINDINGS, session_token)
    struct.pack_into("<H", packet, 9, generation)
    return bytes(packet)


def clear_bindings_packet(session_token: int, generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.CLEAR_BINDINGS, session_token)
    struct.pack_into("<H", packet, 9, generation)
    return bytes(packet)


def begin_force_scope_packet(
    session_token: int,
    *,
    binding_generation: int,
    force_generation: int,
    scope: ForceScope,
    lease_ms: int,
) -> bytes:
    if not 1 <= lease_ms <= 30_000:
        raise ProtocolError("Force lease must be in 1...30,000 ms.")
    packet = _base_packet(0x07, Opcode.BEGIN_FORCE_SCOPE, session_token)
    struct.pack_into(
        "<HHBI",
        packet,
        9,
        binding_generation,
        force_generation,
        int(scope),
        lease_ms,
    )
    return bytes(packet)


def write_force_keys_packet(
    session_token: int, force_generation: int, controls: Sequence[int]
) -> bytes:
    if not 1 <= len(controls) <= 10:
        raise ProtocolError("Each force-key packet must contain one to ten controls.")
    packet = _base_packet(0x07, Opcode.WRITE_FORCE_KEYS, session_token)
    struct.pack_into("<HB", packet, 9, force_generation, len(controls))
    for index, value in enumerate(controls):
        struct.pack_into("<H", packet, 12 + index * 2, value)
    return bytes(packet)


def commit_force_scope_packet(session_token: int, force_generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.COMMIT_FORCE_SCOPE, session_token)
    struct.pack_into("<H", packet, 9, force_generation)
    return bytes(packet)


def clear_force_scope_packet(session_token: int) -> bytes:
    return bytes(_base_packet(0x07, Opcode.CLEAR_FORCE_SCOPE, session_token))


def ack_event_packet(session_token: int, event_sequence: int) -> bytes:
    packet = _base_packet(0x07, Opcode.ACK_EVENT, session_token)
    struct.pack_into("<H", packet, 9, event_sequence)
    return bytes(packet)


def response_matches(report: bytes, opcode: Opcode) -> bool:
    return (
        len(report) == REPORT_LENGTH
        and report[0] in (0x07, 0x08)
        and report[1] == CUSTOM_CHANNEL
        and report[2] == HOST_INTERACTION_VALUE_ID
        and report[3] == HOST_INTERACTION_PROTOCOL_VERSION
        and report[4] == int(opcode)
    )


def parse_response(report: bytes) -> Response:
    if len(report) != REPORT_LENGTH or not response_matches(report, Opcode(report[4])):
        raise ProtocolError("Not a Host Interaction response packet.")
    try:
        return Response(
            verb=report[0],
            opcode=Opcode(report[4]),
            result=Result(report[5]),
            session_token=struct.unpack_from("<I", report, 6)[0],
            binding_generation=struct.unpack_from("<H", report, 10)[0],
            status_flags=StatusFlags(report[12]),
            binding_count=report[13],
            forced_key_count=report[14],
            queued_event_count=report[15],
            last_reset_reason=report[16],
            force_generation=struct.unpack_from("<H", report, 17)[0],
            heartbeat_sequence=struct.unpack_from("<H", report, 19)[0],
        )
    except ValueError as error:
        raise ProtocolError("Response contains an unknown enum value.") from error


def is_device_event(report: bytes) -> bool:
    return (
        len(report) == REPORT_LENGTH
        and report[0] == HOST_INTERACTION_EVENT_GROUP
        and report[1] == CUSTOM_CHANNEL
        and report[2] == HOST_INTERACTION_VALUE_ID
        and report[3] == HOST_INTERACTION_PROTOCOL_VERSION
    )


def parse_device_event(report: bytes) -> DeviceEvent:
    if not is_device_event(report):
        raise ProtocolError("Not a Host Interaction event packet.")
    try:
        return DeviceEvent(
            event_type=EventType(report[4]),
            session_token=struct.unpack_from("<I", report, 5)[0],
            sequence=struct.unpack_from("<H", report, 9)[0],
            binding_generation=struct.unpack_from("<H", report, 11)[0],
            binding_id=struct.unpack_from("<H", report, 13)[0],
            control_id=struct.unpack_from("<H", report, 15)[0],
            edge_or_state=report[17],
            flags=EventFlags(report[18]),
            timestamp_ms=struct.unpack_from("<I", report, 19)[0],
        )
    except ValueError as error:
        raise ProtocolError("Event contains an unknown enum value.") from error

