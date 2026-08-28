#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Fade selected Keychron keys from vivid colors into a white background."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from collections.abc import Sequence

from keychron_rgb_demo import (
    DemoError,
    DetectionResult,
    HSV,
    KeychronProtocol,
    RawHIDConnection,
    choose_result,
    detect_candidate,
    enumerate_keychron_candidates,
    print_detection,
    restore,
    snapshot,
    write_frame,
)


KEY_PALETTE = (
    ("red", 0),
    ("green", 85),
    ("blue", 170),
    ("magenta", 213),
    ("amber", 32),
)


def parse_keys(value: str) -> tuple[str, ...]:
    keys = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if not keys:
        raise argparse.ArgumentTypeError("--keys requires at least one comma-separated key")
    if len(keys) > len(KEY_PALETTE):
        raise argparse.ArgumentTypeError(
            f"this demo supports at most {len(KEY_PALETTE)} selected keys"
        )
    if len(set(keys)) != len(keys):
        raise argparse.ArgumentTypeError("--keys must not contain duplicates")
    return keys


def write_indexed_colors(
    protocol: KeychronProtocol,
    indexed_colors: Sequence[tuple[int, HSV]],
) -> int:
    """Write selected colors while batching adjacent LED indices."""
    ordered = sorted(indexed_colors, key=lambda item: item[0])
    packets = 0
    group_start: int | None = None
    group_colors: list[HSV] = []
    previous_index: int | None = None

    def flush() -> None:
        nonlocal packets, group_start, group_colors
        if group_start is not None and group_colors:
            protocol.set_colors(group_start, group_colors)
            packets += 1
        group_start = None
        group_colors = []

    for index, color in ordered:
        if (
            group_start is None
            or previous_index is None
            or index != previous_index + 1
            or len(group_colors) == 9
        ):
            flush()
            group_start = index
        group_colors.append(color)
        previous_index = index
    flush()
    return packets


