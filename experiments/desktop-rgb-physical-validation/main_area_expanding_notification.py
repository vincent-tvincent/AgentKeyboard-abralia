#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Run a center-outward yellow-green notification over the typing region."""

from __future__ import annotations

import argparse
import math
import time

from abralia.rgb import BLACK, PhysicalSceneBuilder, RgbController, load_profile
from main_area_notification_flash import (
    DEFAULT_REGION,
    MINIMUM_WAIT_SECONDS,
    PEAK_BRIGHTNESS,
    RANDOM_WAIT_MAX_SECONDS,
    RandomSource,
    hold_scene,
    notification_delay,
    notification_scenes,
)
from main_area_sweep_notification import fade_scenes, play_frames, scaled_color


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--expand-ms", type=float, default=350.0)
    parser.add_argument("--pulse-ms", type=float, default=100.0)
    parser.add_argument("--fade-ms", type=float, default=150.0)
    parser.add_argument("--flashes", type=int, default=1)
    parser.add_argument("--flash-gap-ms", type=float, default=120.0)
    parser.add_argument("--tail-decay", type=float, default=0.5)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument(
        "--minimum-wait-seconds", type=float, default=MINIMUM_WAIT_SECONDS
    )
    parser.add_argument(
        "--random-wait-max-seconds", type=float, default=RANDOM_WAIT_MAX_SECONDS
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and validate the animation without opening a keyboard",
    )
    parser.add_argument("--yes", action="store_true", help="skip the EXPAND prompt")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.flashes <= 3:
        raise ValueError("--flashes must be in 1...3 for this bounded demo.")
    if not 40 <= args.flash_gap_ms <= 1000:
        raise ValueError("--flash-gap-ms must be in 40...1000.")
    if not 150 <= args.expand_ms <= 1200:
        raise ValueError("--expand-ms must be in 150...1200.")
    if not 40 <= args.pulse_ms <= 500:
        raise ValueError("--pulse-ms must be in 40...500.")
    if not 50 <= args.fade_ms <= 1000:
        raise ValueError("--fade-ms must be in 50...1000.")
    if not 0.1 <= args.tail_decay <= 0.9:
        raise ValueError("--tail-decay must be in 0.1...0.9.")
    if not 10 <= args.fps <= 30:
        raise ValueError("--fps must be in 10...30.")
    if not MINIMUM_WAIT_SECONDS <= args.minimum_wait_seconds <= 300:
        raise ValueError("--minimum-wait-seconds must be in 5...300.")
    if not 0 <= args.random_wait_max_seconds <= RANDOM_WAIT_MAX_SECONDS:
        raise ValueError("--random-wait-max-seconds must be in 0...30.")


def normalized_region_points(
    profile, region_id: str
) -> tuple[tuple[str, float], ...]:
    try:
        region = profile.regions[region_id]
    except KeyError as error:
        available = ", ".join(profile.regions)
        raise ValueError(
            f"Unknown RGB region {region_id!r}; available regions: {available}."
        ) from error
    raw_points = []
    for element_id in region.elements:
        element = profile.element_by_id[element_id]
        if not element.rgb_capable:
            continue
        point_x = (
            element.led_point.x
            if element.led_point is not None
            else element.geometry.x + element.geometry.width / 2
        )
        point_y = (
            element.led_point.y
            if element.led_point is not None
            else element.geometry.y + element.geometry.height / 2
        )
        raw_points.append((element_id, point_x, point_y))
    if not raw_points:
        raise ValueError(f"RGB region {region_id!r} has no renderable elements.")

    minimum_x = min(x for _element_id, x, _y in raw_points)
    maximum_x = max(x for _element_id, x, _y in raw_points)
    minimum_y = min(y for _element_id, _x, y in raw_points)
    maximum_y = max(y for _element_id, _x, y in raw_points)
    center_x = (minimum_x + maximum_x) / 2
    center_y = (minimum_y + maximum_y) / 2
    scale_x = max((maximum_x - minimum_x) / 2, 0.5)
    scale_y = max((maximum_y - minimum_y) / 2, 0.5)
    return tuple(
        (
            element_id,
            math.hypot((x - center_x) / scale_x, (y - center_y) / scale_y),
        )
        for element_id, x, y in raw_points
    )


