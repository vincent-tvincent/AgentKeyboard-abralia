#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Host-driven random-key breathing halo for custom V3 8K effect 25."""

from __future__ import annotations

import argparse
import math
import random
import signal
import sys
import time
from collections.abc import Callable

from keychron_rgb_demo import (
    DemoError,
    FrameFlags,
    FrameOperation,
    FrameState,
    FrameStatus,
    HSV,
    KeychronProtocol,
    PER_KEY_RGB_INDEPENDENT_V_EFFECT,
    RawHIDConnection,
    enumerate_keychron_candidates,
    restore,
    snapshot,
    write_frame,
)


# QMK g_led_config points from the pinned Keychron V3 8K ANSI encoder source.
LED_POSITIONS: tuple[tuple[int, int], ...] = (
    (0, 0), (16, 0), (29, 0), (42, 0), (55, 0), (71, 0), (84, 0),
    (97, 0), (110, 0), (126, 0), (139, 0), (152, 0), (165, 0),
    (198, 0), (211, 0), (224, 0),
    (0, 15), (13, 15), (26, 15), (39, 15), (52, 15), (65, 15),
    (78, 15), (91, 15), (104, 15), (117, 15), (130, 15), (143, 15),
    (156, 15), (176, 15), (198, 15), (211, 15), (224, 15),
    (3, 28), (19, 28), (32, 28), (45, 28), (59, 28), (72, 28),
    (85, 28), (98, 28), (111, 28), (124, 28), (137, 28), (150, 28),
    (163, 28), (179, 28), (198, 28), (211, 28), (224, 28),
    (5, 40), (23, 40), (36, 40), (49, 40), (62, 40), (75, 40),
    (88, 40), (101, 40), (114, 40), (127, 40), (140, 40), (153, 40),
    (174, 40),
    (8, 52), (29, 52), (42, 52), (55, 52), (68, 52), (81, 52),
    (94, 52), (107, 52), (120, 52), (133, 52), (146, 52), (168, 52),
    (211, 52),
    (2, 64), (18, 64), (34, 64), (83, 64), (131, 64), (148, 64),
    (164, 64), (175, 64), (198, 64), (211, 64), (224, 64),
)

# Comfortable, reduced-saturation RGBY palette. One entry is selected for an
# entire breath, so every illuminated key in that halo shares one color.
HALO_PALETTE: tuple[tuple[str, int, int], ...] = (
    ("coral red", 4, 235),
    ("mint green", 92, 160),
    ("soft azure", 160, 175),
    ("warm amber", 35, 235),
)


def breathing_envelope(progress: float) -> float:
    """Smooth 0 -> 1 -> 0 envelope with zero slope at both ends."""
    progress = min(1.0, max(0.0, progress))
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * progress)


def halo_frame(
    center_index: int,
    radius: float,
    power: float,
    palette_index: int,
) -> list[HSV]:
    if not 0 <= center_index < len(LED_POSITIONS):
        raise DemoError(f"Center LED must be in 0...{len(LED_POSITIONS) - 1}.")
    if radius <= 0:
        raise DemoError("Halo radius must be positive.")
    if power <= 0:
        raise DemoError("Halo power must be positive.")
    if not 0 <= palette_index < len(HALO_PALETTE):
        raise DemoError(
            f"Palette index must be in 0...{len(HALO_PALETTE) - 1}."
        )

    center_x, center_y = LED_POSITIONS[center_index]
    _name, hue, saturation = HALO_PALETTE[palette_index]
    frame: list[HSV] = []
    for x, y in LED_POSITIONS:
        distance = math.hypot(x - center_x, y - center_y)
        if distance >= radius:
            value = 0
        else:
            # Finite cosine falloff: full brightness at the center, a smooth
            # gradient within the halo, and exactly black at/outside its edge.
            cosine = 0.5 * (1.0 + math.cos(math.pi * distance / radius))
            value = round(255 * cosine**power)
        frame.append(HSV(hue=hue, saturation=saturation, value=value))
    return frame


