# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Synchronous Raw HID transport with unmatched-report preservation."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from .errors import TransportError

REPORT_LENGTH = 32


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


class RawHidTransport(Protocol):
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
            vendor_id=item["vendor_id"],
            product_id=item["product_id"],
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


class HidApiTransport:
    def __init__(self, device: object, *, unmatched_capacity: int = 64):
        self._device = device
        self._unmatched: deque[bytes] = deque(maxlen=unmatched_capacity)

    @classmethod
    def open_path(cls, path: bytes | str) -> HidApiTransport:
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
            raise TransportError("Could not open the Raw HID interface.") from error
        return cls(device)

    def _write(self, request: Sequence[int] | bytes) -> None:
        if not request or len(request) > REPORT_LENGTH:
            raise TransportError("Raw HID request must contain 1...32 bytes.")
        payload = bytes(request) + bytes(REPORT_LENGTH - len(request))
        try:
            written = self._device.write(bytes([0]) + payload)
        except OSError as error:
            raise TransportError("Raw HID write failed.") from error
        if written <= 0:
            raise TransportError("The operating system rejected the Raw HID report.")

    def _read(self, timeout_ms: int) -> bytes:
        try:
            report = bytes(self._device.read(REPORT_LENGTH, timeout_ms))
        except OSError as error:
            raise TransportError("Raw HID read failed.") from error
        if len(report) == REPORT_LENGTH + 1 and report[0] == 0:
            report = report[1:]
        return report

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        self._write(request)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            report = self._read(remaining)
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

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()
