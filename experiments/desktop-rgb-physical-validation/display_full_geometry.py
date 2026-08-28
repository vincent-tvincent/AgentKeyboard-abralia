#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Display full-keyboard red, green, and blue physical-geometry bands."""

from __future__ import annotations

import argparse
import time

from abralia.rgb import (
    Canvas,
    MappingStrategy,
    RectangularSceneBuilder,
    RgbController,
    Srgb8,
)


WIDTH = 19
HEIGHT = 7
COLORS = (Srgb8(255, 0, 0), Srgb8(0, 255, 0), Srgb8(0, 0, 255))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--refresh-interval", type=float, default=0.5)
    parser.add_argument("--brightness", type=int, default=160)
    parser.add_argument("--device-index", type=int)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive.")
    if args.refresh_interval <= 0:
        raise ValueError("--refresh-interval must be positive.")
    if not 0 <= args.brightness <= 255:
        raise ValueError("--brightness must be in 0...255.")


def run(args: argparse.Namespace) -> float:
    validate_args(args)
    cells = tuple(
        COLORS[min(2, column // 6)]
        for _row in range(HEIGHT)
        for column in range(WIDTH)
    )
    scene = RectangularSceneBuilder().build(
        "full-geometry-bands",
        Canvas(WIDTH, HEIGHT, cells),
        target="full_keyboard",
        strategy=MappingStrategy.GEOMETRY_RESAMPLE,
        owner="physical-test",
    )
    started = time.monotonic()
    with RgbController.open(device_index=args.device_index) as controller:
        timeout = controller.adapter.capabilities().lease_timeout_seconds
        if timeout is not None and args.refresh_interval >= timeout:
            raise ValueError(
                f"--refresh-interval must be less than {timeout:g} seconds."
            )
        lease = controller.display([scene], brightness_ceiling=args.brightness)
        deadline = started + args.seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            time.sleep(min(args.refresh_interval, max(0.0, remaining)))
            if time.monotonic() < deadline:
                lease.refresh()
    return time.monotonic() - started


def main() -> int:
    try:
        elapsed = run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted; RGB snapshot restoration was requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"COMPLETED_SECONDS={elapsed:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
