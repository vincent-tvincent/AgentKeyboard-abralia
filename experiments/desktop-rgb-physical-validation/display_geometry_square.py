#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Map a filled 6U x 6U canvas square onto the full keyboard geometry."""

from __future__ import annotations

import argparse
import time

from abralia.rgb import (
    BLACK,
    Canvas,
    MappingStrategy,
    RectangularSceneBuilder,
    RgbController,
    Srgb8,
)

CANVAS_WIDTH_U = 19
CANVAS_HEIGHT_U = 7
SQUARE_SIZE_U = 6
DEFAULT_SQUARE_X_U = 6
DEFAULT_SQUARE_Y_U = 0


def parse_hex_color(value: str) -> Srgb8:
    text = value.removeprefix("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError("color must use #RRGGBB")
    try:
        red, green, blue = (int(text[offset : offset + 2], 16) for offset in (0, 2, 4))
    except ValueError as error:
        raise argparse.ArgumentTypeError("color must use #RRGGBB") from error
    return Srgb8(red, green, blue)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--x", type=int, default=DEFAULT_SQUARE_X_U, metavar="U")
    parser.add_argument("--y", type=int, default=DEFAULT_SQUARE_Y_U, metavar="U")
    parser.add_argument("--color", type=parse_hex_color, default=Srgb8(255, 255, 255))
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--refresh-interval", type=float, default=1.0)
    parser.add_argument("--brightness", type=int, default=160)
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--verbose-map",
        action="store_true",
        help="print the mapped sRGB value of every non-black physical key",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 0 <= args.x <= CANVAS_WIDTH_U - SQUARE_SIZE_U:
        raise ValueError(f"--x must be in 0...{CANVAS_WIDTH_U - SQUARE_SIZE_U}.")
    if not 0 <= args.y <= CANVAS_HEIGHT_U - SQUARE_SIZE_U:
        raise ValueError(f"--y must be in 0...{CANVAS_HEIGHT_U - SQUARE_SIZE_U}.")
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive.")
    if args.refresh_interval <= 0:
        raise ValueError("--refresh-interval must be positive.")
    if not 0 <= args.brightness <= 255:
        raise ValueError("--brightness must be in 0...255.")


def build_canvas(square_x: int, square_y: int, color: Srgb8) -> Canvas:
    cells = tuple(
        color
        if (
            square_x <= column < square_x + SQUARE_SIZE_U
            and square_y <= row < square_y + SQUARE_SIZE_U
        )
        else BLACK
        for row in range(CANVAS_HEIGHT_U)
        for column in range(CANVAS_WIDTH_U)
    )
    return Canvas(CANVAS_WIDTH_U, CANVAS_HEIGHT_U, cells)


def build_scene(canvas: Canvas):
    return RectangularSceneBuilder().build(
        "geometry-square-6u",
        canvas,
        target="full_keyboard",
        strategy=MappingStrategy.GEOMETRY_RESAMPLE,
        owner="physical-geometry-square-test",
    )


def print_canvas(square_x: int, square_y: int) -> None:
    print("CANVAS_19U_X_7U")
    for row in range(CANVAS_HEIGHT_U):
        print(
            "".join(
                "#"
                if (
                    square_x <= column < square_x + SQUARE_SIZE_U
                    and square_y <= row < square_y + SQUARE_SIZE_U
                )
                else "."
                for column in range(CANVAS_WIDTH_U)
            )
        )


def print_mapping(physical, report, *, verbose: bool) -> None:
    lit = [
        (element_id, color)
        for element_id, color in physical.colors.items()
        if color != BLACK
    ]
    print(f"LIT_KEY_COUNT={len(lit)}")
    print("LIT_KEYS=" + ",".join(element_id for element_id, _color in lit))
    print(f"MERGED_KEY_COUNT={len(report.merged_elements)}")
    print(
        "LARGE_KEY_SELECTIONS="
        + ",".join(
            f"{element_id}@{row},{column}"
            for element_id, row, column in report.large_key_selections
        )
    )
    print(
        "UNCOVERED_CELLS="
        + ",".join(f"{row},{column}" for row, column in report.uncovered_cells)
    )
    if verbose:
        print("MERGED_KEYS=" + ",".join(report.merged_elements))
        for element_id, color in lit:
            print(
                f"KEY_COLOR {element_id} "
                f"#{color.red:02X}{color.green:02X}{color.blue:02X}"
            )


def run(args: argparse.Namespace) -> float:
    validate_args(args)
    canvas = build_canvas(args.x, args.y, args.color)
    scene = build_scene(canvas)
    print(
        f"SQUARE={SQUARE_SIZE_U}U_X_{SQUARE_SIZE_U}U "
        f"ORIGIN_U={args.x},{args.y} "
        f"COLOR=#{args.color.red:02X}{args.color.green:02X}{args.color.blue:02X}"
    )
    print("TARGET=full_keyboard STRATEGY=geometry_resample")
    print_canvas(args.x, args.y)

    started = time.monotonic()
    with RgbController.open(args.profile, device_index=args.device_index) as controller:
        timeout = controller.adapter.capabilities().lease_timeout_seconds
        if timeout is not None and args.refresh_interval >= timeout:
            raise ValueError(
                f"--refresh-interval must be less than {timeout:g} seconds."
            )
        physical, _device, reports = controller.compile([scene])
        print_mapping(physical, reports[0], verbose=args.verbose_map)
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
