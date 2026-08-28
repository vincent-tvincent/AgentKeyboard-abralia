#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Display a volatile Codex-style >_ hero frame on the Keychron V3 8K."""

from __future__ import annotations

import argparse
import sys
import time

from keychron_rgb_demo import (
    DemoError,
    FrameOperation,
    FrameState,
    HSV,
    KeychronProtocol,
    PER_KEY_RGB_INDEPENDENT_V_EFFECT,
    RawHIDConnection,
    V3_8K_ANSI,
    enumerate_keychron_candidates,
    restore,
    snapshot,
)
from keychron_rgb_idle_halo import commit_frame, wait_for_status


# Final photographed layout:
#   > : F4, 5, T, F, C
#   _ : N, M, Comma, Dot
# Every other LED is exactly black.
CHEVRON_KEYS = ("F4", "5", "T", "F", "C")
CHEVRON_LEDS = (4, 21, 38, 54, 66)
UNDERSCORE_KEYS = ("N", "M", "Comma", "Dot")
UNDERSCORE_LEDS = (69, 70, 71, 72)

DEFAULT_BRIGHTNESS = 220
CHEVRON = HSV(92, 210, 255)
UNDERSCORE = HSV(155, 48, 255)


def build_frame() -> list[HSV]:
    frame = [HSV(0, 0, 0) for _ in range(V3_8K_ANSI.expected_led_count)]
    for index in CHEVRON_LEDS:
        frame[index] = CHEVRON
    for index in UNDERSCORE_LEDS:
        frame[index] = UNDERSCORE
    return frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brightness",
        type=int,
        default=DEFAULT_BRIGHTNESS,
        help="Temporary global brightness in 1...255 (default: 220).",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="Restore after this many seconds; 0 holds until Control-C.",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Keychron candidate index when more than one device is connected.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive LOGO confirmation.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    if not 1 <= args.brightness <= 255:
        raise DemoError("--brightness must be in 1...255.")
    if args.seconds < 0:
        raise DemoError("--seconds must be non-negative.")

    if not args.yes:
        print("This will temporarily replace the keyboard lighting with:")
        print(f"  mint-green > on {', '.join(CHEVRON_KEYS)}")
        print(f"  cool-white _ on {', '.join(UNDERSCORE_KEYS)}")
        print("  every other LED exactly black")
        confirmation = input("Type LOGO to continue: ").strip()
        if confirmation != "LOGO":
            raise DemoError("Confirmation did not match LOGO; no lighting changed.")

    candidates = [
        candidate
        for candidate in enumerate_keychron_candidates()
        if candidate.path is not None
        and candidate.vendor_id == V3_8K_ANSI.vendor_id
        and candidate.product_id == V3_8K_ANSI.product_id
    ]
    if args.device is not None:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.scan_index == args.device
        ]
    if len(candidates) != 1:
        raise DemoError(
            f"Expected one V3 8K Raw HID interface, found {len(candidates)}."
        )

    frame = build_frame()
    with RawHIDConnection(candidates[0].path) as connection:
        protocol = KeychronProtocol(connection)
        led_count = protocol.led_count()
        if led_count != len(frame):
            raise DemoError(f"Expected {len(frame)} LEDs, device reports {led_count}.")

        original = snapshot(protocol, led_count)
        restore_required = False
        try:
            protocol.set_brightness(0)
            restore_required = True
            protocol.set_effect(PER_KEY_RGB_INDEPENDENT_V_EFFECT)
            if protocol.effect() != PER_KEY_RGB_INDEPENDENT_V_EFFECT:
                raise DemoError("Effect 25 is unavailable on the connected firmware.")

            state = protocol.frame_status()
            if state.state is not FrameState.AWAITING:
                protocol.frame_control(FrameOperation.AWAIT)
                wait_for_status(
                    protocol,
                    lambda current: current.state is FrameState.AWAITING,
                    "AWAITING state",
                )

            protocol.frame_control(FrameOperation.BEGIN)
            wait_for_status(
                protocol,
                lambda current: current.state is FrameState.GUARDED
                and current.back_buffer_free,
                "GUARDED state",
            )

            sequence = 1
            commit_frame(protocol, frame, sequence, write_colors=True)
            protocol.set_brightness(args.brightness)
            print("Codex >_ hero frame is active.", flush=True)
            if args.seconds == 0:
                print("Press Control-C to restore prior lighting.", flush=True)

            deadline = None if args.seconds == 0 else time.monotonic() + args.seconds
            while deadline is None or time.monotonic() < deadline:
                time.sleep(1.0)
                sequence = (sequence + 1) & 0xFF
                commit_frame(protocol, frame, sequence, write_colors=False)
        except KeyboardInterrupt:
            print("Restoration requested.", flush=True)
        finally:
            if restore_required:
                restore(protocol, original)
                print("Prior lighting restored.", flush=True)


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DemoError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
