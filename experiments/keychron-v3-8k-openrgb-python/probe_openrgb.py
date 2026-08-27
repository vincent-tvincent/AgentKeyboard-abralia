#!/usr/bin/env python3
"""Read-only OpenRGB SDK probe for the Keychron V3 8K experiment."""

from __future__ import annotations

import sys

from openrgb import OpenRGBClient


EXPECTED_NAME = "Keychron V3 8K"


def main() -> int:
    try:
        client = OpenRGBClient(name="Abralia OpenRGB read-only probe")
    except (ConnectionError, OSError, TimeoutError) as error:
        print(f"Could not connect to the OpenRGB SDK server: {error}", file=sys.stderr)
        print("Start OpenRGB with --server, then run this probe again.", file=sys.stderr)
        return 1

    print(f"OpenRGB SDK returned {len(client.devices)} controllable device(s).")
    for index, device in enumerate(client.devices):
        print(
            f"  [{index}] {device.name!r}: "
            f"type={device.type.name}, leds={len(device.leds)}, zones={len(device.zones)}"
        )

    matches = [device for device in client.devices if EXPECTED_NAME.lower() in device.name.lower()]
    if not matches:
        print(
            f"{EXPECTED_NAME!r} was not registered as a controllable OpenRGB device. "
            "No lighting command was sent."
        )
        return 2

    keyboard = matches[0]
    print(
        f"Found {keyboard.name!r} with {len(keyboard.leds)} LEDs. "
        "Probe complete; no lighting command was sent."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
