#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Full-frame RGB stress test for stock or explicit custom Keychron firmware."""

from __future__ import annotations

import argparse
import random
import signal
import statistics
import sys
import time
from collections.abc import Callable

from keychron_rgb_demo import (
    DemoError,
    DetectionResult,
    FrameFlags,
    FrameOperation,
    FrameState,
    FrameStatus,
    HSV,
    KeychronProtocol,
    PER_KEY_RGB_INDEPENDENT_V_EFFECT,
    RawHIDConnection,
    detect_candidate,
    enumerate_keychron_candidates,
    print_detection,
    restore,
    snapshot,
    write_frame,
)


def choose_stress_device(
    results: list[DetectionResult], requested_index: int | None
) -> DetectionResult:
    if requested_index is not None:
        matching = [
            result for result in results if result.candidate.scan_index == requested_index
        ]
        if not matching:
            raise DemoError(f"No scanned device has index {requested_index}.")
        selected = matching[0]
        if selected.support_level < 2 or not selected.led_count:
            raise DemoError(
                f"Device {requested_index} does not expose per-key color control."
            )
        return selected

    compatible = [
        result
        for result in results
        if result.support_level >= 2 and result.led_count is not None
    ]
    if len(compatible) == 1:
        return compatible[0]
    if not compatible:
        raise DemoError("No detected Keychron exposes per-key color control.")
    raise DemoError("Multiple compatible keyboards found; select one with --device INDEX.")


def random_frame(
    rng: random.Random,
    led_count: int,
    previous_hues: tuple[int, ...] | None,
    independent_value: bool = False,
) -> tuple[list[HSV], tuple[int, ...]]:
    if led_count > 256:
        raise DemoError(
            "This distinct-hue stress generator supports at most 256 LEDs per device."
        )

    # Sampling without replacement gives every key a different hue within a frame.
    # Regenerate if any physical LED would retain its previous hue, ensuring every
    # key changes on every update.
    for _attempt in range(100):
        hues = tuple(rng.sample(range(256), led_count))
        if previous_hues is None or all(
            current != previous for current, previous in zip(hues, previous_hues)
        ):
            values = [255] * led_count
            if independent_value:
                values = [rng.randint(1, 254) for _ in range(led_count)]
                values[0] = 0
                if led_count > 1:
                    values[1] = 255
                rng.shuffle(values)
            colors = [
                HSV(hue=hue, saturation=rng.randint(192, 255), value=value)
                for hue, value in zip(hues, values)
            ]
            return colors, hues
    raise DemoError("Could not generate a frame in which every LED changes hue.")


