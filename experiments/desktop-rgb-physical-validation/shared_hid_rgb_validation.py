#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Validate every RGB region through one shared HID owner in both I/O modes."""

from __future__ import annotations

import argparse
import time

from abralia.rgb import (
    BLACK,
    PhysicalOverlaySceneBuilder,
    PhysicalSceneBuilder,
    RgbController,
    Srgb8,
    load_profile,
)
from abralia.rgb.adapters.keychron_effect25 import FrameState, KeychronEffect25Adapter

from abralia import SharedHidMode, SharedRawHidSession

PALETTE = (
    Srgb8(255, 48, 32),
    Srgb8(32, 210, 96),
    Srgb8(40, 120, 255),
    Srgb8(255, 180, 24),
    Srgb8(210, 48, 255),
    Srgb8(24, 220, 220),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument(
        "--mode",
        choices=("cooperative", "threaded", "both"),
        default="both",
    )
    parser.add_argument("--seconds-per-region", type=float, default=1.5)
    parser.add_argument("--combined-seconds", type=float, default=2.5)
    parser.add_argument("--animation-seconds", type=float, default=4.0)
    parser.add_argument("--black-seconds", type=float, default=2.5)
    parser.add_argument("--timeout-observation-seconds", type=float, default=3.0)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--brightness", type=int, default=160)
    parser.add_argument("--device-index", type=int)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "seconds_per_region",
        "combined_seconds",
        "animation_seconds",
        "black_seconds",
        "timeout_observation_seconds",
        "fps",
    ):
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive.")
    if not 0 <= args.brightness <= 255:
        raise ValueError("--brightness must be in 0...255.")
    if args.fps > 30:
        raise ValueError("--fps must not exceed 30 for this physical validation.")


def static_region_scene(profile, region_id: str, color: Srgb8):
    region = profile.regions[region_id]
    colors = {
        element_id: color
        for element_id in region.elements
        if profile.element_by_id[element_id].rgb_capable
    }
    return PhysicalSceneBuilder().build(
        f"region-{region_id}",
        colors,
        background=BLACK,
        owner="shared-hid-rgb-validation",
    )


def combined_scenes(profile):
    scenes = [
        PhysicalSceneBuilder().build(
            "combined-base",
            {},
            background=BLACK,
            owner="shared-hid-rgb-validation",
            priority=0,
        )
    ]
    for index, (region_id, region) in enumerate(profile.regions.items()):
        color = (
            Srgb8(16, 16, 16)
            if region_id == "full_keyboard"
            else PALETTE[index % len(PALETTE)]
        )
        colors = {
            element_id: color
            for element_id in region.elements
            if profile.element_by_id[element_id].rgb_capable
        }
        scenes.append(
            PhysicalOverlaySceneBuilder().build(
                f"combined-{region_id}",
                colors,
                owner=f"shared-hid-{region_id}",
                priority=index + 1,
            )
        )
    return scenes


def hold_static(
    controller: RgbController,
    scenes: list,
    *,
    seconds: float,
    brightness: int,
) -> None:
    lease = controller.display(scenes, brightness_ceiling=brightness)
    deadline = time.monotonic() + seconds
    refresh_at = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= refresh_at:
            lease.refresh()
            refresh_at = now + 1.0
        time.sleep(min(0.02, max(0.0, deadline - now)))
    lease.close()


def animation_scene(profile, elapsed: float, duration: float):
    points = []
    for element in profile.rgb_elements:
        point = element.led_point
        x = (
            point.x
            if point is not None
            else element.geometry.x + element.geometry.width / 2
        )
        points.append((element.element_id, x))
    minimum = min(x for _element_id, x in points)
    maximum = max(x for _element_id, x in points)
    center = minimum + (maximum - minimum) * (elapsed / duration)
    colors = {}
    for element_id, x in points:
        distance = abs(x - center)
        value = max(0.0, 1.0 - distance / 3.0)
        pulse = value * value
        colors[element_id] = Srgb8(
            round(40 * pulse),
            round(150 * pulse),
            round(255 * pulse),
        )
    return PhysicalSceneBuilder().build(
        "shared-hid-moving-band",
        colors,
        background=BLACK,
        owner="shared-hid-rgb-validation",
    )


def run_mode(args: argparse.Namespace, mode: SharedHidMode) -> None:
    profile = load_profile(args.profile)
    print(f"PHASE mode={mode.value} action=open", flush=True)
    with SharedRawHidSession.open_profile(
        profile.device_profile,
        device_index=args.device_index,
        mode=mode,
    ) as session:
        adapter = KeychronEffect25Adapter(
            session.rgb_transport(), session.device_info, profile=profile.device_profile
        )
        with RgbController(adapter, profile) as controller:
            for index, region_id in enumerate(profile.regions):
                print(
                    f"PHASE mode={mode.value} action=region region={region_id}",
                    flush=True,
                )
                hold_static(
                    controller,
                    [
                        static_region_scene(
                            profile, region_id, PALETTE[index % len(PALETTE)]
                        )
                    ],
                    seconds=args.seconds_per_region,
                    brightness=args.brightness,
                )

            print(f"PHASE mode={mode.value} action=combined", flush=True)
            hold_static(
                controller,
                combined_scenes(profile),
                seconds=args.combined_seconds,
                brightness=args.brightness,
            )

            print(f"PHASE mode={mode.value} action=animation", flush=True)
            started = time.monotonic()
            next_frame = started
            frame_count = 0
            while time.monotonic() - started < args.animation_seconds:
                elapsed = time.monotonic() - started
                controller.display(
                    [animation_scene(profile, elapsed, args.animation_seconds)],
                    brightness_ceiling=args.brightness,
                )
                frame_count += 1
                next_frame += 1.0 / args.fps
                delay = next_frame - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            achieved = frame_count / max(0.001, time.monotonic() - started)
            print(
                f"ANIMATION mode={mode.value} frames={frame_count} fps={achieved:.2f}",
                flush=True,
            )

            black = PhysicalSceneBuilder().build(
                "shared-hid-black-standby",
                {},
                background=BLACK,
                owner="shared-hid-rgb-validation",
            )
            print(f"PHASE mode={mode.value} action=black_refresh", flush=True)
            hold_static(
                controller,
                [black],
                seconds=args.black_seconds,
                brightness=args.brightness,
            )
            print(
                f"PHASE mode={mode.value} action=stop_refresh_observe_awaiting",
                flush=True,
            )
            time.sleep(args.timeout_observation_seconds)
            frame_state = adapter._frame_status()[0]
            print(
                f"FRAME_STATE mode={mode.value} after_timeout={frame_state.name}",
                flush=True,
            )
            if frame_state is not FrameState.AWAITING:
                raise RuntimeError(
                    f"Expected AWAITING after timeout, found {frame_state.name}."
                )
    print(f"PHASE mode={mode.value} action=restored_closed", flush=True)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        modes = (
            tuple(SharedHidMode) if args.mode == "both" else (SharedHidMode(args.mode),)
        )
        for mode in modes:
            run_mode(args, mode)
    except KeyboardInterrupt:
        print("Interrupted; RGB restoration was requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print("SHARED_HID_RGB_VALIDATION_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
