# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Command-line frontend for the generalized Abralia RGB desktop API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..device_profile import load_device_profile
from .adapters.keychron_effect25 import KeychronEffect25Adapter
from .colors import BLACK, Color, Srgb8, parse_color
from .controller import DisplayLease, RgbController
from .errors import RgbError
from .profiles import load_profile
from .scene import (
    AbstractScene,
    Canvas,
    MappingStrategy,
    PhysicalSceneBuilder,
    RectangularSceneBuilder,
)


def _color(value: object) -> Color:
    if isinstance(value, Mapping):
        return parse_color(value)
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError("Color must be a string or tagged object.")
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        try:
            return Srgb8(*(int(text[offset : offset + 2], 16) for offset in (1, 3, 5)))
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"Invalid hexadecimal color {value!r}."
            ) from error
    parts = text.split(",")
    if len(parts) == 3:
        try:
            return Srgb8(*(int(part) for part in parts))
        except (ValueError, RgbError) as error:
            raise argparse.ArgumentTypeError(f"Invalid RGB color {value!r}.") from error
    raise argparse.ArgumentTypeError("Use #RRGGBB, R,G,B, or a tagged color object.")


def _assignment(value: str) -> tuple[str, Color]:
    try:
        element_id, color = value.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use ELEMENT=#RRGGBB.") from error
    if not element_id:
        raise argparse.ArgumentTypeError("Physical element ID cannot be empty.")
    return element_id, _color(color)


def _keycode(value: str) -> int:
    try:
        keycode = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Keycode must be a decimal or 0x-prefixed integer."
        ) from error
    if not 0 <= keycode <= 0xFFFF:
        raise argparse.ArgumentTypeError("Keycode must be in 0x0000...0xFFFF.")
    return keycode


def _layers(value: str) -> tuple[int, ...]:
    try:
        layers = tuple(int(part) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Layers must be comma-separated integers."
        ) from error
    if not layers:
        raise argparse.ArgumentTypeError("At least one layer is required.")
    return layers


def _add_device_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile", required=True, help="bundled profile ID or JSON path"
    )
    parser.add_argument(
        "--compatibility",
        help="optional user compatibility-layout JSON path",
    )
    parser.add_argument(
        "--device-index", type=int, help="index among devices matching the profile"
    )
    parser.add_argument("--brightness-ceiling", type=int, default=255, metavar="0..255")
    parser.add_argument(
        "--seconds", type=float, default=5.0, help="display duration before restore"
    )
    parser.add_argument(
        "--refresh-interval",
        type=float,
        default=1.0,
        help="seconds between explicit lease refreshes (default: 1.0)",
    )


def _hold(
    lease: DisplayLease,
    *,
    seconds: float,
    refresh_interval: float,
) -> None:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(refresh_interval, remaining))
        if time.monotonic() < deadline:
            lease.refresh()


def _validate_display_args(args: argparse.Namespace) -> None:
    if not 0 <= args.brightness_ceiling <= 255:
        raise ValueError("--brightness-ceiling must be in 0...255.")
    if args.seconds <= 0:
        raise ValueError("--seconds must be positive.")
    if args.refresh_interval <= 0:
        raise ValueError("--refresh-interval must be positive.")


def _validate_adapter_refresh(
    controller: RgbController, refresh_interval: float
) -> None:
    timeout = controller.adapter.capabilities().lease_timeout_seconds
    if timeout is not None and refresh_interval >= timeout:
        raise ValueError(
            f"--refresh-interval must be less than the adapter's {timeout:g}-second timeout."
        )


def _render(
    controller: RgbController,
    scenes: list[AbstractScene],
    args: argparse.Namespace,
) -> None:
    try:
        _validate_display_args(args)
    except Exception:
        controller.adapter.close()
        raise
    with controller:
        _validate_adapter_refresh(controller, args.refresh_interval)
        lease = controller.display(scenes, brightness_ceiling=args.brightness_ceiling)
        try:
            _hold(
                lease,
                seconds=args.seconds,
                refresh_interval=args.refresh_interval,
            )
        finally:
            lease.close()


def _devices(profile_source: str) -> int:
    devices = KeychronEffect25Adapter.discover(load_device_profile(profile_source))
    if not devices:
        print("No matching Keychron V3 8K Raw HID interfaces found.")
        return 0
    for index, device in enumerate(devices):
        product = device.product or "Keychron Raw HID device"
        print(
            f"[{index}] {product} {device.vendor_id:04X}:{device.product_id:04X} "
            f"usage {device.usage_page or 0:04X}:{device.usage or 0:04X}"
        )
    return 0


def _validate_profile(source: str) -> int:
    profile = load_profile(source)
    print(
        f"OK {profile.profile_id}: {len(profile.elements)} physical elements, "
        f"{len(profile.rgb_elements)} RGB elements, {len(profile.regions)} canvas targets"
    )
    return 0


