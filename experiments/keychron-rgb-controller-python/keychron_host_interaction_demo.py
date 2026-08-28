#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Guarded manual harness for Abralia Host Interaction firmware v1.

The harness never flashes firmware, persists settings, or changes RGB. It
claims a volatile session, stages example bindings, keeps the watchdog alive,
ACKs events, and releases the session on exit.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from collections.abc import Callable

import hid

from host_interaction_protocol import (
    CAPTURE_DOWN_UP,
    MIRROR_DOWN_UP,
    BindingEntry,
    DeviceEvent,
    EventType,
    ForceScope,
    Lifetime,
    Opcode,
    ProtocolError,
    Result,
    ack_event_packet,
    begin_binding_replace_packet,
    begin_force_scope_packet,
    claim_session_packet,
    clear_force_scope_packet,
    commit_bindings_packet,
    commit_force_scope_packet,
    encoder_control,
    get_capabilities_packet,
    is_device_event,
    keepalive_packet,
    key_control,
    parse_device_event,
    parse_response,
    release_session_packet,
    response_matches,
    write_bindings_packet,
    write_force_keys_packet,
)
from keychron_rgb_demo import (
    DemoError,
    REPORT_LENGTH,
    V3_8K_ANSI,
    enumerate_keychron_candidates,
)


class HostInteractionConnection:
    def __init__(self, path: bytes | str):
        self.path = path
        self.device: hid.device | None = None
        self.pending_events: list[DeviceEvent] = []

    def __enter__(self) -> HostInteractionConnection:
        device = hid.device()
        try:
            device.open_path(self.path)
            device.set_nonblocking(False)
        except OSError as error:
            device.close()
            raise DemoError(
                "Could not open Raw HID. Close Keychron Launcher/VIA and check HID permissions."
            ) from error
        self.device = device
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self.device is not None:
            self.device.close()
            self.device = None

    def send(self, packet: bytes) -> None:
        assert self.device is not None
        if len(packet) != REPORT_LENGTH:
            raise DemoError("Host Interaction reports must contain exactly 32 bytes.")
        if self.device.write(bytes([0]) + packet) <= 0:
            raise DemoError("The operating system rejected the Raw HID report.")

    def read(self, timeout_ms: int) -> bytes:
        assert self.device is not None
        report = bytes(self.device.read(REPORT_LENGTH, timeout_ms))
        if len(report) == REPORT_LENGTH + 1 and report[0] == 0:
            report = report[1:]
        return report

    def transact(self, packet: bytes, opcode: Opcode, timeout_ms: int = 1000) -> bytes:
        self.send(packet)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            report = self.read(remaining)
            if not report:
                continue
            if is_device_event(report):
                self.pending_events.append(parse_device_event(report))
                continue
            if response_matches(report, opcode):
                response = parse_response(report)
                if response.result is not Result.OK:
                    raise DemoError(
                        f"{opcode.name} failed with {response.result.name}."
                    )
                return report
        raise DemoError(f"Timed out waiting for {opcode.name} response.")


def find_v3_path() -> bytes | str:
    matches = [
        candidate
        for candidate in enumerate_keychron_candidates()
        if candidate.vendor_id == V3_8K_ANSI.vendor_id
        and candidate.product_id == V3_8K_ANSI.product_id
        and candidate.path is not None
        and candidate.usage_page == 0xFF60
        and candidate.usage == 0x61
    ]
    if len(matches) != 1:
        raise DemoError(
            f"Expected one V3 8K Raw HID interface, found {len(matches)}."
        )
    assert matches[0].path is not None
    return matches[0].path


def transact(
    connection: HostInteractionConnection,
    packet: bytes,
    opcode: Opcode,
) -> None:
    connection.transact(packet, opcode)


def configure_demo_bindings(
    connection: HostInteractionConnection,
    token: int,
    generation: int,
) -> list[int]:
    home = key_control(1, 15)
    end = key_control(2, 15)
    encoder_cw = encoder_control(0, clockwise=True)
    encoder_ccw = encoder_control(0, clockwise=False)

    transact(
        connection,
        begin_binding_replace_packet(token, generation),
        Opcode.BEGIN_BINDING_REPLACE,
    )
    transact(
        connection,
        write_bindings_packet(
            token,
            generation,
            flags=CAPTURE_DOWN_UP,
            lifetime=Lifetime.ONE_SHOT,
            duration_ms=30_000,
            entries=[BindingEntry(home, 1001), BindingEntry(encoder_cw, 1002)],
        ),
        Opcode.WRITE_BINDINGS,
    )
    transact(
        connection,
        write_bindings_packet(
            token,
            generation,
            flags=MIRROR_DOWN_UP,
            lifetime=Lifetime.SESSION,
            duration_ms=0,
            entries=[BindingEntry(end, 1003), BindingEntry(encoder_ccw, 1004)],
        ),
        Opcode.WRITE_BINDINGS,
    )
    transact(
        connection,
        commit_bindings_packet(token, generation),
        Opcode.COMMIT_BINDINGS,
    )
    return [home, encoder_cw]


