#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Run a yellow-green sweep-and-pulse notification across the typing region."""

from __future__ import annotations

import argparse
import math
import time

from abralia.rgb import BLACK, PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from main_area_notification_flash import (
    DEFAULT_REGION,
    FLASH_COLOR,
    MINIMUM_WAIT_SECONDS,
    PEAK_BRIGHTNESS,
    RANDOM_WAIT_MAX_SECONDS,
    RandomSource,
    hold_scene,
    notification_delay,
    notification_scenes,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--sweep-ms", type=float, default=250.0)
    parser.add_argument("--pulse-ms", type=float, default=120.0)
    parser.add_argument("--fade-ms", type=float, default=150.0)
    parser.add_argument("--band-width-u", type=float, default=5.0)
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
    parser.add_argument("--yes", action="store_true", help="skip the SWEEP prompt")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 100 <= args.sweep_ms <= 1000:
        raise ValueError("--sweep-ms must be in 100...1000.")
    if not 40 <= args.pulse_ms <= 500:
        raise ValueError("--pulse-ms must be in 40...500.")
    if not 50 <= args.fade_ms <= 1000:
        raise ValueError("--fade-ms must be in 50...1000.")
    if not 2 <= args.band_width_u <= 10:
        raise ValueError("--band-width-u must be in 2...10.")
    if not 10 <= args.fps <= 30:
        raise ValueError("--fps must be in 10...30.")
    if not MINIMUM_WAIT_SECONDS <= args.minimum_wait_seconds <= 300:
        raise ValueError("--minimum-wait-seconds must be in 5...300.")
    if not 0 <= args.random_wait_max_seconds <= RANDOM_WAIT_MAX_SECONDS:
        raise ValueError("--random-wait-max-seconds must be in 0...30.")


def scaled_color(level: float) -> Srgb8:
    bounded = min(1.0, max(0.0, level))
    return Srgb8(
        round(FLASH_COLOR.red * bounded),
        round(FLASH_COLOR.green * bounded),
        round(FLASH_COLOR.blue * bounded),
    )


def region_positions(profile, region_id: str) -> tuple[tuple[str, float], ...]:
    try:
        region = profile.regions[region_id]
    except KeyError as error:
        available = ", ".join(profile.regions)
        raise ValueError(
            f"Unknown RGB region {region_id!r}; available regions: {available}."
        ) from error
    positions = []
    for element_id in region.elements:
        element = profile.element_by_id[element_id]
        if not element.rgb_capable:
            continue
        x = (
            element.led_point.x
            if element.led_point is not None
            else element.geometry.x + element.geometry.width / 2
        )
        positions.append((element_id, x))
    if not positions:
        raise ValueError(f"RGB region {region_id!r} has no renderable elements.")
    return tuple(positions)


def sweep_scenes(
    profile,
    region_id: str,
    *,
    duration_ms: float,
    fps: float,
    band_width_u: float,
):
    positions = region_positions(profile, region_id)
    minimum = min(x for _element_id, x in positions)
    maximum = max(x for _element_id, x in positions)
    half_width = band_width_u / 2
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    scenes = []
    for step in range(steps):
        progress = step / (steps - 1)
        center = minimum + progress * (maximum - minimum)
        colors = {}
        for element_id, x in positions:
            proximity = max(0.0, 1.0 - abs(x - center) / half_width)
            level = proximity * proximity * (3.0 - 2.0 * proximity)
            colors[element_id] = scaled_color(level)
        scenes.append(
            PhysicalSceneBuilder().build(
                f"main-area-sweep-{step}",
                colors,
                background=BLACK,
                owner="main-area-sweep-notification-demo",
            )
        )
    return tuple(scenes)


def fade_scenes(profile, region_id: str, *, duration_ms: float, fps: float):
    positions = region_positions(profile, region_id)
    steps = max(2, math.ceil(duration_ms / 1000.0 * fps))
    scenes = []
    for step in range(1, steps + 1):
        color = scaled_color(1.0 - step / steps)
        scenes.append(
            PhysicalSceneBuilder().build(
                f"main-area-fade-{step}",
                {element_id: color for element_id, _x in positions},
                background=BLACK,
                owner="main-area-sweep-notification-demo",
            )
        )
    return tuple(scenes)


def play_frames(
    controller: RgbController,
    scenes,
    duration_seconds: float,
    *,
    clock=time.monotonic,
    sleep=time.sleep,
) -> None:
    interval = duration_seconds / len(scenes)
    next_frame_at = clock()
    for scene in scenes:
        lease = controller.display([scene], brightness_ceiling=PEAK_BRIGHTNESS)
        lease.close()
        next_frame_at += interval
        sleep(max(0.0, next_frame_at - clock()))


def run(args: argparse.Namespace, *, rng: RandomSource | None = None) -> float:
    validate_args(args)
    profile = load_profile(args.profile)
    _renderable, pulse, dark = notification_scenes(profile, args.region)
    sweep = sweep_scenes(
        profile,
        args.region,
        duration_ms=args.sweep_ms,
        fps=args.fps,
        band_width_u=args.band_width_u,
    )
    fade = fade_scenes(
        profile,
        args.region,
        duration_ms=args.fade_ms,
        fps=args.fps,
    )
    print(
        f"SWEEP_NOTIFICATION region={args.region} sweep_frames={len(sweep)} "
        f"fade_frames={len(fade)} peak={PEAK_BRIGHTNESS} color=#A0FF00 "
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
        print(
            f"SWEEP_TRIGGERED waited_seconds={delay:.3f} peak={PEAK_BRIGHTNESS}",
            flush=True,
        )
        play_frames(controller, sweep, args.sweep_ms / 1000.0)
        hold_scene(controller, pulse, args.pulse_ms / 1000.0)
        play_frames(controller, fade, args.fade_ms / 1000.0)
        hold_scene(controller, dark, 0.12)
    return time.monotonic() - started


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.yes:
        confirmation = input(
            "Maximum-brightness yellow-green sweep ahead. Type SWEEP to continue: "
        )
        if confirmation != "SWEEP":
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
        print(f"SWEEP_NOTIFICATION_COMPLETE_SECONDS={elapsed:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