def _render_keys(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    colors: dict[str, Color] = {}
    for requested_id, color in args.color:
        element_id = profile.resolve_element_id(requested_id)
        if element_id in colors:
            raise ValueError(
                f"Physical element {element_id!r} was assigned more than once."
            )
        colors[element_id] = color
    scene = PhysicalSceneBuilder().build(
        "cli-physical",
        colors,
        background=args.background,
        owner="abralia-rgb-cli",
    )
    _render(
        RgbController.open(
            args.profile,
            device_index=args.device_index,
            compatibility_source=args.compatibility,
        ),
        [scene],
        args,
    )
    print("RGB scene completed; the pre-command snapshot was restored.")
    return 0


def _load_canvas(path: str) -> Canvas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cells"), list):
        raise TypeError("Canvas JSON must contain width, height, and a cells array.")
    if type(data.get("width")) is not int or type(data.get("height")) is not int:
        raise ValueError("Canvas width and height must be integers.")
    cells = tuple(_color(value) for value in data["cells"])
    return Canvas(data["width"], data["height"], cells)


def _render_canvas(args: argparse.Namespace) -> int:
    scene = RectangularSceneBuilder().build(
        "cli-canvas",
        _load_canvas(args.canvas),
        target=args.target,
        strategy=MappingStrategy(args.strategy),
        owner="abralia-rgb-cli",
    )
    _render(
        RgbController.open(
            args.profile,
            device_index=args.device_index,
            compatibility_source=args.compatibility,
        ),
        [scene],
        args,
    )
    print("RGB canvas completed; the pre-command snapshot was restored.")
    return 0


def _render_keycode(args: argparse.Namespace) -> int:
    _validate_display_args(args)
    controller = RgbController.open(
        args.profile,
        device_index=args.device_index,
        compatibility_source=args.compatibility,
    )
    with controller:
        _validate_adapter_refresh(controller, args.refresh_interval)
        scene, resolution = controller.build_keycode_scene(
            "cli-keycode",
            keycode=args.keycode,
            layers=args.layers,
            color=args.color,
            owner="abralia-rgb-cli",
        )
        lease = controller.display([scene], brightness_ceiling=args.brightness_ceiling)
        try:
            _hold(
                lease,
                seconds=args.seconds,
                refresh_interval=args.refresh_interval,
            )
        finally:
            lease.close()
    matched = ", ".join(resolution.physical_element_ids)
    print(f"Matched physical controls: {matched}")
    print("RGB keycode scene completed; the pre-command snapshot was restored.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abralia-rgb",
        description="Generalized, profile-driven Abralia RGB host control.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    devices = subparsers.add_parser(
        "devices", help="list profile-matching Raw HID interfaces"
    )
    devices.add_argument("--profile", required=True)

    profile = subparsers.add_parser(
        "profile", help="validate or describe a layout profile"
    )
    profile_subparsers = profile.add_subparsers(dest="profile_command", required=True)
    validate = profile_subparsers.add_parser(
        "validate", help="validate schema and profile invariants"
    )
    validate.add_argument("source")

    keys = subparsers.add_parser(
        "render-keys", help="display a complete physical-key scene"
    )
    _add_device_options(keys)
    keys.add_argument(
        "--color",
        action="append",
        default=[],
        type=_assignment,
        metavar="ELEMENT=#RRGGBB",
        help="repeat for each non-background physical element",
    )
    keys.add_argument("--background", type=_color, default=BLACK)

    keycode = subparsers.add_parser(
        "render-keycode",
        help="resolve a live VIA keycode and light every physical match",
    )
    _add_device_options(keycode)
    keycode.add_argument("keycode", type=_keycode, help="raw 16-bit QMK/VIA keycode")
    keycode.add_argument(
        "--layers", required=True, type=_layers, help="comma-separated VIA layers"
    )
    keycode.add_argument("--color", required=True, type=_color)

    canvas = subparsers.add_parser(
        "render-canvas", help="map a JSON rectangular canvas"
    )
    _add_device_options(canvas)
    canvas.add_argument(
        "canvas", help="path to JSON containing width, height, and tagged cells"
    )
    canvas.add_argument("--target", default="full_keyboard")
    canvas.add_argument(
        "--strategy",
        choices=[strategy.value for strategy in MappingStrategy],
        default=MappingStrategy.GEOMETRY_RESAMPLE.value,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "devices":
            return _devices(args.profile)
        if args.command == "profile":
            return _validate_profile(args.source)
        if args.command == "render-keys":
            return _render_keys(args)
        if args.command == "render-keycode":
            return _render_keycode(args)
        return _render_canvas(args)
    except KeyboardInterrupt:
        print("Interrupted; restoration was requested.", file=sys.stderr)
        return 130
    except (
        argparse.ArgumentTypeError,
        RgbError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
