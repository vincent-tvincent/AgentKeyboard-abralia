#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Show white idle lighting around a maximum-brightness expanding notification."""

from __future__ import annotations

import argparse
import math
import time

from abralia.rgb import PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from main_area_expanding_notification import (
    normalized_region_points,
    play_frames,
    temporal_tail_levels,
)
from main_area_notification_flash import (
    DEFAULT_REGION,
    FLASH_COLOR,
    MINIMUM_WAIT_SECONDS,
    PEAK_BRIGHTNESS,
    RANDOM_WAIT_MAX_SECONDS,
    RandomSource,
    hold_scene,
    notification_delay,
)


DEFAULT_IDLE_PERCENT = 50.0
NOTIFICATION_BASE_MAX_PERCENT = 10.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--idle-brightness-percent",
        type=float,
        default=DEFAULT_IDLE_PERCENT,
        help="white idle brightness in 1...100 percent (default: 50)",
    )
    parser.add_argument("--expand-ms", type=float, default=350.0)
    parser.add_argument("--edge-fill-ms", type=float, default=220.0)
    parser.add_argument("--pulse-ms", type=float, default=100.0)
    parser.add_argument("--notification-fade-ms", type=float, default=150.0)
    parser.add_argument("--idle-transition-ms", type=float, default=180.0)
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
    parser.add_argument(
        "--preview-after-5s",
        action="store_true",
        help="start the notification sequence after a fixed five-second idle wait",
    )
    parser.add_argument(
        "--post-seconds",
        type=float,
        default=3.0,
        help="seconds to show restored white idle before snapshot restoration (default: 3)",
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and validate the animation without opening a keyboard",
    )
    parser.add_argument("--yes", action="store_true", help="skip the IDLE prompt")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.flashes <= 3:
        raise ValueError("--flashes must be in 1...3 for this bounded demo.")
    if not 40 <= args.flash_gap_ms <= 1000:
        raise ValueError("--flash-gap-ms must be in 40...1000.")
    if not 1 <= args.idle_brightness_percent <= 100:
        raise ValueError("--idle-brightness-percent must be in 1...100.")
    if not 150 <= args.expand_ms <= 1200:
        raise ValueError("--expand-ms must be in 150...1200.")
    if not 100 <= args.edge_fill_ms <= 800:
        raise ValueError("--edge-fill-ms must be in 100...800.")
    if not 40 <= args.pulse_ms <= 500:
        raise ValueError("--pulse-ms must be in 40...500.")
    if not 50 <= args.notification_fade_ms <= 1000:
        raise ValueError("--notification-fade-ms must be in 50...1000.")
    if not 50 <= args.idle_transition_ms <= 1000:
        raise ValueError("--idle-transition-ms must be in 50...1000.")
    if not 0.1 <= args.tail_decay <= 0.9:
        raise ValueError("--tail-decay must be in 0.1...0.9.")
    if not 10 <= args.fps <= 30:
        raise ValueError("--fps must be in 10...30.")
    if not MINIMUM_WAIT_SECONDS <= args.minimum_wait_seconds <= 300:
        raise ValueError("--minimum-wait-seconds must be in 5...300.")
    if not 0 <= args.random_wait_max_seconds <= RANDOM_WAIT_MAX_SECONDS:
        raise ValueError("--random-wait-max-seconds must be in 0...30.")
    if not 0 <= args.post_seconds <= 60:
        raise ValueError("--post-seconds must be in 0...60.")


def percent_to_value(percent: float) -> int:
    return round(PEAK_BRIGHTNESS * percent / 100.0)


def white(value: int) -> Srgb8:
    return Srgb8(value, value, value)


def blend(start: Srgb8, end: Srgb8, progress: float) -> Srgb8:
    bounded = min(1.0, max(0.0, progress))
    return Srgb8(
        round(start.red + (end.red - start.red) * bounded),
        round(start.green + (end.green - start.green) * bounded),
        round(start.blue + (end.blue - start.blue) * bounded),
    )


def idle_scene(value: int, scene_id: str):
    return PhysicalSceneBuilder().build(
        scene_id,
        {},
        background=white(value),
        owner="white-idle-expanding-notification-demo",
    )


def notification_base_value(idle_percent: float) -> int:
    return percent_to_value(min(idle_percent, NOTIFICATION_BASE_MAX_PERCENT))


def white_transition_scenes(
    start_value: int,
    end_value: int,
    *,
    duration_ms: float,
    fps: float,
    scene_prefix: str,
):
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    return tuple(
        idle_scene(
            round(start_value + (end_value - start_value) * step / steps),
            f"{scene_prefix}-{step}",
        )
        for step in range(1, steps + 1)
    )


def notification_scene(
    profile, region_id: str, base_value: int, level: float, name: str
):
    points = normalized_region_points(profile, region_id)
    base = white(base_value)
    color = blend(base, FLASH_COLOR, level)
    return PhysicalSceneBuilder().build(
        name,
        {element_id: color for element_id, _radius in points},
        background=base,
        owner="white-idle-expanding-notification-demo",
    )


def expanding_notification_scenes(
    profile,
    region_id: str,
    base_value: int,
    *,
    duration_ms: float,
    fps: float,
    tail_decay: float,
):
    points = normalized_region_points(profile, region_id)
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    levels = temporal_tail_levels(points, steps=steps, tail_decay=tail_decay)
    base = white(base_value)
    return tuple(
        PhysicalSceneBuilder().build(
            f"white-idle-expand-{step}",
            {
                element_id: blend(base, FLASH_COLOR, level)
                for element_id, level in frame.items()
            },
            background=base,
            owner="white-idle-expanding-notification-demo",
        )
        for step, frame in enumerate(levels)
    )


