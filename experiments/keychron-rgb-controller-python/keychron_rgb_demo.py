#!/usr/bin/env python3
"""Capability-driven stock-firmware RGB demo for supported Keychron keyboards.

This experiment performs volatile HID operations only. It never flashes
firmware, remaps keys, or sends VIA/Keychron persistence commands.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Callable, Iterable, Sequence

import hid


KEYCHRON_VID = 0x3434
RAW_USAGE_PAGE = 0xFF60
RAW_USAGE = 0x61
REPORT_LENGTH = 32
KEYCHRON_RGB_FEATURE = 0x80
MAX_COLORS_PER_PACKET = 9
PER_KEY_RGB_INDEPENDENT_V_EFFECT = 25
PER_KEY_RGB_FRAME_VALUE_ID = 0x01


class FrameOperation(IntEnum):
    AWAIT = 0x00
    DIRECT = 0x01
    BEGIN = 0x02
    COMMIT = 0x03


class FrameState(IntEnum):
    AWAITING = 0x00
    DIRECT = 0x01
    GUARDED = 0x02


class FrameResult(IntEnum):
    OK = 0x00
    BUSY = 0x01
    INVALID_STATE = 0x02


class FrameFlags(IntFlag):
    ACTIVE_VALID = 1 << 0
    PENDING_VALID = 1 << 1
    BACK_BUFFER_FREE = 1 << 2
    TRANSITION_QUEUED = 1 << 3


class DemoError(RuntimeError):
    """Expected demo failure with a user-readable message."""


@dataclass(frozen=True)
class HSV:
    hue: int
    saturation: int
    value: int

    def bytes(self) -> list[int]:
        values = (self.hue, self.saturation, self.value)
        if any(value < 0 or value > 255 for value in values):
            raise DemoError(f"HSV components must be in 0...255, got {values}.")
        return list(values)


@dataclass(frozen=True)
class MatrixCoordinate:
    row: int
    column: int


@dataclass(frozen=True)
class DeviceDescriptor:
    name: str
    vendor_id: int
    product_id: int
    expected_led_count: int
    per_key_effect: int
    true_per_key_value: bool
    keys: dict[str, MatrixCoordinate]


V3_8K_ANSI = DeviceDescriptor(
    name="Keychron V3 8K ANSI encoder",
    vendor_id=0x3434,
    product_id=0x0F30,
    expected_led_count=87,
    per_key_effect=23,
    # The stock common solid renderer replaces stored per-key V with the
    # global brightness, so black background + colored foreground is not
    # available without a firmware change.
    true_per_key_value=False,
    keys={
        "W": MatrixCoordinate(2, 2),
        "A": MatrixCoordinate(3, 1),
        "S": MatrixCoordinate(3, 2),
        "D": MatrixCoordinate(3, 3),
        "SPACE": MatrixCoordinate(5, 6),
    },
)

DESCRIPTORS = {
    (V3_8K_ANSI.vendor_id, V3_8K_ANSI.product_id): V3_8K_ANSI,
}


@dataclass
class Candidate:
    scan_index: int
    vendor_id: int
    product_id: int
    product: str
    manufacturer: str
    serial: str
    path: bytes | str | None
    usage_page: int | None
    usage: int | None
    interface_number: int | None
    descriptor: DeviceDescriptor | None = None


@dataclass
class DetectionResult:
    candidate: Candidate
    support_level: int = 0
    support_summary: str = "Detected only"
    keychron_protocol: int | None = None
    command_set: int | None = None
    rgb_feature: bool = False
    rgb_protocol: tuple[int, int] | None = None
    led_count: int | None = None
    global_effect: int | None = None
    global_brightness: int | None = None
    per_key_type: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def demo_ready(self) -> bool:
        descriptor = self.candidate.descriptor
        return (
            self.support_level >= 2
            and descriptor is not None
            and self.led_count == descriptor.expected_led_count
        )


@dataclass(frozen=True)
class DeviceSnapshot:
    effect: int
    brightness: int
    per_key_type: int
    colors: tuple[HSV, ...]


@dataclass(frozen=True)
class FrameStatus:
    state: FrameState
    active_sequence: int
    pending_sequence: int
    flags: FrameFlags
    result: FrameResult

    @property
    def back_buffer_free(self) -> bool:
        return bool(self.flags & FrameFlags.BACK_BUFFER_FREE)

    @property
    def transition_queued(self) -> bool:
        return bool(self.flags & FrameFlags.TRANSITION_QUEUED)


def text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def enumerate_keychron_candidates() -> list[Candidate]:
    interfaces = list(hid.enumerate())
    keychron_interfaces = [
        interface
        for interface in interfaces
        if interface.get("vendor_id") == KEYCHRON_VID
        or "keychron" in text(interface.get("manufacturer_string")).lower()
        or "keychron" in text(interface.get("product_string")).lower()
    ]

    raw_interfaces = [
        interface
        for interface in keychron_interfaces
        if interface.get("usage_page") == RAW_USAGE_PAGE
        and interface.get("usage") == RAW_USAGE
    ]

    candidates: list[Candidate] = []
    for interface in raw_interfaces:
        vendor_id = int(interface.get("vendor_id", 0))
        product_id = int(interface.get("product_id", 0))
        candidates.append(
            Candidate(
                scan_index=len(candidates),
                vendor_id=vendor_id,
                product_id=product_id,
                product=text(interface.get("product_string")) or "Unknown Keychron",
                manufacturer=text(interface.get("manufacturer_string")) or "Keychron",
                serial=text(interface.get("serial_number")),
                path=interface.get("path"),
                usage_page=interface.get("usage_page"),
                usage=interface.get("usage"),
                interface_number=interface.get("interface_number"),
                descriptor=DESCRIPTORS.get((vendor_id, product_id)),
            )
        )

    # Report a Keychron product even if it has no standard Raw HID collection.
    represented = {(item.vendor_id, item.product_id) for item in candidates}
    for interface in keychron_interfaces:
        identity = (
            int(interface.get("vendor_id", 0)),
            int(interface.get("product_id", 0)),
        )
        if identity in represented:
            continue
        candidates.append(
            Candidate(
                scan_index=len(candidates),
                vendor_id=identity[0],
                product_id=identity[1],
                product=text(interface.get("product_string")) or "Unknown Keychron",
                manufacturer=text(interface.get("manufacturer_string")) or "Keychron",
                serial=text(interface.get("serial_number")),
                path=None,
                usage_page=interface.get("usage_page"),
                usage=interface.get("usage"),
                interface_number=interface.get("interface_number"),
                descriptor=DESCRIPTORS.get(identity),
            )
        )
        represented.add(identity)

    return candidates


class RawHIDConnection:
    def __init__(self, path: bytes | str | None):
        if path is None:
            raise DemoError("The device has no usable Raw HID path.")
        self._path = path
        self._device: hid.device | None = None

    def __enter__(self) -> RawHIDConnection:
        device = hid.device()
        try:
            device.open_path(self._path)
            device.set_nonblocking(False)
        except OSError as error:
            device.close()
            raise DemoError(
                "Could not open the Keychron Raw HID interface. Close Launcher/VIA "
                "and check the operating-system HID permission."
            ) from error
        self._device = device
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def _drain_stale_reports(self) -> None:
        assert self._device is not None
        self._device.set_nonblocking(True)
        try:
            while self._device.read(REPORT_LENGTH):
                pass
        finally:
            self._device.set_nonblocking(False)

    def transact(
        self,
        request: Sequence[int],
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        assert self._device is not None
        if not request or len(request) > REPORT_LENGTH:
            raise DemoError(f"HID request must contain 1...{REPORT_LENGTH} bytes.")

        payload = bytes(request) + bytes(REPORT_LENGTH - len(request))
        self._drain_stale_reports()
        written = self._device.write(bytes([0]) + payload)
        if written <= 0:
            raise DemoError("The operating system did not accept the HID output report.")

        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            report = bytes(self._device.read(REPORT_LENGTH, remaining_ms))
            if not report:
                continue
            if len(report) == REPORT_LENGTH + 1 and report[0] == 0:
                report = report[1:]
            if response_matches(report):
                return report

        raise DemoError("Timed out waiting for a matching Raw HID response.")


class KeychronProtocol:
    def __init__(self, connection: RawHIDConnection):
        self.connection = connection

    def get_protocol(self) -> tuple[int, int]:
        response = self.connection.transact(
            [0xA0], lambda report: len(report) >= 4 and report[0] == 0xA0
        )
        return response[1], response[3]

    def get_features(self) -> tuple[int, int]:
        response = self.connection.transact(
            [0xA2], lambda report: len(report) >= 4 and report[0] == 0xA2
        )
        return response[2], response[3]

    def rgb(self, command: int, payload: Sequence[int] = ()) -> bytes:
        response = self.connection.transact(
            [0xA8, command, *payload],
            lambda report: len(report) >= 3
            and report[0] == 0xA8
            and report[1] == command,
        )
        if response[2] != 0:
            raise DemoError(f"Keychron RGB command 0x{command:02X} was rejected.")
        return response

    def rgb_protocol(self) -> tuple[int, int]:
        response = self.rgb(0x01)
        return response[3], response[4]

    def led_count(self) -> int:
        return self.rgb(0x05)[3]

    def led_index(self, coordinate: MatrixCoordinate) -> int:
        if coordinate.column >= 24:
            raise DemoError("The Keychron mapping command supports at most 24 columns.")
        mask = 1 << coordinate.column
        response = self.rgb(
            0x06,
            [
                coordinate.row,
                mask & 0xFF,
                (mask >> 8) & 0xFF,
                (mask >> 16) & 0xFF,
            ],
        )
        index = response[3 + coordinate.column]
        if index == 0xFF:
            raise DemoError(
                f"No LED was reported at matrix {coordinate.row},{coordinate.column}."
            )
        return index

    def per_key_type(self) -> int:
        return self.rgb(0x07)[3]

    def set_per_key_type(self, effect_type: int) -> None:
        self.rgb(0x08, [effect_type])

    def get_colors(self, start: int, count: int) -> list[HSV]:
        if count < 1 or count > MAX_COLORS_PER_PACKET:
            raise DemoError(f"Per-key color read count must be 1...{MAX_COLORS_PER_PACKET}.")
        response = self.rgb(0x09, [start, count])
        return [
            HSV(*response[3 + offset * 3 : 6 + offset * 3])
            for offset in range(count)
        ]

    def set_colors(self, start: int, colors: Sequence[HSV]) -> None:
        if not colors or len(colors) > MAX_COLORS_PER_PACKET:
            raise DemoError(f"Per-key color write count must be 1...{MAX_COLORS_PER_PACKET}.")
        data: list[int] = [start, len(colors)]
        for color in colors:
            data.extend(color.bytes())
        self.rgb(0x0A, data)

    def via_get(self, value_id: int) -> int:
        response = self.connection.transact(
            [0x08, 0x03, value_id],
            lambda report: len(report) >= 4
            and report[0] == 0x08
            and report[1] == 0x03
            and report[2] == value_id,
        )
        return response[3]

    def via_set(self, value_id: int, value: int) -> None:
        self.connection.transact(
            [0x07, 0x03, value_id, value],
            lambda report: len(report) >= 4
            and report[0] == 0x07
            and report[1] == 0x03
            and report[2] == value_id,
        )

    def effect(self) -> int:
        return self.via_get(0x02)

    def set_effect(self, effect: int) -> None:
        self.via_set(0x02, effect)

    def brightness(self) -> int:
        return self.via_get(0x01)

    def set_brightness(self, brightness: int) -> None:
        self.via_set(0x01, brightness)

    @staticmethod
    def _frame_status_from_response(response: bytes) -> FrameStatus:
        if len(response) < 8:
            raise DemoError("Per-key frame-control response is incomplete.")
        try:
            return FrameStatus(
                state=FrameState(response[3]),
                active_sequence=response[4],
                pending_sequence=response[5],
                flags=FrameFlags(response[6]),
                result=FrameResult(response[7]),
            )
        except ValueError as error:
            raise DemoError("Per-key frame-control response contains an unknown value.") from error

    def frame_status(self) -> FrameStatus:
        response = self.connection.transact(
            [0x08, 0x00, PER_KEY_RGB_FRAME_VALUE_ID],
            lambda report: len(report) >= 8
            and report[0] == 0x08
            and report[1] == 0x00
            and report[2] == PER_KEY_RGB_FRAME_VALUE_ID,
        )
        status = self._frame_status_from_response(response)
        if status.result is not FrameResult.OK:
            raise DemoError(
                f"Per-key frame status failed with result {status.result.name}."
            )
        return status

    def frame_control(
        self,
        operation: FrameOperation,
        sequence: int = 0,
        *,
        require_ok: bool = True,
    ) -> FrameStatus:
        if not 0 <= sequence <= 255:
            raise DemoError("Frame sequence must be in 0...255.")
        response = self.connection.transact(
            [
                0x07,
                0x00,
                PER_KEY_RGB_FRAME_VALUE_ID,
                int(operation),
                sequence,
            ],
            lambda report: len(report) >= 8
            and report[0] == 0x07
            and report[1] == 0x00
            and report[2] == PER_KEY_RGB_FRAME_VALUE_ID,
        )
        status = self._frame_status_from_response(response)
        if require_ok and status.result is not FrameResult.OK:
            raise DemoError(
                f"Per-key frame operation {operation.name} failed with "
                f"result {status.result.name}."
            )
        return status


def detect_candidate(candidate: Candidate) -> DetectionResult:
    result = DetectionResult(candidate=candidate)
    if candidate.path is None:
        result.error = "No standard FF60:61 Raw HID interface"
        return result

    try:
        with RawHIDConnection(candidate.path) as connection:
            protocol = KeychronProtocol(connection)
            result.keychron_protocol, result.command_set = protocol.get_protocol()
            result.support_level = 0
            result.support_summary = "Keychron protocol detected"

            feature_low, _feature_high = protocol.get_features()
            result.rgb_feature = bool(feature_low & KEYCHRON_RGB_FEATURE)

            try:
                result.global_effect = protocol.effect()
                result.global_brightness = protocol.brightness()
                result.support_level = 1
                result.support_summary = "Global RGB control"
            except DemoError as error:
                result.warnings.append(f"Global VIA lighting query failed: {error}")

            if result.rgb_feature:
                result.rgb_protocol = protocol.rgb_protocol()
                result.led_count = protocol.led_count()
                result.per_key_type = protocol.per_key_type()
                # A non-mutating read proves that the per-key color buffer is exposed.
                protocol.get_colors(0, 1)
                result.support_level = 2
                result.support_summary = "Per-key color control"

            descriptor = candidate.descriptor
            if descriptor is None:
                result.warnings.append(
                    "Protocol is usable, but no physical-key descriptor is bundled for this model."
                )
            elif result.led_count != descriptor.expected_led_count:
                result.warnings.append(
                    f"Descriptor expects {descriptor.expected_led_count} LEDs, "
                    f"device reports {result.led_count}."
                )
            elif descriptor.true_per_key_value:
                result.support_level = 3
                result.support_summary = "Per-key color plus brightness/off"
            else:
                result.warnings.append(
                    "Stock renderer does not provide true per-key brightness/off; "
                    "white background is supported, black background with colored keys is not."
                )
    except (DemoError, OSError) as error:
        result.error = str(error)

    return result


def chunks(values: Sequence[HSV], size: int) -> Iterable[tuple[int, Sequence[HSV]]]:
    for start in range(0, len(values), size):
        yield start, values[start : start + size]


def snapshot(protocol: KeychronProtocol, led_count: int) -> DeviceSnapshot:
    colors: list[HSV] = []
    for start in range(0, led_count, MAX_COLORS_PER_PACKET):
        count = min(MAX_COLORS_PER_PACKET, led_count - start)
        colors.extend(protocol.get_colors(start, count))
    return DeviceSnapshot(
        effect=protocol.effect(),
        brightness=protocol.brightness(),
        per_key_type=protocol.per_key_type(),
        colors=tuple(colors),
    )


def write_frame(protocol: KeychronProtocol, colors: Sequence[HSV]) -> None:
    for start, color_chunk in chunks(colors, MAX_COLORS_PER_PACKET):
        protocol.set_colors(start, color_chunk)


def restore(protocol: KeychronProtocol, state: DeviceSnapshot) -> None:
    protocol.set_brightness(0)
    write_frame(protocol, state.colors)
    protocol.set_per_key_type(state.per_key_type)
    protocol.set_effect(state.effect)
    protocol.set_brightness(state.brightness)


def print_detection(results: Sequence[DetectionResult]) -> None:
    print("\nSTEP 3 — Detection result")
    for result in results:
        candidate = result.candidate
        print(
            f"[{candidate.scan_index}] {candidate.product} "
            f"({candidate.vendor_id:04X}:{candidate.product_id:04X})"
        )
        print(
            f"    Raw HID: "
            f"{candidate.usage_page or 0:04X}:{candidate.usage or 0:04X}, "
            f"interface {candidate.interface_number}"
        )
        print(f"    Support level: {result.support_level} — {result.support_summary}")
        if result.keychron_protocol is not None:
            print(
                f"    Keychron protocol: {result.keychron_protocol}; "
                f"command set: {result.command_set}"
            )
        print(f"    Keychron RGB feature: {'yes' if result.rgb_feature else 'no'}")
        if result.rgb_protocol is not None:
            print(
                f"    RGB protocol: {result.rgb_protocol[0]}.{result.rgb_protocol[1]}; "
                f"LEDs: {result.led_count}; per-key type: {result.per_key_type}"
            )
        if candidate.descriptor is not None:
            print(f"    Descriptor: {candidate.descriptor.name}")
        if result.error:
            print(f"    Error: {result.error}")
        for warning in result.warnings:
            print(f"    Note: {warning}")


def choose_result(
    results: Sequence[DetectionResult], requested_index: int | None
) -> DetectionResult:
    if requested_index is not None:
        for result in results:
            if result.candidate.scan_index == requested_index:
                return result
        raise DemoError(f"No scanned device has index {requested_index}.")

    ready = [result for result in results if result.demo_ready]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        raise DemoError("No detected Keychron is ready for the per-key demo.")
    raise DemoError("Multiple demo-ready keyboards were found; select one with --device INDEX.")


def run_scene(
    result: DetectionResult,
    requested_background: str,
    hold_seconds: float,
    cycles: int,
) -> None:
    candidate = result.candidate
    descriptor = candidate.descriptor
    assert descriptor is not None
    assert result.led_count is not None

    background = requested_background
    if background == "auto":
        background = "black" if result.support_level >= 3 else "white"
    if background == "black" and result.support_level < 3:
        print(
            "Requested black background is unavailable at detected Level 2; "
            "falling back to white so the colored keys remain visible."
        )
        background = "white"

    background_color = HSV(0, 0, 0 if background == "black" else 255)
    key_order = ("W", "A", "S", "D", "SPACE")
    palette = (
        ("red", HSV(0, 255, 255)),
        ("green", HSV(85, 255, 255)),
        ("blue", HSV(170, 255, 255)),
        ("magenta", HSV(213, 255, 255)),
        ("amber", HSV(32, 255, 255)),
    )

    print("\nSTEP 4 — Build and display a volatile RGB scene")
    print(f"    Background: {background}")
    print(f"    Color cycles: {cycles}; hold time per cycle: {hold_seconds:g} seconds")
    print("    Keys: W, A, S, D, and Space rotate through red/green/blue/magenta/amber")

    with RawHIDConnection(candidate.path) as connection:
        protocol = KeychronProtocol(connection)
        state = snapshot(protocol, result.led_count)
        restore_required = False
        try:
            protocol.set_brightness(0)
            restore_required = True

            led_by_key: dict[str, int] = {}
            print("    Key-to-LED mapping:")
            for key in key_order:
                index = protocol.led_index(descriptor.keys[key])
                if index >= result.led_count:
                    raise DemoError(f"{key} returned out-of-range LED index {index}.")
                led_by_key[key] = index
                coordinate = descriptor.keys[key]
                print(
                    f"      {key}: matrix {coordinate.row},{coordinate.column} -> LED {index}"
                )

            protocol.set_per_key_type(0)
            protocol.set_effect(descriptor.per_key_effect)

            for cycle in range(cycles):
                frame = [background_color] * result.led_count
                assignments: list[str] = []
                for offset, key in enumerate(key_order):
                    color_name, color = palette[(offset + cycle * 2) % len(palette)]
                    frame[led_by_key[key]] = color
                    assignments.append(f"{key}={color_name}")

                write_frame(protocol, frame)
                protocol.set_brightness(255)
                print(f"    Cycle {cycle + 1}/{cycles}: {', '.join(assignments)}")

                deadline = time.monotonic() + hold_seconds
                while time.monotonic() < deadline:
                    time.sleep(max(0, min(0.05, deadline - time.monotonic())))
        finally:
            if restore_required:
                restore(protocol, state)

        restored_state = snapshot(protocol, result.led_count)
        if restored_state != state:
            raise DemoError(
                "Post-restore readback does not match the original RGB snapshot."
            )

    print("    Scene commands were acknowledged; restoration readback matches the original state.")
    print("    Visual correctness still requires observing the physical keyboard.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan, classify, and demonstrate stock Keychron RGB control."
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="scan and detect support without changing lighting",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="scanned device index to use when multiple keyboards are present",
    )
    parser.add_argument(
        "--background",
        choices=("auto", "black", "white"),
        default="auto",
        help="scene background; auto selects the best supported option",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=2.0,
        help="seconds to display each color cycle (default: 2)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=3,
        help="number of foreground color changes (default: 3)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="run the lighting phase without an interactive confirmation",
    )
    args = parser.parse_args()
    if args.hold_seconds < 0:
        parser.error("--hold-seconds must be non-negative")
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    interrupted = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("STEP 1 — Scan connected HID interfaces for Keychron devices")
    candidates = enumerate_keychron_candidates()
    if not candidates:
        raise DemoError("No connected Keychron HID device was found.")
    print(f"    Found {len(candidates)} Keychron device candidate(s).")

    print("\nSTEP 2 — Negotiate stock-firmware support levels")
    results = [detect_candidate(candidate) for candidate in candidates]
    print("    Detection used read-only protocol, feature, and color-buffer queries.")
    print_detection(results)

    if args.probe_only:
        print("\nProbe-only run complete. No lighting state was changed.")
        return 0

    selected = choose_result(results, args.device)
    if not selected.demo_ready:
        raise DemoError(
            f"Device {selected.candidate.scan_index} is not ready for the per-key demo."
        )

    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        input("\nPress Enter to display the temporary scene, or Control-C to cancel: ")

    run_scene(selected, args.background, args.hold_seconds, args.cycles)
    if interrupted:
        return 130
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; restoration was requested.", file=sys.stderr)
        raise SystemExit(130)
    except DemoError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
