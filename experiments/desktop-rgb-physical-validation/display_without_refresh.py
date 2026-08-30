#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Submit one cyan F-row frame and intentionally let its guarded lease expire."""

from __future__ import annotations

import argparse
import time

from abralia.rgb import PhysicalSceneBuilder, RgbController, Srgb8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--initial-delay",
        type=float,
        default=2.0,
        help="seconds to wait before submitting the frame",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=8.0,
        help="seconds to keep the controller open without refreshing",
    )
    parser.add_argument("--brightness", type=int, default=200)
    parser.add_argument("--device-index", type=int)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.initial_delay < 0:
        raise ValueError("--initial-delay cannot be negative.")
    if args.hold_seconds <= 0:
        raise ValueError("--hold-seconds must be positive.")
    if not 0 <= args.brightness <= 255:
        raise ValueError("--brightness must be in 0...255.")


def run(args: argparse.Namespace) -> None:
    validate_args(args)
    scene = PhysicalSceneBuilder().build(
        "unrefreshed-lease",
        {f"F{index}": Srgb8(0, 180, 255) for index in range(1, 13)},
        background=Srgb8(0, 0, 0),
        owner="physical-test",
    )
    time.sleep(args.initial_delay)
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        timeout = controller.adapter.capabilities().lease_timeout_seconds
        controller.display([scene], brightness_ceiling=args.brightness)
        print(
            f"FRAME_SUBMITTED_WITHOUT_REFRESH lease_timeout_seconds={timeout}",
            flush=True,
        )
        time.sleep(args.hold_seconds)
    print("SNAPSHOT_RESTORED", flush=True)


def main() -> int:
    try:
        run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted; RGB snapshot restoration was requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
