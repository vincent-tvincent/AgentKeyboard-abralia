# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Validate and resolve Abralia user compatibility layouts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .layout import CompatibilityLayoutError, load_compatibility_layout
from .rgb.profiles import DEFAULT_PROFILE, ProfileValidationError, load_profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abralia-layout",
        description="Validate or resolve an Abralia compatibility layout.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "resolve"):
        command = subparsers.add_parser(name)
        command.add_argument("source", help="compatibility-layout JSON path")
        command.add_argument(
            "--profile",
            default=DEFAULT_PROFILE,
            help="bundled hardware profile ID or JSON path",
        )
        if name == "resolve":
            command.add_argument(
                "--output",
                default="-",
                help="resolved JSON path, or - for standard output",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = load_profile(args.profile)
        layout = load_compatibility_layout(profile, args.source)
        if args.command == "validate":
            control_count = sum(
                len(region.controls) for region in layout.regions.values()
            )
            print(
                f"OK {profile.profile_id}: {len(layout.regions)} compatibility "
                f"region(s), {control_count} resolved control(s)"
            )
            return 0
        text = json.dumps(layout.to_dict(), indent=2) + "\n"
        if args.output == "-":
            print(text, end="")
        else:
            Path(args.output).write_text(text, encoding="utf-8")
        return 0
    except (
        CompatibilityLayoutError,
        ProfileValidationError,
        OSError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