def run_fade(
    result: DetectionResult,
    selected_keys: tuple[str, ...],
    steps: int,
    step_seconds: float,
    start_saturation: int,
    brightness: int,
    final_hold_seconds: float,
) -> None:
    candidate = result.candidate
    descriptor = candidate.descriptor
    assert descriptor is not None
    assert result.led_count is not None

    unknown_keys = [key for key in selected_keys if key not in descriptor.keys]
    if unknown_keys:
        available = ", ".join(sorted(descriptor.keys))
        raise DemoError(
            f"Descriptor has no mapping for {', '.join(unknown_keys)}. "
            f"Available demo keys: {available}."
        )

    assignments = {
        key: KEY_PALETTE[offset]
        for offset, key in enumerate(selected_keys)
    }
    white = HSV(0, 0, 255)
    packets_per_selected_update = 0
    completed_steps = 0

    print("\nSTEP 4 — White-background saturation fade")
    print(f"    LEDs: {result.led_count}; global brightness: {brightness}")
    print(f"    Fade steps: {steps}; interval: {step_seconds:.3f} seconds")
    print(f"    Approximate fade duration: {steps * step_seconds:.2f} seconds")
    print(f"    Starting saturation: {start_saturation}; ending saturation: 0")
    print(
        "    Selected keys: "
        + ", ".join(
            f"{key}={assignments[key][0]}" for key in selected_keys
        )
    )

    with RawHIDConnection(candidate.path) as connection:
        protocol = KeychronProtocol(connection)
        original = snapshot(protocol, result.led_count)
        restore_required = False

        try:
            protocol.set_brightness(0)
            restore_required = True

            led_by_key: dict[str, int] = {}
            print("    Key-to-LED mapping:")
            for key in selected_keys:
                coordinate = descriptor.keys[key]
                index = protocol.led_index(coordinate)
                if index >= result.led_count:
                    raise DemoError(f"{key} returned out-of-range LED index {index}.")
                led_by_key[key] = index
                print(
                    f"      {key}: matrix {coordinate.row},{coordinate.column} -> LED {index}"
                )

            initial_frame = [white] * result.led_count
            for key in selected_keys:
                _name, hue = assignments[key]
                initial_frame[led_by_key[key]] = HSV(hue, start_saturation, 255)

            protocol.set_per_key_type(0)
            write_frame(protocol, initial_frame)
            protocol.set_effect(descriptor.per_key_effect)
            protocol.set_brightness(brightness)

            fade_start = time.monotonic()
            next_step_time = fade_start
            for step in range(steps + 1):
                if step > 0:
                    remaining = next_step_time - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)

                saturation = round(start_saturation * (1 - step / steps))
                selected_colors = [
                    (
                        led_by_key[key],
                        HSV(assignments[key][1], saturation, 255),
                    )
                    for key in selected_keys
                ]
                packets_per_selected_update = write_indexed_colors(
                    protocol, selected_colors
                )
                completed_steps += 1

                if step == 0 or step == steps or step % max(1, steps // 5) == 0:
                    print(f"    Step {step}/{steps}: saturation={saturation}")
                next_step_time = fade_start + (step + 1) * step_seconds

            if final_hold_seconds > 0:
                print(
                    f"    All selected keys are now white; holding for "
                    f"{final_hold_seconds:g} seconds."
                )
                time.sleep(final_hold_seconds)
        finally:
            if restore_required:
                restore(protocol, original)

        restored = snapshot(protocol, result.led_count)
        if restored != original:
            raise DemoError(
                "Post-fade restoration readback does not match the original snapshot."
            )

    print("\nSTEP 5 — Fade result")
    print(f"    Saturation frames completed: {completed_steps}")
    print(f"    HID packets per selected-key update: {packets_per_selected_update}")
    print("    Selected keys reached saturation 0 and visually merged into white.")
    print("    Full restoration readback matches the original state.")
    print("    Visual correctness still requires observing the physical keyboard.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fade selected Keychron keys into a white RGB background."
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="scan and detect support without changing lighting",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="scanned device index to use when multiple keyboards are present",
    )
    parser.add_argument(
        "--keys",
        type=parse_keys,
        default=parse_keys("W,A,S,D,SPACE"),
        help="comma-separated selected keys (default: W,A,S,D,SPACE)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=30,
        help="number of saturation reductions (default: 30)",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=0.1,
        help="target interval between saturation steps (default: 0.1)",
    )
    parser.add_argument(
        "--start-saturation",
        type=int,
        default=255,
        help="initial selected-key saturation in 0...255 (default: 255)",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=96,
        help="temporary global brightness in 0...255 (default: 96)",
    )
    parser.add_argument(
        "--final-hold-seconds",
        type=float,
        default=1.0,
        help="seconds to hold the all-white result before restoration (default: 1)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="run the lighting phase without interactive confirmation",
    )
    args = parser.parse_args()

    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.step_seconds < 0:
        parser.error("--step-seconds must be non-negative")
    if not 0 <= args.start_saturation <= 255:
        parser.error("--start-saturation must be in 0...255")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be in 0...255")
    if args.final_hold_seconds < 0:
        parser.error("--final-hold-seconds must be non-negative")
    return args


def main() -> int:
    args = parse_args()

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    print("STEP 1 — Scan connected HID interfaces for Keychron devices")
    candidates = enumerate_keychron_candidates()
    if not candidates:
        raise DemoError("No connected Keychron HID device was found.")
    print(f"    Found {len(candidates)} Keychron device candidate(s).")

    print("\nSTEP 2 — Negotiate stock-firmware support levels")
    results = [detect_candidate(candidate) for candidate in candidates]
    print("    Detection used read-only protocol, feature, and color-buffer queries.")
    print_detection(results)

    if args.probe_only:
        print("\nProbe-only run complete. No lighting state was changed.")
        return 0

    selected = choose_result(results, args.device)
    if not selected.demo_ready:
        raise DemoError(
            f"Device {selected.candidate.scan_index} is not ready for this fade demo."
        )

    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        input("\nPress Enter to display the saturation fade, or Control-C to cancel: ")

    run_fade(
        selected,
        selected_keys=args.keys,
        steps=args.steps,
        step_seconds=args.step_seconds,
        start_saturation=args.start_saturation,
        brightness=args.brightness,
        final_hold_seconds=args.final_hold_seconds,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; restoration was requested.", file=sys.stderr)
        raise SystemExit(130)
    except DemoError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
