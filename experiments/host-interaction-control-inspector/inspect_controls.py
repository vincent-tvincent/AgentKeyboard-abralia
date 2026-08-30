#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Print binding and Control IDs for every mirrored keyboard press."""

from __future__ import annotations

import argparse
import json
import sys
import time

from abralia.interaction import (
    BindingEntry,
    BindingPolicy,
    Capabilities,
    ConfiguredBinding,
    ControlId,
    DeviceEvent,
    EventType,
    HostInteractionController,
    HostInteractionError,
    HostInteractionProtocolClient,
    Lifetime,
    Routing,
)

from abralia import load_device_profile


def controls_from_capabilities(
    capabilities: Capabilities, toggle_control: ControlId
) -> tuple[ControlId, ...]:
    """Enumerate firmware addresses without assuming a physical layout."""

    controls = [
        ControlId.key(row, column)
        for row in range(capabilities.matrix_rows)
        for column in range(capabilities.matrix_columns)
        if ControlId.key(row, column) != toggle_control
    ]
    for index in range(capabilities.encoder_count):
        controls.extend(
            [
                ControlId.encoder_clockwise(index),
                ControlId.encoder_counterclockwise(index),
            ]
        )
    return tuple(controls)


def inspection_bindings(
    capabilities: Capabilities,
    toggle_control: ControlId,
) -> tuple[ConfiguredBinding, ...]:
    policy = BindingPolicy(
        routing=Routing.MIRROR,
        lifetime=Lifetime.SESSION,
        duration_ms=0,
        emit_down=True,
        emit_up=False,
    )
    return tuple(
        ConfiguredBinding(BindingEntry(control_id, index), policy)
        for index, control_id in enumerate(
            controls_from_capabilities(capabilities, toggle_control), start=1
        )
    )


def event_record(event: DeviceEvent) -> dict[str, int | str | bool]:
    record: dict[str, int | str | bool] = {
        "event": event.event_type.name,
        "sequence": event.sequence,
        "binding_id": event.binding_id,
        "control_id": f"0x{int(event.control_id):04X}",
        "control_id_value": int(event.control_id),
        "control_kind": event.control_id.kind.name,
        "primary": event.control_id.primary,
        "secondary": event.control_id.secondary,
        "timestamp_ms": event.timestamp_ms,
        "flags": int(event.flags),
    }
    if event.event_type is EventType.CONTROL_EDGE:
        record["edge"] = event.edge.name
    elif event.event_type is EventType.MODE_CHANGED:
        record["mode_active"] = event.mode_active
    return record


def print_event(event: DeviceEvent, *, json_lines: bool) -> None:
    record = event_record(event)
    if json_lines:
        print(json.dumps(record, sort_keys=True), flush=True)
        return
    fields = [
        f"event={record['event']}",
        f"binding_id={record['binding_id']}",
        f"control_id={record['control_id']}",
        f"kind={record['control_kind']}",
        f"primary={record['primary']}",
        f"secondary={record['secondary']}",
    ]
    if "edge" in record:
        fields.append(f"edge={record['edge']}")
    if "mode_active" in record:
        fields.append(f"mode_active={record['mode_active']}")
    print(" ".join(fields), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive INSPECT confirmation.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one machine-readable JSON object per firmware event.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="Stop after this many seconds; zero listens until Ctrl-C.",
    )
    parser.add_argument(
        "--lease-ms",
        type=int,
        default=30_000,
        help="Renewable force-all lease in 1...30000 ms.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    if args.seconds < 0:
        raise HostInteractionError("--seconds cannot be negative.")
    if not 1 <= args.lease_ms <= 30_000:
        raise HostInteractionError("--lease-ms must be in 1...30000.")
    if not args.yes:
        confirmation = input(
            "Type INSPECT to install volatile MIRROR bindings and listen: "
        )
        if confirmation != "INSPECT":
            raise HostInteractionError("Confirmation did not match.")

    profile = load_device_profile(args.profile)
    toggle_control = ControlId.key(*profile.require_interaction().toggle_matrix)
    with HostInteractionProtocolClient.open_profile(
        profile, device_index=args.device_index
    ) as protocol:
        controller = HostInteractionController(protocol)
        bindings = inspection_bindings(controller.capabilities, toggle_control)
        controller.replace_bindings(bindings)
        controller.activate_all(lease_ms=args.lease_ms)

        print(
            f"listening controls={len(bindings)} "
            f"reserved_pause={toggle_control} routing=MIRROR",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
        renew_interval = max(0.001, args.lease_ms * 0.75 / 1000)
        renew_at = time.monotonic() + renew_interval

        while deadline is None or time.monotonic() < deadline:
            if time.monotonic() >= renew_at:
                controller.activate_all(lease_ms=args.lease_ms)
                renew_at = time.monotonic() + renew_interval
            for event in protocol.service(timeout_ms=250):
                print_event(event, json_lines=args.json)


def main() -> int:
    try:
        run(parse_args())
    except KeyboardInterrupt:
        return 0
    except (HostInteractionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
