#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Render a drifting volumetric fog orb through the production RGB API."""

from __future__ import annotations

import argparse
import math
import time

from abralia.rgb import PhysicalSceneBuilder, RgbController, Srgb8


def clamp(value: float, lower: float = 0.0, upper: float = 255.0) -> int:
    return round(max(lower, min(upper, value)))


def gaussian(distance_squared: float, width: float) -> float:
    return math.exp(-distance_squared / width)


def color_at(x: float, y: float, elapsed: float, index: int) -> Srgb8:
    center_x = 9.1 + 5.8 * math.sin(elapsed * 0.31)
    center_y = 3.05 + 1.65 * math.sin(elapsed * 0.47 + 0.8)
    dx = (x - center_x) / 3.25
    dy = (y - center_y) / 2.05
    radius = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)

    noise = (
        0.52
        + 0.20 * math.sin(x * 1.13 + y * 1.71 + elapsed * 0.83)
        + 0.14 * math.sin(x * 2.87 - y * 2.11 - elapsed * 1.29)
        + 0.09 * math.sin(x * 5.31 + y * 3.79 + elapsed * 1.91)
    )
    warped_radius = radius + 0.15 * noise
    warped_radius += 0.055 * math.sin(4 * angle - elapsed)
    volume = gaussian(warped_radius * warped_radius, 0.72)
    volume *= 0.68 + 0.50 * noise
    core = gaussian(radius * radius, 0.115)
    shell = math.exp(-((warped_radius - 0.78) ** 2) / 0.032)
    rim_bias = max(0.0, 0.5 + 0.5 * math.cos(angle - elapsed * 0.22))

    direction_x = 1.0 if math.cos(elapsed * 0.31) >= 0 else -1.0
    trail = 0.0
    for step in range(1, 5):
        trail_x = center_x - direction_x * (1.2 + step * 1.05)
        trail_y = center_y + 0.30 * math.sin(elapsed * 0.9 + step)
        trail_distance = ((x - trail_x) / (1.1 + step * 0.28)) ** 2
        trail_distance += ((y - trail_y) / (0.7 + step * 0.14)) ** 2
        trail += gaussian(trail_distance, 1.0) * (0.20 / step)

    spark_x = center_x + 2.15 * math.cos(elapsed * 1.07)
    spark_y = center_y + 1.20 * math.sin(elapsed * 1.07)
    spark_distance = ((x - spark_x) / 0.62) ** 2
    spark_distance += ((y - spark_y) / 0.48) ** 2
    spark = gaussian(spark_distance, 0.78)

    seed = math.sin(index * 91.7) * 43758.5453
    star_gate = 1.0 if seed - math.floor(seed) > 0.90 else 0.0
    star = star_gate * (
        0.5 + 0.5 * math.sin(elapsed * 1.7 + index * 0.73)
    ) ** 4

    red = 9 * trail + 27 * volume + 112 * shell * rim_bias
    red += 235 * spark + 34 * star
    green = 28 * trail + 112 * volume + 82 * shell
    green += 148 * core + 108 * spark + 40 * star
    blue = 52 * trail + 205 * volume
    blue += 238 * shell * (0.45 + 0.55 * rim_bias)
    blue += 255 * core + 30 * spark + 58 * star

    shadow = 0.58 + 0.42 * max(0.0, math.cos(angle + 0.75))
    if radius < 1.2:
        red *= shadow
        green *= shadow
        blue *= 0.78 + 0.22 * shadow
    return Srgb8(clamp(red), clamp(green), clamp(blue))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--brightness", type=int, default=190)
    parser.add_argument("--device-index", type=int)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive.")
    if not 1 <= args.fps <= 30:
        raise ValueError("--fps must be in 1...30.")
    if not 0 <= args.brightness <= 255:
        raise ValueError("--brightness must be in 0...255.")


def run(args: argparse.Namespace) -> tuple[int, float]:
    validate_args(args)
    frame_count = 0
    with RgbController.open(device_index=args.device_index) as controller:
        points = []
        for index, element in enumerate(controller.profile.rgb_elements):
            point = element.led_point
            x = (
                point.x
                if point is not None
                else element.geometry.x + element.geometry.width / 2
            )
            y = (
                point.y
                if point is not None
                else element.geometry.y + element.geometry.height / 2
            )
            points.append((index, element.element_id, x, y))

        started = time.monotonic()
        next_frame = started
        while time.monotonic() - started < args.seconds:
            elapsed = time.monotonic() - started
            colors = {
                element_id: color_at(x, y, elapsed, index)
                for index, element_id, x, y in points
            }
            scene = PhysicalSceneBuilder().build(
                "fog-orb",
                colors,
                background=Srgb8(0, 0, 0),
                owner="physical-animation-test",
            )
            controller.display([scene], brightness_ceiling=args.brightness)
            frame_count += 1
            next_frame += 1.0 / args.fps
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    return frame_count, time.monotonic() - started


def main() -> int:
    args = parse_args()
    try:
        frame_count, elapsed = run(args)
    except KeyboardInterrupt:
        print("Interrupted; RGB snapshot restoration was requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"ANIMATION_FPS={frame_count / elapsed:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