def handle_event(
    connection: HostInteractionConnection,
    token: int,
    event: DeviceEvent,
) -> None:
    if event.session_token != token:
        return
    if event.event_type is EventType.CONTROL_EDGE:
        print(
            "event",
            f"seq={event.sequence}",
            f"binding={event.binding_id}",
            f"control=0x{event.control_id:04X}",
            f"edge={event.edge_or_state}",
            f"flags=0x{int(event.flags):02X}",
        )
    elif event.event_type is EventType.MODE_CHANGED:
        print(
            "mode",
            "ACTIVE" if event.edge_or_state else "NORMAL",
            f"seq={event.sequence}",
        )
    else:
        print("firmware event", event.event_type.name, f"seq={event.sequence}")

    transact(
        connection,
        ack_event_packet(token, event.sequence),
        Opcode.ACK_EVENT,
    )


def run_probe(connection: HostInteractionConnection) -> None:
    report = connection.transact(
        get_capabilities_packet(), Opcode.GET_CAPABILITIES
    )
    print("Host Interaction firmware detected")
    print(f"  matrix: {report[12]} rows × {report[13]} columns")
    print(f"  encoders: {report[14]}")
    print(f"  event queue: {report[15]}")
    print(f"  controls: {int.from_bytes(report[16:18], 'little')}")
    print(f"  double-tap window: {int.from_bytes(report[18:20], 'little')} ms")
    print(f"  heartbeat timeout: {int.from_bytes(report[20:22], 'little')} ms")


def run_demo(
    connection: HostInteractionConnection,
    *,
    seconds: float,
    force_selected: bool,
) -> None:
    confirmation = input(
        "Type INTERACT to claim a volatile session and stage input bindings: "
    )
    if confirmation != "INTERACT":
        raise DemoError("Confirmation did not match; keyboard state was not changed.")

    token = secrets.randbits(32) or 1
    binding_generation = 1
    force_generation = 1
    heartbeat_sequence = 0

    transact(connection, claim_session_packet(token), Opcode.CLAIM_SESSION)
    try:
        selected_controls = configure_demo_bindings(
            connection, token, binding_generation
        )
        if force_selected:
            transact(
                connection,
                begin_force_scope_packet(
                    token,
                    binding_generation=binding_generation,
                    force_generation=force_generation,
                    scope=ForceScope.SELECTED,
                    lease_ms=30_000,
                ),
                Opcode.BEGIN_FORCE_SCOPE,
            )
            transact(
                connection,
                write_force_keys_packet(
                    token, force_generation, selected_controls
                ),
                Opcode.WRITE_FORCE_KEYS,
            )
            transact(
                connection,
                commit_force_scope_packet(token, force_generation),
                Opcode.COMMIT_FORCE_SCOPE,
            )

        print("Bindings staged:")
        print("  Home: CAPTURE + ONE_SHOT, binding 1001")
        print("  knob clockwise: CAPTURE + ONE_SHOT, binding 1002")
        print("  End: MIRROR + SESSION, binding 1003")
        print("  knob counterclockwise: MIRROR + SESSION, binding 1004")
        if not force_selected:
            print("Double-tap Pause to enter or exit Host Interaction Mode.")
        print("Press Ctrl-C to end; the harness will release the session.")

        deadline = time.monotonic() + seconds
        next_heartbeat = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                heartbeat_sequence = (heartbeat_sequence + 1) & 0xFFFF
                transact(
                    connection,
                    keepalive_packet(token, heartbeat_sequence),
                    Opcode.KEEPALIVE,
                )
                next_heartbeat = time.monotonic() + 1.0

            while connection.pending_events:
                handle_event(connection, token, connection.pending_events.pop(0))

            report = connection.read(50)
            if report and is_device_event(report):
                handle_event(connection, token, parse_device_event(report))
    finally:
        try:
            transact(
                connection,
                clear_force_scope_packet(token),
                Opcode.CLEAR_FORCE_SCOPE,
            )
        except DemoError:
            pass
        try:
            transact(
                connection,
                release_session_packet(token),
                Opcode.RELEASE_SESSION,
            )
        except DemoError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Read capabilities without claiming a session or changing behavior.",
    )
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument(
        "--force-selected",
        action="store_true",
        help="Force the Home and clockwise-knob bindings for a bounded lease.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.seconds <= 0:
        raise DemoError("--seconds must be positive.")

    with HostInteractionConnection(find_v3_path()) as connection:
        if args.probe_only:
            run_probe(connection)
        else:
            run_demo(
                connection,
                seconds=args.seconds,
                force_selected=args.force_selected,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DemoError, ProtocolError, KeyboardInterrupt) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
