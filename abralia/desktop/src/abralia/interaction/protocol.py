# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Typed codec for every Abralia Host Interaction firmware v1 payload."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Sequence

from .errors import ProtocolError


REPORT_LENGTH = 32
CUSTOM_CHANNEL = 0x00
HOST_INTERACTION_VALUE_ID = 0x02
PROTOCOL_VERSION = 0x01
EVENT_GROUP = 0xF0
MAX_TTL_MS = 3_600_000
MAX_FORCE_LEASE_MS = 30_000


class ControlKind(IntEnum):
    KEY = 0
    ENCODER_CW = 1
    ENCODER_CCW = 2


@dataclass(frozen=True, order=True, slots=True)
class ControlId:
    """Firmware physical-control address, independent of configured keycode."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise ProtocolError("Control ID must be an integer.")
        if not 0 <= self.value <= 0xFFFF:
            raise ProtocolError("Control ID must fit uint16.")
        try:
            ControlKind((self.value >> 14) & 0x03)
        except ValueError as error:
            raise ProtocolError("Control ID uses the reserved control kind.") from error

    @classmethod
    def key(cls, row: int, column: int) -> ControlId:
        return cls.from_parts(ControlKind.KEY, row, column)

    @classmethod
    def encoder_clockwise(cls, index: int) -> ControlId:
        return cls.from_parts(ControlKind.ENCODER_CW, index, 0)

    @classmethod
    def encoder_counterclockwise(cls, index: int) -> ControlId:
        return cls.from_parts(ControlKind.ENCODER_CCW, index, 0)

    @classmethod
    def from_parts(
        cls, kind: ControlKind, primary: int, secondary: int = 0
    ) -> ControlId:
        if not 0 <= primary <= 0x3F or not 0 <= secondary <= 0xFF:
            raise ProtocolError("Control address is outside the protocol range.")
        return cls((int(kind) << 14) | (primary << 8) | secondary)

    @property
    def kind(self) -> ControlKind:
        return ControlKind((self.value >> 14) & 0x03)

    @property
    def primary(self) -> int:
        return (self.value >> 8) & 0x3F

    @property
    def secondary(self) -> int:
        return self.value & 0xFF

    def __int__(self) -> int:
        return self.value

    def __str__(self) -> str:
        return f"0x{self.value:04X}"


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


class Routing(IntEnum):
    CAPTURE = 0
    MIRROR = 1


class BindingFlags(IntFlag):
    MIRROR = 1 << 0
    EVENT_DOWN = 1 << 1
    EVENT_UP = 1 << 2


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


class ResetReason(IntEnum):
    NONE = 0x00
    SESSION_REPLACED = 0x01
    HEARTBEAT_TIMEOUT = 0x02
    EVENT_OVERFLOW = 0x03
    HOST_RELEASED = 0x04


@dataclass(frozen=True, slots=True)
class BindingPolicy:
    routing: Routing = Routing.CAPTURE
    lifetime: Lifetime = Lifetime.SESSION
    duration_ms: int = 0
    emit_down: bool = True
    emit_up: bool = True

    def __post_init__(self) -> None:
        if not self.emit_down and not self.emit_up:
            raise ProtocolError("A binding must emit DOWN, UP, or both.")
        if self.lifetime is Lifetime.SESSION:
            if self.duration_ms != 0:
                raise ProtocolError("SESSION bindings require duration_ms=0.")
        elif not 1 <= self.duration_ms <= MAX_TTL_MS:
            raise ProtocolError(
                f"TTL/ONE_SHOT duration must be 1...{MAX_TTL_MS} ms."
            )

    @property
    def flags(self) -> BindingFlags:
        flags = BindingFlags(0)
        if self.routing is Routing.MIRROR:
            flags |= BindingFlags.MIRROR
        if self.emit_down:
            flags |= BindingFlags.EVENT_DOWN
        if self.emit_up:
            flags |= BindingFlags.EVENT_UP
        return flags


@dataclass(frozen=True, slots=True)
class BindingEntry:
    control_id: ControlId
    binding_id: int

    def __post_init__(self) -> None:
        if not 1 <= self.binding_id <= 0xFFFF:
            raise ProtocolError("Binding ID must be in 1...65535.")


@dataclass(frozen=True, slots=True)
class Response:
    verb: int
    opcode: Opcode
    result: Result
    session_token: int
    binding_generation: int
    status_flags: StatusFlags
    binding_count: int
    forced_control_count: int
    queued_event_count: int
    last_reset_reason: ResetReason
    force_generation: int
    heartbeat_sequence: int


@dataclass(frozen=True, slots=True)
class Capabilities:
    response: Response
    matrix_rows: int
    matrix_columns: int
    encoder_count: int
    event_queue_capacity: int
    total_control_slots: int
    double_tap_window_ms: int
    heartbeat_timeout_ms: int
    maximum_force_lease_ms: int
    supported_binding_flags: BindingFlags
    supported_lifetimes: frozenset[Lifetime]


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    event_type: EventType
    session_token: int
    sequence: int
    binding_generation: int
    binding_id: int
    control_id: ControlId
    edge_or_state: int
    flags: EventFlags
    timestamp_ms: int

    @property
    def edge(self) -> Edge:
        if self.event_type is not EventType.CONTROL_EDGE:
            raise ProtocolError("Only CONTROL_EDGE events contain an edge.")
        try:
            return Edge(self.edge_or_state)
        except ValueError as error:
            raise ProtocolError("CONTROL_EDGE event has an unknown edge.") from error

    @property
    def mode_active(self) -> bool:
        if self.event_type is not EventType.MODE_CHANGED:
            raise ProtocolError("Only MODE_CHANGED events contain mode state.")
        return bool(self.edge_or_state)


def _uint16(name: str, value: int, *, nonzero: bool = False) -> int:
    lower = 1 if nonzero else 0
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= 0xFFFF:
        qualifier = "nonzero " if nonzero else ""
        raise ProtocolError(f"{name} must fit {qualifier}uint16.")
    return value


def _uint32(name: str, value: int, *, nonzero: bool = False) -> int:
    lower = 1 if nonzero else 0
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= 0xFFFFFFFF:
        qualifier = "nonzero " if nonzero else ""
        raise ProtocolError(f"{name} must fit {qualifier}uint32.")
    return value


def _base_packet(verb: int, opcode: Opcode, session_token: int = 0) -> bytearray:
    _uint32("Session token", session_token)
    packet = bytearray(REPORT_LENGTH)
    packet[0:5] = bytes(
        [verb, CUSTOM_CHANNEL, HOST_INTERACTION_VALUE_ID, PROTOCOL_VERSION, int(opcode)]
    )
    struct.pack_into("<I", packet, 5, session_token)
    return packet


def get_capabilities_packet() -> bytes:
    return bytes(_base_packet(0x08, Opcode.GET_CAPABILITIES))


def get_status_packet(session_token: int = 0) -> bytes:
    return bytes(_base_packet(0x08, Opcode.GET_STATUS, session_token))


def claim_session_packet(session_token: int) -> bytes:
    _uint32("Session token", session_token, nonzero=True)
    return bytes(_base_packet(0x07, Opcode.CLAIM_SESSION, session_token))


def keepalive_packet(session_token: int, sequence: int) -> bytes:
    packet = _base_packet(0x07, Opcode.KEEPALIVE, session_token)
    struct.pack_into("<H", packet, 9, _uint16("Heartbeat sequence", sequence))
    return bytes(packet)


def release_session_packet(session_token: int) -> bytes:
    return bytes(_base_packet(0x07, Opcode.RELEASE_SESSION, session_token))


def begin_binding_replace_packet(
    session_token: int, binding_generation: int
) -> bytes:
    packet = _base_packet(0x07, Opcode.BEGIN_BINDING_REPLACE, session_token)
    struct.pack_into(
        "<H", packet, 9, _uint16("Binding generation", binding_generation, nonzero=True)
    )
    return bytes(packet)


def write_bindings_packet(
    session_token: int,
    binding_generation: int,
    policy: BindingPolicy,
    entries: Sequence[BindingEntry],
) -> bytes:
    if not 1 <= len(entries) <= 3:
        raise ProtocolError("Each WRITE_BINDINGS packet requires one to three entries.")
    packet = _base_packet(0x07, Opcode.WRITE_BINDINGS, session_token)
    struct.pack_into(
        "<HBBIB",
        packet,
        9,
        _uint16("Binding generation", binding_generation, nonzero=True),
        int(policy.flags),
        int(policy.lifetime),
        policy.duration_ms,
        len(entries),
    )
    for index, entry in enumerate(entries):
        struct.pack_into(
            "<HH", packet, 18 + index * 4, int(entry.control_id), entry.binding_id
        )
    return bytes(packet)


def commit_bindings_packet(session_token: int, binding_generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.COMMIT_BINDINGS, session_token)
    struct.pack_into(
        "<H", packet, 9, _uint16("Binding generation", binding_generation, nonzero=True)
    )
    return bytes(packet)


def clear_bindings_packet(session_token: int, binding_generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.CLEAR_BINDINGS, session_token)
    struct.pack_into(
        "<H", packet, 9, _uint16("Binding generation", binding_generation, nonzero=True)
    )
    return bytes(packet)


def begin_force_scope_packet(
    session_token: int,
    *,
    binding_generation: int,
    force_generation: int,
    scope: ForceScope,
    lease_ms: int,
) -> bytes:
    if not 1 <= lease_ms <= MAX_FORCE_LEASE_MS:
        raise ProtocolError(f"Force lease must be in 1...{MAX_FORCE_LEASE_MS} ms.")
    packet = _base_packet(0x07, Opcode.BEGIN_FORCE_SCOPE, session_token)
    struct.pack_into(
        "<HHBI",
        packet,
        9,
        _uint16("Binding generation", binding_generation),
        _uint16("Force generation", force_generation, nonzero=True),
        int(scope),
        lease_ms,
    )
    return bytes(packet)


def write_force_controls_packet(
    session_token: int, force_generation: int, controls: Sequence[ControlId]
) -> bytes:
    if not 1 <= len(controls) <= 10:
        raise ProtocolError(
            "Each WRITE_FORCE_KEYS packet requires one to ten controls."
        )
    packet = _base_packet(0x07, Opcode.WRITE_FORCE_KEYS, session_token)
    struct.pack_into(
        "<HB",
        packet,
        9,
        _uint16("Force generation", force_generation, nonzero=True),
        len(controls),
    )
    for index, control in enumerate(controls):
        struct.pack_into("<H", packet, 12 + index * 2, int(control))
    return bytes(packet)


def commit_force_scope_packet(session_token: int, force_generation: int) -> bytes:
    packet = _base_packet(0x07, Opcode.COMMIT_FORCE_SCOPE, session_token)
    struct.pack_into(
        "<H", packet, 9, _uint16("Force generation", force_generation, nonzero=True)
    )
    return bytes(packet)


def clear_force_scope_packet(session_token: int) -> bytes:
    return bytes(_base_packet(0x07, Opcode.CLEAR_FORCE_SCOPE, session_token))


def ack_event_packet(session_token: int, event_sequence: int) -> bytes:
    packet = _base_packet(0x07, Opcode.ACK_EVENT, session_token)
    struct.pack_into(
        "<H", packet, 9, _uint16("Event sequence", event_sequence, nonzero=True)
    )
    return bytes(packet)


def response_matches(report: bytes, opcode: Opcode) -> bool:
    return (
        len(report) == REPORT_LENGTH
        and report[0] in (0x07, 0x08)
        and report[1] == CUSTOM_CHANNEL
        and report[2] == HOST_INTERACTION_VALUE_ID
        and report[3] == PROTOCOL_VERSION
        and report[4] == int(opcode)
    )


def parse_response(report: bytes, expected_opcode: Opcode | None = None) -> Response:
    if len(report) != REPORT_LENGTH:
        raise ProtocolError("Host Interaction response must contain 32 bytes.")
    try:
        opcode = Opcode(report[4])
    except ValueError as error:
        raise ProtocolError("Response contains an unknown opcode.") from error
    if not response_matches(report, opcode):
        raise ProtocolError("Not a Host Interaction response packet.")
    if expected_opcode is not None and opcode is not expected_opcode:
        raise ProtocolError(
            f"Expected {expected_opcode.name}, received {opcode.name}."
        )
    try:
        return Response(
            verb=report[0],
            opcode=opcode,
            result=Result(report[5]),
            session_token=struct.unpack_from("<I", report, 6)[0],
            binding_generation=struct.unpack_from("<H", report, 10)[0],
            status_flags=StatusFlags(report[12]),
            binding_count=report[13],
            forced_control_count=report[14],
            queued_event_count=report[15],
            last_reset_reason=ResetReason(report[16]),
            force_generation=struct.unpack_from("<H", report, 17)[0],
            heartbeat_sequence=struct.unpack_from("<H", report, 19)[0],
        )
    except ValueError as error:
        raise ProtocolError("Response contains an unknown enum value.") from error


def parse_capabilities(report: bytes) -> Capabilities:
    if not response_matches(report, Opcode.GET_CAPABILITIES):
        raise ProtocolError("Not a Host Interaction capabilities response.")
    try:
        result = Result(report[5])
    except ValueError as error:
        raise ProtocolError("Capabilities response contains an unknown result.") from error
    # GET_CAPABILITIES deliberately replaces common response bytes 12 onward.
    response = Response(
        verb=report[0],
        opcode=Opcode.GET_CAPABILITIES,
        result=result,
        session_token=struct.unpack_from("<I", report, 6)[0],
        binding_generation=struct.unpack_from("<H", report, 10)[0],
        status_flags=StatusFlags(0),
        binding_count=0,
        forced_control_count=0,
        queued_event_count=0,
        last_reset_reason=ResetReason.NONE,
        force_generation=0,
        heartbeat_sequence=0,
    )
    lifetime_mask = report[25]
    return Capabilities(
        response=response,
        matrix_rows=report[12],
        matrix_columns=report[13],
        encoder_count=report[14],
        event_queue_capacity=report[15],
        total_control_slots=struct.unpack_from("<H", report, 16)[0],
        double_tap_window_ms=struct.unpack_from("<H", report, 18)[0],
        heartbeat_timeout_ms=struct.unpack_from("<H", report, 20)[0],
        maximum_force_lease_ms=struct.unpack_from("<H", report, 22)[0],
        supported_binding_flags=BindingFlags(report[24]),
        supported_lifetimes=frozenset(
            lifetime for lifetime in Lifetime if lifetime_mask & (1 << int(lifetime))
        ),
    )


def is_device_event(report: bytes) -> bool:
    return (
        len(report) == REPORT_LENGTH
        and report[0] == EVENT_GROUP
        and report[1] == CUSTOM_CHANNEL
        and report[2] == HOST_INTERACTION_VALUE_ID
        and report[3] == PROTOCOL_VERSION
    )


def parse_device_event(report: bytes) -> DeviceEvent:
    if not is_device_event(report):
        raise ProtocolError("Not a Host Interaction event packet.")
    try:
        event = DeviceEvent(
            event_type=EventType(report[4]),
            session_token=struct.unpack_from("<I", report, 5)[0],
            sequence=struct.unpack_from("<H", report, 9)[0],
            binding_generation=struct.unpack_from("<H", report, 11)[0],
            binding_id=struct.unpack_from("<H", report, 13)[0],
            control_id=ControlId(struct.unpack_from("<H", report, 15)[0]),
            edge_or_state=report[17],
            flags=EventFlags(report[18]),
            timestamp_ms=struct.unpack_from("<I", report, 19)[0],
        )
        if event.event_type is EventType.CONTROL_EDGE:
            event.edge
        return event
    except ValueError as error:
        raise ProtocolError("Event contains an unknown enum value.") from error
