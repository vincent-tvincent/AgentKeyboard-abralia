#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Flash the main typing region once at maximum brightness as a notification."""

from __future__ import annotations

import argparse
import random
import time
from typing import Protocol

from abralia.rgb import BLACK, PhysicalSceneBuilder, RgbController, Srgb8, load_profile


DEFAULT_REGION = "alphanumeric_block"
PEAK_BRIGHTNESS = 255
FLASH_COLOR = Srgb8(160, 255, 0)
MINIMUM_WAIT_SECONDS = 5.0
RANDOM_WAIT_MAX_SECONDS = 30.0
REFRESH_INTERVAL_SECONDS = 1.0


class RandomSource(Protocol):
    def uniform(self, low: float, high: float) -> float: ...


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"profile region to flash (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--flashes",
        type=int,
        default=1,
        help="number of brief flashes in the bounded notification (default: 1)",
    )
    parser.add_argument(
        "--on-ms",
        type=float,
        default=160.0,
        help="duration of each maximum-brightness flash (default: 160)",
    )
    parser.add_argument(
        "--off-ms",
        type=float,
        default=120.0,
        help="dark gap between flashes (default: 120)",
    )
    parser.add_argument(
        "--minimum-wait-seconds",
        type=float,
        default=MINIMUM_WAIT_SECONDS,
        help="guaranteed all-black wait before the random interval (default: 5)",
    )
    parser.add_argument(
        "--random-wait-max-seconds",
        type=float,
        default=RANDOM_WAIT_MAX_SECONDS,
        help="upper bound for the additional random wait (default: 30)",
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and describe the notification without opening a keyboard",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed FLASH confirmation",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.flashes <= 3:
        raise ValueError("--flashes must be in 1...3 for this bounded demo.")
    if not 40 <= args.on_ms <= 1000:
        raise ValueError("--on-ms must be in 40...1000.")
    if not 40 <= args.off_ms <= 1000:
        raise ValueError("--off-ms must be in 40...1000.")
    if not MINIMUM_WAIT_SECONDS <= args.minimum_wait_seconds <= 300:
        raise ValueError("--minimum-wait-seconds must be in 5...300.")
    if not 0 <= args.random_wait_max_seconds <= RANDOM_WAIT_MAX_SECONDS:
        raise ValueError("--random-wait-max-seconds must be in 0...30.")


def notification_delay(
    minimum_seconds: float,
    random_max_seconds: float,
    *,
    rng: RandomSource | None = None,
) -> float:
    source = rng or random.SystemRandom()
    return minimum_seconds + source.uniform(0.0, random_max_seconds)


def notification_scenes(profile, region_id: str):
    try:
        region = profile.regions[region_id]
    except KeyError as error:
        available = ", ".join(profile.regions)
        raise ValueError(
            f"Unknown RGB region {region_id!r}; available regions: {available}."
        ) from error
    renderable = tuple(
        element_id
        for element_id in region.elements
        if profile.element_by_id[element_id].rgb_capable
    )
    if not renderable:
        raise ValueError(f"RGB region {region_id!r} has no renderable elements.")
    active = PhysicalSceneBuilder().build(
        "main-area-notification-on",
        {element_id: FLASH_COLOR for element_id in renderable},
        background=BLACK,
        owner="main-area-notification-demo",
    )
    dark = PhysicalSceneBuilder().build(
        "main-area-notification-off",
        {},
        background=BLACK,
        owner="main-area-notification-demo",
    )
    return renderable, active, dark


def hold_scene(
    controller: RgbController,
    scene,
    duration_seconds: float,
    *,
    clock=time.monotonic,
    sleep=time.sleep,
) -> None:
    lease = controller.display([scene], brightness_ceiling=PEAK_BRIGHTNESS)
    try:
        deadline = clock() + duration_seconds
        refresh_at = clock() + REFRESH_INTERVAL_SECONDS
        while clock() < deadline:
            now = clock()
            if now >= refresh_at:
                lease.refresh()
                refresh_at = now + REFRESH_INTERVAL_SECONDS
            sleep(min(0.02, max(0.0, deadline - now)))
    finally:
        lease.close()


def run(
    args: argparse.Namespace,
    *,
    rng: RandomSource | None = None,
) -> float:
    validate_args(args)
    profile = load_profile(args.profile)
    renderable, active, dark = notification_scenes(profile, args.region)
    print(
        f"NOTIFICATION region={args.region} keys={len(renderable)} "
        f"flashes={args.flashes} on_ms={args.on_ms:g} peak={PEAK_BRIGHTNESS} "
        f"black_wait={args.minimum_wait_seconds:g}+random(0..{args.random_wait_max_seconds:g})s",
        flush=True,
    )
    if args.dry_run:
        return 0.0

    started = time.monotonic()
    delay = notification_delay(
        args.minimum_wait_seconds,
        args.random_wait_max_seconds,
        rng=rng,
    )
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        hold_scene(controller, dark, delay)
        for index in range(args.flashes):
            preceding_wait = delay if index == 0 else args.off_ms / 1000.0
            print(
                f"FLASH_TRIGGERED index={index + 1} waited_seconds={preceding_wait:.3f} "
                f"color=yellow-green peak={PEAK_BRIGHTNESS}",
                flush=True,
            )
            hold_scene(controller, active, args.on_ms / 1000.0)
            if index + 1 < args.flashes:
                hold_scene(controller, dark, args.off_ms / 1000.0)
        hold_scene(controller, dark, args.off_ms / 1000.0)
    return time.monotonic() - started


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.yes:
        confirmation = input(
            "Maximum-brightness yellow-green notification ahead. Type FLASH to continue: "
        )
        if confirmation != "FLASH":
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
        print(f"NOTIFICATION_COMPLETE_SECONDS={elapsed:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
