#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""White keyboard -> F1 status -> expand/fill -> breathing status-color reminder."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import re
import time

from abralia.rgb import AbstractScene, PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from abralia.rgb.colors import LinearRgb, linear_to_srgb8, to_linear_rgb
from main_area_expanding_notification import normalized_region_points, play_frames
from main_area_notification_flash import DEFAULT_REGION, FLASH_COLOR, hold_scene
from white_idle_expanding_notification import (
    blend,
    edge_to_center_fill_scenes,
    expanding_notification_scenes,
    percent_to_value,
    white,
)


OWNER = "f1-breathing-notification-demo"


@dataclass(frozen=True)
class Phase:
    name: str
    frames: tuple[AbstractScene, ...]
    seconds: float


def hex_color(value: str) -> Srgb8:
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError("Use a six-digit RGB color, such as 2080FF.")
    digits = value.removeprefix("#")
    color = Srgb8(*(int(digits[i:i + 2], 16) for i in (0, 2, 4)))
    if color == Srgb8(0, 0, 0):
        raise argparse.ArgumentTypeError("The demo status color must be visible, not black.")
    return color


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--profile", required=True, help="explicit profile ID or JSON path")
    parser.add_argument("--region", default=DEFAULT_REGION, help="notification region")
    parser.add_argument("--slot-element", default="F1", help="physical profile element for the slot")
    parser.add_argument("--status-color", type=hex_color, default="2080FF", help="demo color; not a production theme")
    parser.add_argument("--white-brightness-percent", type=float, default=100.0, help="initial and surrounding white value")
    parser.add_argument("--idle-seconds", type=float, default=5.0, help="all-white initial interval")
    parser.add_argument("--slot-seconds", type=float, default=2.0, help="show the assigned slot before notification")
    parser.add_argument("--prepare-ms", type=float, default=180.0, help="lower only the notification region to 10% white")
    parser.add_argument("--expand-ms", type=float, default=200.0, help="center-outward expansion")
    parser.add_argument("--edge-fill-ms", type=float, default=100.0, help="edge-to-center fill")
    parser.add_argument("--peak-ms", type=float, default=100.0, help="filled peak before the first breath")
    parser.add_argument("--breath-seconds", type=float, default=12.0, help="bounded reminder duration")
    parser.add_argument("--breath-period", type=float, default=2.0, help="seconds per breathing cycle")
    parser.add_argument("--color-blend-seconds", type=float, default=4.0, help="time to reach status color and settled intensity")
    parser.add_argument("--breath-min-percent", type=float, default=12.0, help="minimum relative linear-light intensity")
    parser.add_argument("--breath-max-percent", type=float, default=50.0, help="settled maximum relative linear-light intensity")
    parser.add_argument("--post-seconds", type=float, default=3.0, help="white plus status slot after clearing the reminder")
    parser.add_argument("--fps", type=float, default=30.0, help="requested frame rate")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--dry-run", action="store_true", help="build/check every frame without opening a keyboard")
    parser.add_argument("--yes", action="store_true", help="skip the typed BREATHE confirmation")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return argument_parser().parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    bounds = {
        "white_brightness_percent": (1, 100), "idle_seconds": (1, 300),
        "slot_seconds": (0.1, 60), "prepare_ms": (50, 1000),
        "expand_ms": (150, 1200), "edge_fill_ms": (100, 800),
        "peak_ms": (40, 500), "breath_seconds": (2, 120),
        "breath_period": (1.5, 10), "color_blend_seconds": (1, 30),
        "breath_min_percent": (1, 90), "breath_max_percent": (2, 100),
        "post_seconds": (0.1, 60), "fps": (10, 30),
    }
    for name, (low, high) in bounds.items():
        value = getattr(args, name)
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and in {low}...{high}.")
    if args.breath_min_percent >= args.breath_max_percent:
        raise ValueError("Breathing minimum must be less than its maximum.")
    if args.breath_seconds < args.color_blend_seconds + args.breath_period:
        raise ValueError("Allow at least one full breathing cycle after the color blend.")


def breathing_color(args: argparse.Namespace, elapsed: float) -> Srgb8:
    fraction = min(1.0, max(0.0, elapsed / args.color_blend_seconds))
    fraction = fraction * fraction * (3 - 2 * fraction)
    upper = 1 + (args.breath_max_percent / 100 - 1) * fraction
    lower = args.breath_min_percent / 100
    wave = (1 + math.cos(2 * math.pi * elapsed / args.breath_period)) / 2
    level = lower + (upper - lower) * wave
    start = to_linear_rgb(FLASH_COLOR)
    end = to_linear_rgb(args.status_color)
    return linear_to_srgb8(LinearRgb(*(
        min(1.0, max(0.0, (a + (b - a) * fraction) * level))
        for a, b in zip(
            (start.red, start.green, start.blue),
            (end.red, end.green, end.blue),
        )
    )))


