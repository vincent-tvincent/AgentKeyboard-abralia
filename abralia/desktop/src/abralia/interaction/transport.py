# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Synchronous Raw HID transport for Host Interaction commands and events."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..device_profile import DeviceProfile
from .errors import AmbiguousDeviceError, DeviceNotFoundError, TransportError
from .protocol import REPORT_LENGTH


@dataclass(frozen=True, slots=True)
class HidDeviceInfo:
    path: bytes | str
    vendor_id: int
    product_id: int
    usage_page: int | None
    usage: int | None
    interface_number: int | None
    product: str
    manufacturer: str
    serial_number: str


class InteractionTransport(Protocol):
    def write(self, request: Sequence[int] | bytes) -> None: ...

    def read(self, timeout_ms: int) -> bytes: ...

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes: ...

    def pop_unmatched(self) -> bytes | None: ...

    def close(self) -> None: ...


def enumerate_hid_devices() -> list[HidDeviceInfo]:
    try:
        import hid
    except ImportError as error:
        raise TransportError("hidapi is not installed.") from error

    return [
        HidDeviceInfo(
            path=item["path"],
            vendor_id=int(item.get("vendor_id", 0)),
            product_id=int(item.get("product_id", 0)),
            usage_page=item.get("usage_page"),
            usage=item.get("usage"),
            interface_number=item.get("interface_number"),
            product=item.get("product_string") or "",
            manufacturer=item.get("manufacturer_string") or "",
            serial_number=item.get("serial_number") or "",
        )
        for item in hid.enumerate()
        if item.get("path") is not None
    ]


def find_profile_interface(
    profile: DeviceProfile,
    devices: Sequence[HidDeviceInfo] | None = None,
    *,
    device_index: int | None = None,
) -> HidDeviceInfo:
    candidates = [
        device
        for device in (devices if devices is not None else enumerate_hid_devices())
        if profile.device_match.matches(device)
    ]
    if not candidates:
        raise DeviceNotFoundError("No Raw HID interface matches the supplied profile.")
    if device_index is not None:
        if not 0 <= device_index < len(candidates):
            raise DeviceNotFoundError(
                "Device index is outside the matching interfaces."
            )
        return candidates[device_index]
    if len(candidates) > 1:
        raise AmbiguousDeviceError(
            f"Expected one profile-matching Raw HID interface, found {len(candidates)}."
        )
    return candidates[0]


class HidApiInteractionTransport:
    """One-owner HID connection; Launcher/VIA must not hold the same interface."""

    def __init__(self, device: object, *, unmatched_capacity: int = 64):
        self._device = device
        self._unmatched: deque[bytes] = deque(maxlen=unmatched_capacity)

    @classmethod
    def open_path(cls, path: bytes | str) -> HidApiInteractionTransport:
        try:
            import hid

            device = hid.device()
            device.open_path(path)
            device.set_nonblocking(False)
        except (ImportError, OSError) as error:
            try:
                device.close()
            except (NameError, AttributeError):
                pass
            raise TransportError(
                "Could not open Raw HID. Close Keychron Launcher/VIA and check HID permissions."
            ) from error
        return cls(device)

    @classmethod
    def open_profile(
        cls, profile: DeviceProfile, *, device_index: int | None = None
    ) -> HidApiInteractionTransport:
        device = find_profile_interface(profile, device_index=device_index)
        return cls.open_path(device.path)

    def write(self, request: Sequence[int] | bytes) -> None:
        if not request or len(request) > REPORT_LENGTH:
            raise TransportError("Raw HID request must contain 1...32 bytes.")
        payload = bytes(request) + bytes(REPORT_LENGTH - len(request))
        try:
            written = self._device.write(bytes([0]) + payload)
        except OSError as error:
            raise TransportError("Raw HID write failed.") from error
        if written <= 0:
            raise TransportError("The operating system rejected the Raw HID report.")

    def read(self, timeout_ms: int) -> bytes:
        if timeout_ms < 0:
            raise TransportError("Raw HID timeout cannot be negative.")
        try:
            report = bytes(self._device.read(REPORT_LENGTH, timeout_ms))
        except OSError as error:
            raise TransportError("Raw HID read failed.") from error
        if len(report) == REPORT_LENGTH + 1 and report[0] == 0:
            report = report[1:]
        if report and len(report) != REPORT_LENGTH:
            raise TransportError(
                f"Raw HID returned {len(report)} bytes; expected {REPORT_LENGTH}."
            )
        return report

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        if timeout_ms <= 0:
            raise TransportError("Transaction timeout must be positive.")
        self.write(request)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            report = self.read(remaining)
            if not report:
                continue
            if response_matches(report):
                return report
            self._unmatched.append(report)
        raise TransportError("Timed out waiting for a matching Raw HID response.")

    def pop_unmatched(self) -> bytes | None:
        return self._unmatched.popleft() if self._unmatched else None

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def __enter__(self) -> HidApiInteractionTransport:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()