def expanding_scenes(
    profile,
    region_id: str,
    *,
    duration_ms: float,
    fps: float,
    tail_decay: float,
):
    points = normalized_region_points(profile, region_id)
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    levels = temporal_tail_levels(points, steps=steps, tail_decay=tail_decay)
    return tuple(
        PhysicalSceneBuilder().build(
            f"main-area-expand-{step}",
            {
                element_id: scaled_color(level)
                for element_id, level in frame.items()
            },
            background=BLACK,
            owner="main-area-expanding-notification-demo",
        )
        for step, frame in enumerate(levels)
    )


def temporal_tail_levels(
    points: tuple[tuple[str, float], ...],
    *,
    steps: int,
    tail_decay: float,
) -> tuple[dict[str, float], ...]:
    minimum_radius = min(radius for _element_id, radius in points)
    maximum_radius = max(radius for _element_id, radius in points)
    radius_span = max(maximum_radius - minimum_radius, 1e-9)
    activation_step = {
        element_id: round((radius - minimum_radius) / radius_span * (steps - 1))
        for element_id, radius in points
    }
    levels = {element_id: 0.0 for element_id, _radius in points}
    frames = []
    for step in range(steps):
        levels = {
            element_id: level * tail_decay for element_id, level in levels.items()
        }
        for element_id, active_at in activation_step.items():
            if active_at == step:
                levels[element_id] = 1.0
        frames.append(levels.copy())
    return tuple(frames)


def run(args: argparse.Namespace, *, rng: RandomSource | None = None) -> float:
    validate_args(args)
    profile = load_profile(args.profile)
    _renderable, pulse, dark = notification_scenes(profile, args.region)
    expansion = expanding_scenes(
        profile,
        args.region,
        duration_ms=args.expand_ms,
        fps=args.fps,
        tail_decay=args.tail_decay,
    )
    fade = fade_scenes(
        profile,
        args.region,
        duration_ms=args.fade_ms,
        fps=args.fps,
    )
    print(
        f"EXPANDING_NOTIFICATION region={args.region} frames={len(expansion)} "
        f"fade_frames={len(fade)} flashes={args.flashes} "
        f"peak={PEAK_BRIGHTNESS} color=#A0FF00 "
        f"black_wait={args.minimum_wait_seconds:g}+random(0..{args.random_wait_max_seconds:g})s",
        flush=True,
    )
    if args.dry_run:
        return 0.0

    delay = notification_delay(
        args.minimum_wait_seconds,
        args.random_wait_max_seconds,
        rng=rng,
    )
    started = time.monotonic()
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        hold_scene(controller, dark, delay)
        for index in range(args.flashes):
            preceding_wait = delay if index == 0 else args.flash_gap_ms / 1000.0
            print(
                f"EXPANSION_TRIGGERED index={index + 1} "
                f"waited_seconds={preceding_wait:.3f} peak={PEAK_BRIGHTNESS}",
                flush=True,
            )
            play_frames(controller, expansion, args.expand_ms / 1000.0)
            hold_scene(controller, pulse, args.pulse_ms / 1000.0)
            play_frames(controller, fade, args.fade_ms / 1000.0)
            if index + 1 < args.flashes:
                hold_scene(controller, dark, args.flash_gap_ms / 1000.0)
        hold_scene(controller, dark, 0.12)
    return time.monotonic() - started


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.yes:
        confirmation = input(
            "Maximum-brightness yellow-green expansion ahead. Type EXPAND to continue: "
        )
        if confirmation != "EXPAND":
            print("Notification cancelled; no keyboard state was changed.")
            return 1
    try:
        elapsed = run(args)
    except KeyboardInterrupt:
        print("Interrupted; RGB snapshot restoration was requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    if args.dry_run:
        print("DRY_RUN_OK: no keyboard was opened.")
    else:
        print(f"EXPANDING_NOTIFICATION_COMPLETE_SECONDS={elapsed:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