def run_stress_test(
    result: DetectionResult,
    cycles: int,
    hold_seconds: float,
    brightness: int,
    seed: int,
    report_every: int,
    per_key_effect_override: int | None,
    custom_independent_v: bool,
) -> None:
    candidate = result.candidate
    assert result.led_count is not None

    descriptor = candidate.descriptor
    if custom_independent_v and per_key_effect_override is None:
        per_key_effect = PER_KEY_RGB_INDEPENDENT_V_EFFECT
    elif per_key_effect_override is not None:
        per_key_effect = per_key_effect_override
    elif descriptor is not None:
        per_key_effect = descriptor.per_key_effect
    else:
        raise DemoError(
            "No per-key effect identifier is known for this model. "
            "Provide a verified value with --per-key-effect."
        )

    rng = random.Random(seed)
    packets_per_frame = (result.led_count + 8) // 9
    write_durations: list[float] = []
    frame_intervals: list[float] = []
    overruns = 0
    completed = 0
    previous_hues: tuple[int, ...] | None = None
    previous_frame_start: float | None = None

    print("\nSTEP 4 — Full-frame random-color stress test")
    print(f"    LEDs per frame: {result.led_count}")
    control_packets_per_frame = 1 if custom_independent_v else 0
    print(
        f"    HID packets per frame: {packets_per_frame} RGB"
        f" + {control_packets_per_frame} frame-control"
    )
    print(f"    Target interval: {hold_seconds:.4f} seconds")
    print(
        f"    Target rate: {1 / hold_seconds:.2f} frames/second"
        if hold_seconds > 0
        else "    Target rate: maximum throughput"
    )
    print(f"    Cycles: {cycles}; global brightness: {brightness}; random seed: {seed}")
    print(
        "    Renderer: effect 25 guarded independent-V"
        if custom_independent_v
        else f"    Renderer: stock-compatible effect {per_key_effect}"
    )

    with RawHIDConnection(candidate.path) as connection:
        protocol = KeychronProtocol(connection)
        original = snapshot(protocol, result.led_count)
        restore_required = False
        test_start = time.monotonic()
        next_frame_time = test_start

        try:
            protocol.set_brightness(0)
            restore_required = True
            protocol.set_per_key_type(0)
            protocol.set_effect(per_key_effect)

            if custom_independent_v:
                # Effect changes reload saved RGB config in this Keychron fork.
                # Reassert zero before exposing the direct shared-buffer path.
                protocol.set_brightness(0)
                # Exercise the unchanged Keychron write command in direct mode first.
                protocol.frame_control(FrameOperation.DIRECT)
                wait_for_frame_status(
                    protocol,
                    lambda status: status.state is FrameState.DIRECT
                    and not status.transition_queued,
                    "DIRECT state",
                )
                protocol.set_colors(0, [HSV(0, 255, 255)])
                protocol.set_brightness(brightness)
                time.sleep(0.1)

                protocol.frame_control(FrameOperation.BEGIN)
                wait_for_frame_status(
                    protocol,
                    lambda status: status.state is FrameState.GUARDED
                    and status.back_buffer_free,
                    "GUARDED state with a free back buffer",
                )
                test_start = time.monotonic()
                next_frame_time = test_start

            for cycle in range(cycles):
                if hold_seconds > 0:
                    remaining = next_frame_time - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    elif cycle > 0:
                        overruns += 1

                frame_start = time.monotonic()
                if previous_frame_start is not None:
                    frame_intervals.append(frame_start - previous_frame_start)
                previous_frame_start = frame_start

                frame, previous_hues = random_frame(
                    rng,
                    result.led_count,
                    previous_hues,
                    independent_value=custom_independent_v,
                )
                write_start = time.monotonic()
                write_frame(protocol, frame)
                if custom_independent_v:
                    sequence = cycle & 0xFF
                    protocol.frame_control(FrameOperation.COMMIT, sequence)
                    wait_for_frame_status(
                        protocol,
                        lambda status, expected=sequence: bool(
                            status.flags & FrameFlags.ACTIVE_VALID
                        )
                        and status.active_sequence == expected
                        and status.back_buffer_free,
                        f"active frame sequence {sequence}",
                    )
                elif cycle == 0:
                    protocol.set_brightness(brightness)
                write_durations.append(time.monotonic() - write_start)
                completed += 1

                if report_every > 0 and (
                    completed == 1
                    or completed % report_every == 0
                    or completed == cycles
                ):
                    print(
                        f"    Frame {completed}/{cycles}: "
                        f"write={write_durations[-1] * 1000:.1f} ms"
                    )

                next_frame_time = test_start + completed * hold_seconds

            if custom_independent_v:
                print("    Waiting for the two-second guarded-frame timeout...")
                wait_for_frame_status(
                    protocol,
                    lambda status: status.state is FrameState.AWAITING,
                    "automatic AWAITING timeout",
                    timeout_seconds=3.0,
                )
        finally:
            if restore_required:
                if custom_independent_v:
                    try:
                        protocol.frame_control(FrameOperation.AWAIT)
                    except DemoError:
                        pass
                restore(protocol, original)

        restored = snapshot(protocol, result.led_count)
        if restored != original:
            raise DemoError(
                "Post-stress restoration readback does not match the original snapshot."
            )

    total_duration = time.monotonic() - test_start
    achieved_rate = completed / total_duration if total_duration > 0 else 0
    average_write = statistics.fmean(write_durations) if write_durations else 0
    maximum_write = max(write_durations, default=0)
    average_interval = statistics.fmean(frame_intervals) if frame_intervals else 0

    print("\nSTEP 5 — Stress-test result")
    print(f"    Frames completed: {completed}/{cycles}")
    print(
        "    Total HID frame packets: "
        f"{completed * (packets_per_frame + control_packets_per_frame)}"
    )
    print(f"    Average frame write: {average_write * 1000:.1f} ms")
    print(f"    Maximum frame write: {maximum_write * 1000:.1f} ms")
    if frame_intervals:
        print(f"    Average start-to-start interval: {average_interval * 1000:.1f} ms")
    print(f"    End-to-end achieved rate including restoration: {achieved_rate:.2f} fps")
    print(f"    Schedule overruns: {overruns}")
    print("    Full restoration readback matches the original state.")
    print("    Visual correctness still requires observing the physical keyboard.")


def wait_for_frame_status(
    protocol: KeychronProtocol,
    predicate: Callable[[FrameStatus], bool],
    description: str,
    timeout_seconds: float = 1.0,
) -> FrameStatus:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        last_status = protocol.frame_status()
        if predicate(last_status):
            return last_status
        time.sleep(0.005)
    raise DemoError(f"Timed out waiting for {description}; last status={last_status}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stress-test Keychron per-key RGB with random full frames."
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
        "--cycles",
        type=int,
        default=100,
        help="number of random full-key frames (default: 100)",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=0.05,
        help="target start-to-start interval in seconds (default: 0.05)",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=96,
        help="temporary global brightness in 0...255 (default: 96)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="reproducible random seed; generated automatically when omitted",
    )
    parser.add_argument(
        "--report-every",
        type=int,
        default=10,
        help="print timing every N frames; 0 disables progress output (default: 10)",
    )
    parser.add_argument(
        "--per-key-effect",
        type=int,
        help="verified per-key RGB Matrix effect ID for an unprofiled model",
    )
    parser.add_argument(
        "--custom-independent-v",
        action="store_true",
        help=(
            "require custom effect 25 and test direct writes, guarded frame commits, "
            "independent V values, and timeout recovery"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive rapid-color warning",
    )
    args = parser.parse_args()

    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.hold_seconds < 0:
        parser.error("--hold-seconds must be non-negative")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be in 0...255")
    if args.report_every < 0:
        parser.error("--report-every must be non-negative")
    if args.per_key_effect is not None and not 0 <= args.per_key_effect <= 255:
        parser.error("--per-key-effect must be in 0...255")
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

    selected = choose_stress_device(results, args.device)
    seed = args.seed if args.seed is not None else random.SystemRandom().getrandbits(64)

    print("\nWARNING — rapid full-key color changes")
    print(
        "This test changes the entire keyboard repeatedly and may be unsuitable "
        "for people with photosensitivity. Do not stare at the keyboard."
    )
    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        confirmation = input("Type STRESS to continue, or anything else to cancel: ")
        if confirmation != "STRESS":
            print("Stress test cancelled. No lighting state was changed.")
            return 0

    run_stress_test(
        selected,
        cycles=args.cycles,
        hold_seconds=args.hold_seconds,
        brightness=args.brightness,
        seed=seed,
        report_every=args.report_every,
        per_key_effect_override=args.per_key_effect,
        custom_independent_v=args.custom_independent_v,
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