def apply_mixed_color_cutoff(
    frame: list[HSV],
    global_brightness: int,
    minimum_visible_value: int,
) -> list[HSV]:
    """Turn off keys whose final V would be too low to preserve mixed color."""
    gated: list[HSV] = []
    for color in frame:
        effective_value = (
            color.value * global_brightness + 127
        ) // 255
        value = 0 if effective_value < minimum_visible_value else color.value
        gated.append(HSV(color.hue, color.saturation, value))
    return gated


def wait_for_status(
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
    raise DemoError(
        f"Timed out waiting for {description}; last status={last_status}."
    )


def commit_frame(
    protocol: KeychronProtocol,
    frame: list[HSV],
    sequence: int,
    write_colors: bool,
) -> None:
    if write_colors:
        write_frame(protocol, frame)
    protocol.frame_control(FrameOperation.COMMIT, sequence)
    wait_for_status(
        protocol,
        lambda status: bool(status.flags & FrameFlags.ACTIVE_VALID)
        and status.active_sequence == sequence
        and status.back_buffer_free,
        f"active frame sequence {sequence}",
    )


def choose_next_center(rng: random.Random, previous: int | None) -> int:
    if len(LED_POSITIONS) == 1:
        return 0
    center = rng.randrange(len(LED_POSITIONS))
    while center == previous:
        center = rng.randrange(len(LED_POSITIONS))
    return center


def run_idle_halo(args: argparse.Namespace) -> None:
    candidates = enumerate_keychron_candidates()
    usable = [candidate for candidate in candidates if candidate.path is not None]
    if args.device is not None:
        usable = [candidate for candidate in usable if candidate.scan_index == args.device]
    if len(usable) != 1:
        raise DemoError(
            f"Expected one selected Keychron Raw HID device, found {len(usable)}."
        )

    candidate = usable[0]
    rng = random.Random(args.seed)

    with RawHIDConnection(candidate.path) as connection:
        protocol = KeychronProtocol(connection)
        if protocol.led_count() != len(LED_POSITIONS):
            raise DemoError(
                f"Expected {len(LED_POSITIONS)} LEDs, device reports "
                f"{protocol.led_count()}."
            )

        original = snapshot(protocol, len(LED_POSITIONS))
        restore_required = False

        try:
            protocol.set_brightness(0)
            restore_required = True
            protocol.set_effect(PER_KEY_RGB_INDEPENDENT_V_EFFECT)
            protocol.set_brightness(0)

            status = protocol.frame_status()
            if status.state is not FrameState.AWAITING:
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

            sequence = 0
            previous_center: int | None = None
            completed_cycles = 0
            frame_interval = 1.0 / args.fps

            while args.cycles == 0 or completed_cycles < args.cycles:
                center = choose_next_center(rng, previous_center)
                previous_center = center
                palette_index = rng.randrange(len(HALO_PALETTE))
                frame = halo_frame(
                    center,
                    args.halo_radius,
                    args.halo_power,
                    palette_index,
                )

                visible_frame = apply_mixed_color_cutoff(
                    frame,
                    global_brightness=0,
                    minimum_visible_value=args.minimum_visible_value,
                )
                commit_frame(protocol, visible_frame, sequence, write_colors=True)
                sequence = (sequence + 1) & 0xFF
                last_commit = time.monotonic()
                cycle_start = last_commit
                next_tick = cycle_start

                x, y = LED_POSITIONS[center]
                color_name = HALO_PALETTE[palette_index][0]
                print(
                    f"Cycle {completed_cycles + 1}: center LED {center} "
                    f"at ({x},{y}), color={color_name}",
                    flush=True,
                )

                while True:
                    now = time.monotonic()
                    progress = (now - cycle_start) / args.duration
                    if progress >= 1.0:
                        protocol.set_brightness(0)
                        break

                    brightness = round(
                        args.brightness * breathing_envelope(progress)
                    )
                    next_visible_frame = apply_mixed_color_cutoff(
                        frame,
                        global_brightness=brightness,
                        minimum_visible_value=args.minimum_visible_value,
                    )
                    if next_visible_frame != visible_frame:
                        visible_frame = next_visible_frame
                        commit_frame(
                            protocol,
                            visible_frame,
                            sequence,
                            write_colors=True,
                        )
                        sequence = (sequence + 1) & 0xFF
                        last_commit = time.monotonic()
                    protocol.set_brightness(brightness)

                    if now - last_commit >= args.keepalive_seconds:
                        commit_frame(
                            protocol,
                            visible_frame,
                            sequence,
                            write_colors=False,
                        )
                        sequence = (sequence + 1) & 0xFF
                        last_commit = time.monotonic()

                    next_tick += frame_interval
                    delay = next_tick - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_tick = time.monotonic()

                completed_cycles += 1
        finally:
            if restore_required:
                protocol.set_brightness(0)
                try:
                    protocol.frame_control(FrameOperation.AWAIT)
                    wait_for_status(
                        protocol,
                        lambda current: current.state is FrameState.AWAITING,
                        "cleanup AWAITING state",
                    )
                finally:
                    restore(protocol, original)

                restored = snapshot(protocol, len(LED_POSITIONS))
                if restored != original:
                    raise DemoError("Idle-halo restoration readback mismatch.")
                print(
                    f"Restored effect={restored.effect}, "
                    f"brightness={restored.brightness}.",
                    flush=True,
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Animate a random-key breathing halo through custom RGB effect 25."
        )
    )
    parser.add_argument("--device", type=int, help="scanned Raw HID device index")
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="seconds for one complete fade-in/fade-out cycle (default: 10)",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=112,
        help="maximum temporary global brightness in 0...255 (default: 112)",
    )
    parser.add_argument(
        "--halo-radius",
        type=float,
        default=60.0,
        help="finite black-edged halo radius in QMK coordinate units (default: 60)",
    )
    parser.add_argument(
        "--halo-power",
        type=float,
        default=2.0,
        help="center-weighted falloff power; higher is tighter (default: 2)",
    )
    parser.add_argument(
        "--minimum-visible-value",
        type=int,
        default=8,
        help=(
            "turn mixed colors off below this final V to avoid primary-color "
            "quantization (default: 8)"
        ),
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=20.0,
        help="brightness updates per second (default: 20)",
    )
    parser.add_argument(
        "--keepalive-seconds",
        type=float,
        default=1.0,
        help="guarded-frame recommit interval below the firmware timeout (default: 1)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="number of random breaths; 0 runs until interrupted (default: 0)",
    )
    parser.add_argument("--seed", type=int, help="reproducible random seed")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive lighting confirmation",
    )
    args = parser.parse_args()

    if args.duration <= 0:
        parser.error("--duration must be positive")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be in 0...255")
    if args.halo_radius <= 0:
        parser.error("--halo-radius must be positive")
    if args.halo_power <= 0:
        parser.error("--halo-power must be positive")
    if not 0 <= args.minimum_visible_value <= 32:
        parser.error("--minimum-visible-value must be in 0...32")
    if args.fps <= 0 or args.fps > 60:
        parser.error("--fps must be in (0, 60]")
    if not 0 < args.keepalive_seconds < 2:
        parser.error("--keepalive-seconds must be in (0, 2)")
    if args.cycles < 0:
        parser.error("--cycles must be non-negative")
    return args


def main() -> int:
    args = parse_args()

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    print("WARNING — slow full-key brightness animation")
    print(
        "This demo temporarily animates the full RGB matrix and restores its "
        "complete original state afterward."
    )
    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        confirmation = input("Type HALO to continue, or anything else to cancel: ")
        if confirmation != "HALO":
            print("Idle-halo demo cancelled. No lighting state was changed.")
            return 0

    run_idle_halo(args)
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