def build_phases(args: argparse.Namespace) -> tuple[Phase, ...]:
    validate_args(args)
    profile = load_profile(args.profile)
    slot = profile.element_by_id.get(args.slot_element)
    if slot is None or not slot.rgb_capable:
        raise ValueError(f"Slot {args.slot_element!r} must be an RGB-capable profile element.")
    function_row = profile.regions.get("function_row")
    if function_row is None or args.slot_element not in function_row.elements:
        raise ValueError("The demo slot must belong to the profile's function_row region.")
    region_ids = tuple(element_id for element_id, _ in normalized_region_points(profile, args.region))
    if set(region_ids) & set(function_row.elements):
        raise ValueError("The notification region must not overlap the function-row status region.")
    idle = white(percent_to_value(args.white_brightness_percent))
    base_value = percent_to_value(min(args.white_brightness_percent, 10))
    base = white(base_value)

    def scene(name: str, colors: dict[str, Srgb8], *, with_slot: bool = True) -> AbstractScene:
        return PhysicalSceneBuilder().build(
            name,
            {**colors, **({args.slot_element: args.status_color} if with_slot else {})},
            background=idle,
            owner=OWNER,
        )

    def region_scene(name: str, color: Srgb8) -> AbstractScene:
        return scene(name, dict.fromkeys(region_ids, color))

    prepare_steps = max(2, math.ceil(args.prepare_ms / 1000 * args.fps))
    prepare = tuple(region_scene(
        f"prepare-{step}", blend(idle, base, step / prepare_steps)
    ) for step in range(1, prepare_steps + 1))
    expansion = expanding_notification_scenes(
        profile, args.region, base_value,
        duration_ms=args.expand_ms, fps=args.fps, tail_decay=0.5,
    )
    fill = edge_to_center_fill_scenes(
        profile, args.region, base_value,
        duration_ms=args.edge_fill_ms, fps=args.fps,
        starting_colors=expansion[-1].payload.colors,
    )
    # Keep the existing onset geometry/tail while preserving white outside its region.
    expansion = tuple(scene(frame.scene_id, dict(frame.payload.colors)) for frame in expansion)
    fill = tuple(scene(frame.scene_id, dict(frame.payload.colors)) for frame in fill)
    breath_steps = max(2, math.ceil(args.breath_seconds * args.fps))
    breath = tuple(region_scene(
        f"breathe-{step}", breathing_color(args, args.breath_seconds * step / (breath_steps - 1))
    ) for step in range(breath_steps))
    # This is an explicit timed end of the demo, not a fade between fill and breathing.
    last_color = breath[-1].payload.colors[region_ids[0]]
    clear_steps = max(2, math.ceil(0.3 * args.fps))
    clear = tuple(region_scene(
        f"clear-{step}", blend(last_color, idle, step / clear_steps)
    ) for step in range(1, clear_steps + 1))
    return (
        Phase("WHITE_EVERYWHERE", (scene("initial-white", {}, with_slot=False),), args.idle_seconds),
        Phase("F1_STATUS", (scene("slot-status", {}),), args.slot_seconds),
        Phase("PREPARE_REGION", prepare, args.prepare_ms / 1000),
        Phase("EXPAND", expansion, args.expand_ms / 1000),
        Phase("FILL", fill, args.edge_fill_ms / 1000),
        Phase("FILLED_PEAK", (region_scene("peak", FLASH_COLOR),), args.peak_ms / 1000),
        Phase("BREATHE_AND_BLEND", breath, args.breath_seconds),
        Phase("CLEAR_NOTIFICATION", clear, 0.3),
        Phase("WHITE_WITH_STATUS", (scene("post-white-status", {}),), args.post_seconds),
    )


def play_demo(controller: RgbController, phases: tuple[Phase, ...]) -> None:
    for phase in phases:
        print(f"PHASE {phase.name} seconds={phase.seconds:g}", flush=True)
        if len(phase.frames) == 1:
            hold_scene(controller, phase.frames[0], phase.seconds)
        else:
            play_frames(controller, phase.frames, phase.seconds)


def run(args: argparse.Namespace) -> float:
    phases = build_phases(args)
    print(
        f"F1_BREATHING_DEMO slot={args.slot_element} region={args.region} "
        f"status={args.status_color.to_json()} total_seconds={sum(p.seconds for p in phases):g} "
        f"frames={sum(len(p.frames) for p in phases)} RGB_ONLY",
        flush=True,
    )
    if args.dry_run:
        for phase in phases:
            print(f"CHECKED {phase.name} frames={len(phase.frames)} seconds={phase.seconds:g}")
        return 0.0
    started = time.monotonic()
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        play_demo(controller, phases)
    return time.monotonic() - started


def main() -> int:
    args = parse_args()
    try:
        # Validate before requesting input or opening the device.
        build_phases(args)
        if not args.dry_run and not args.yes:
            if input("White backlight and bounded notification demo. Type BREATHE to start: ") != "BREATHE":
                print("Cancelled; no keyboard was opened.")
                return 1
        elapsed = run(args)
    except KeyboardInterrupt:
        print("Interrupted; any opened controller requests RGB snapshot restoration on exit.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print("DRY_RUN_OK: no keyboard was opened." if args.dry_run else f"DEMO_COMPLETE seconds={elapsed:.3f}; RGB snapshot restored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