def edge_to_center_fill_scenes(
    profile,
    region_id: str,
    base_value: int,
    *,
    duration_ms: float,
    fps: float,
    starting_colors,
    softness: float = 0.25,
):
    points = normalized_region_points(profile, region_id)
    maximum_radius = max(radius for _element_id, radius in points)
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    base = white(base_value)
    scenes = []
    for step in range(steps):
        progress = step / (steps - 1)
        frontier = maximum_radius * (1.0 - progress)
        colors = {}
        for element_id, radius in points:
            edge_progress = min(
                1.0,
                max(0.0, (radius - frontier + softness) / softness),
            )
            level = edge_progress * edge_progress * (3.0 - 2.0 * edge_progress)
            current = starting_colors.get(element_id, base)
            colors[element_id] = blend(current, FLASH_COLOR, level)
        scenes.append(
            PhysicalSceneBuilder().build(
                f"white-idle-edge-fill-{step}",
                colors,
                background=base,
                owner="white-idle-expanding-notification-demo",
            )
        )
    return tuple(scenes)


def notification_fade_scenes(
    profile,
    region_id: str,
    base_value: int,
    *,
    duration_ms: float,
    fps: float,
):
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    return tuple(
        notification_scene(
            profile,
            region_id,
            base_value,
            1.0 - step / steps,
            f"white-idle-notification-fade-{step}",
        )
        for step in range(1, steps + 1)
    )


def selected_wait_seconds(
    args: argparse.Namespace, *, rng: RandomSource | None = None
) -> float:
    if args.preview_after_5s:
        return MINIMUM_WAIT_SECONDS
    return notification_delay(
        args.minimum_wait_seconds,
        args.random_wait_max_seconds,
        rng=rng,
    )


def run(args: argparse.Namespace, *, rng: RandomSource | None = None) -> float:
    validate_args(args)
    profile = load_profile(args.profile)
    idle_value = percent_to_value(args.idle_brightness_percent)
    base_value = notification_base_value(args.idle_brightness_percent)
    idle = idle_scene(idle_value, "white-idle")
    base = idle_scene(base_value, "white-notification-base")
    expansion = expanding_notification_scenes(
        profile,
        args.region,
        base_value,
        duration_ms=args.expand_ms,
        fps=args.fps,
        tail_decay=args.tail_decay,
    )
    edge_fill = edge_to_center_fill_scenes(
        profile,
        args.region,
        base_value,
        duration_ms=args.edge_fill_ms,
        fps=args.fps,
        starting_colors=expansion[-1].payload.colors,
    )
    pulse = notification_scene(
        profile, args.region, base_value, 1.0, "notification-peak"
    )
    notification_fade = notification_fade_scenes(
        profile,
        args.region,
        base_value,
        duration_ms=args.notification_fade_ms,
        fps=args.fps,
    )
    lower_idle = white_transition_scenes(
        idle_value,
        base_value,
        duration_ms=args.idle_transition_ms,
        fps=args.fps,
        scene_prefix="lower-idle",
    )
    restore_idle = white_transition_scenes(
        base_value,
        idle_value,
        duration_ms=args.idle_transition_ms,
        fps=args.fps,
        scene_prefix="restore-idle",
    )
    wait_description = (
        "fixed(5)s"
        if args.preview_after_5s
        else (
            f"{args.minimum_wait_seconds:g}"
            f"+random(0..{args.random_wait_max_seconds:g})s"
        )
    )
    print(
        f"WHITE_IDLE_NOTIFICATION idle_percent={args.idle_brightness_percent:g} "
        f"idle_value={idle_value} notification_base={base_value} "
        f"flashes={args.flashes} animation_peak={PEAK_BRIGHTNESS} color=#A0FF00 "
        f"tail_decay={args.tail_decay:g} "
        f"expand_frames={len(expansion)} edge_fill_frames={len(edge_fill)} "
        f"wait={wait_description}",
        flush=True,
    )
    if args.dry_run:
        return 0.0

    delay = selected_wait_seconds(args, rng=rng)
    started = time.monotonic()
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        hold_scene(controller, idle, delay)
        if idle_value > base_value:
            play_frames(controller, lower_idle, args.idle_transition_ms / 1000.0)
        else:
            hold_scene(controller, base, 0.05)
        for index in range(args.flashes):
            preceding_wait = delay if index == 0 else args.flash_gap_ms / 1000.0
            print(
                f"NOTIFICATION_TRIGGERED index={index + 1} "
                f"waited_seconds={preceding_wait:.3f} "
                f"idle_percent={args.idle_brightness_percent:g} "
                f"peak={PEAK_BRIGHTNESS}",
                flush=True,
            )
            play_frames(controller, expansion, args.expand_ms / 1000.0)
            play_frames(controller, edge_fill, args.edge_fill_ms / 1000.0)
            hold_scene(controller, pulse, args.pulse_ms / 1000.0)
            play_frames(
                controller,
                notification_fade,
                args.notification_fade_ms / 1000.0,
            )
            if index + 1 < args.flashes:
                hold_scene(controller, base, args.flash_gap_ms / 1000.0)
        if idle_value > base_value:
            play_frames(controller, restore_idle, args.idle_transition_ms / 1000.0)
        if args.post_seconds:
            hold_scene(controller, idle, args.post_seconds)
    return time.monotonic() - started


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.yes:
        confirmation = input(
            "White idle plus maximum yellow-green notification ahead. Type IDLE to continue: "
        )
        if confirmation != "IDLE":
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
        print(f"WHITE_IDLE_NOTIFICATION_COMPLETE_SECONDS={elapsed:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
