# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""One Raw HID owner with cooperative and threaded borrowed transport views."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Self

from .interaction.errors import TransportError as InteractionTransportError
from .rgb.errors import TransportError as RgbTransportError
from .rgb.transport import HidDeviceInfo

REPORT_LENGTH = 32


class SharedHidMode(str, Enum):
    COOPERATIVE = "cooperative"
    THREADED = "threaded"


class SharedHidError(RuntimeError):
    """The shared physical HID session failed."""


class SharedRawHidSession:
    """Own one physical Raw HID handle and lend non-owning protocol views."""

    def __init__(
        self,
        device: object,
        device_info: HidDeviceInfo,
        *,
        mode: SharedHidMode | str = SharedHidMode.COOPERATIVE,
        unmatched_capacity: int = 64,
        reader_poll_ms: int = 20,
    ):
        if unmatched_capacity <= 0:
            raise ValueError("unmatched_capacity must be positive.")
        if reader_poll_ms <= 0:
            raise ValueError("reader_poll_ms must be positive.")
        self.device_info = device_info
        self.mode = SharedHidMode(mode)
        self._device = device
        self._unmatched_capacity = unmatched_capacity
        self._reader_poll_ms = reader_poll_ms
        self._unmatched: deque[bytes] = deque()
        self._condition = threading.Condition(threading.RLock())
        self._transaction_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._fatal_error: SharedHidError | None = None
        self._pending_matcher: Callable[[bytes], bool] | None = None
        self._pending_response: bytes | None = None
        self._stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._rgb_view = SharedRgbTransportView(self)
        self._interaction_view = SharedInteractionTransportView(self)
        if self.mode is SharedHidMode.THREADED:
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="abralia-shared-hid-reader",
                daemon=True,
            )
            self._reader_thread.start()

    @classmethod
    def open_path(
        cls,
        path: bytes | str,
        device_info: HidDeviceInfo,
        *,
        mode: SharedHidMode | str = SharedHidMode.COOPERATIVE,
        unmatched_capacity: int = 64,
        reader_poll_ms: int = 20,
    ) -> SharedRawHidSession:
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
            raise SharedHidError(
                "Could not open the Raw HID interface. Close Keychron Launcher/VIA "
                "and check HID permissions."
            ) from error
        return cls(
            device,
            device_info,
            mode=mode,
            unmatched_capacity=unmatched_capacity,
            reader_poll_ms=reader_poll_ms,
        )

    @classmethod
    def open_keychron_v3_8k(
        cls,
        *,
        device_index: int | None = None,
        mode: SharedHidMode | str = SharedHidMode.COOPERATIVE,
        unmatched_capacity: int = 64,
        reader_poll_ms: int = 20,
    ) -> SharedRawHidSession:
        from .rgb.adapters.keychron_effect25 import KeychronEffect25Adapter

        devices = KeychronEffect25Adapter.discover()
        if not devices:
            raise SharedHidError("No matching Keychron V3 8K Raw HID interface found.")
        if device_index is None:
            if len(devices) != 1:
                raise SharedHidError(
                    f"Found {len(devices)} matching interfaces; select device_index."
                )
            selected = devices[0]
        else:
            if not 0 <= device_index < len(devices):
                raise SharedHidError(f"device_index must be in 0...{len(devices) - 1}.")
            selected = devices[device_index]
        return cls.open_path(
            selected.path,
            selected,
            mode=mode,
            unmatched_capacity=unmatched_capacity,
            reader_poll_ms=reader_poll_ms,
        )

    def rgb_transport(self) -> SharedRgbTransportView:
        return self._rgb_view

    def interaction_transport(self) -> SharedInteractionTransportView:
        return self._interaction_view

    def _check_available_locked(self) -> None:
        if self._fatal_error is not None:
            raise self._fatal_error
        if self._closed:
            raise SharedHidError("The shared Raw HID session is closed.")

    @staticmethod
    def _payload(request: Sequence[int] | bytes) -> bytes:
        if not request or len(request) > REPORT_LENGTH:
            raise SharedHidError("Raw HID request must contain 1...32 bytes.")
        return bytes(request) + bytes(REPORT_LENGTH - len(request))

    def _write_device(self, request: Sequence[int] | bytes) -> None:
        payload = self._payload(request)
        with self._write_lock:
            with self._condition:
                self._check_available_locked()
            try:
                written = self._device.write(bytes([0]) + payload)
            except OSError as error:
                raise SharedHidError("Raw HID write failed.") from error
        if written <= 0:
            raise SharedHidError("The operating system rejected the Raw HID report.")

    def _read_device(self, timeout_ms: int) -> bytes:
        if timeout_ms < 0:
            raise SharedHidError("Raw HID timeout cannot be negative.")
        with self._condition:
            self._check_available_locked()
        try:
            report = bytes(self._device.read(REPORT_LENGTH, timeout_ms))
        except OSError as error:
            raise SharedHidError("Raw HID read failed.") from error
        if len(report) == REPORT_LENGTH + 1 and report[0] == 0:
            report = report[1:]
        if report and len(report) != REPORT_LENGTH:
            raise SharedHidError(
                f"Raw HID returned {len(report)} bytes; expected {REPORT_LENGTH}."
            )
        return report

    def _enqueue_locked(self, report: bytes) -> None:
        if len(self._unmatched) >= self._unmatched_capacity:
            error = SharedHidError("Shared Raw HID unmatched-report queue overflowed.")
            self._fatal_error = error
            self._condition.notify_all()
            raise error
        self._unmatched.append(report)
        self._condition.notify_all()

    def _set_fatal(self, error: SharedHidError) -> None:
        with self._condition:
            if self._fatal_error is None:
                self._fatal_error = error
            self._condition.notify_all()
        self._stop.set()

    def _reader_loop(self) -> None:
        try:
            while not self._stop.is_set():
                report = self._read_device(self._reader_poll_ms)
                if not report:
                    continue
                with self._condition:
                    self._check_available_locked()
                    matcher = self._pending_matcher
                    if matcher is not None and matcher(report):
                        self._pending_response = report
                        self._pending_matcher = None
                        self._condition.notify_all()
                    else:
                        self._enqueue_locked(report)
        except SharedHidError as error:
            if not self._stop.is_set():
                self._set_fatal(error)
        except Exception as error:  # noqa: BLE001 - surface reader-thread failures.
            if not self._stop.is_set():
                self._set_fatal(SharedHidError(f"Shared HID reader failed: {error}"))

    def _cooperative_transact(
        self,
        request: Sequence[int] | bytes,
        matcher: Callable[[bytes], bool],
        timeout_ms: int,
    ) -> bytes:
        self._write_device(request)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            report = self._read_device(remaining)
            if not report:
                continue
            if matcher(report):
                return report
            with self._condition:
                self._enqueue_locked(report)
        raise SharedHidError("Timed out waiting for a matching Raw HID response.")

    def _threaded_transact(
        self,
        request: Sequence[int] | bytes,
        matcher: Callable[[bytes], bool],
        timeout_ms: int,
    ) -> bytes:
        with self._condition:
            self._check_available_locked()
            if self._pending_matcher is not None:
                raise SharedHidError(
                    "Another shared HID transaction is already pending."
                )
            self._pending_matcher = matcher
            self._pending_response = None
        try:
            self._write_device(request)
            deadline = time.monotonic() + timeout_ms / 1000
            with self._condition:
                while self._pending_response is None:
                    self._check_available_locked()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SharedHidError(
                            "Timed out waiting for a matching Raw HID response."
                        )
                    self._condition.wait(remaining)
                response = self._pending_response
                self._pending_response = None
                return response
        finally:
            with self._condition:
                self._pending_matcher = None
                self._pending_response = None
                self._condition.notify_all()

    def transact(
        self,
        request: Sequence[int] | bytes,
        matcher: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        if timeout_ms <= 0:
            raise SharedHidError("Transaction timeout must be positive.")
        with self._transaction_lock:
            if self.mode is SharedHidMode.COOPERATIVE:
                return self._cooperative_transact(request, matcher, timeout_ms)
            return self._threaded_transact(request, matcher, timeout_ms)

    def write(self, request: Sequence[int] | bytes) -> None:
        with self._transaction_lock:
            self._write_device(request)

    def read(self, timeout_ms: int) -> bytes:
        if timeout_ms < 0:
            raise SharedHidError("Raw HID timeout cannot be negative.")
        if self.mode is SharedHidMode.COOPERATIVE:
            with self._transaction_lock:
                with self._condition:
                    self._check_available_locked()
                    if self._unmatched:
                        return self._unmatched.popleft()
                if timeout_ms == 0:
                    return b""
                return self._read_device(timeout_ms)

        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while not self._unmatched:
                self._check_available_locked()
                remaining = deadline - time.monotonic()
                if timeout_ms == 0 or remaining <= 0:
                    return b""
                self._condition.wait(remaining)
            return self._unmatched.popleft()

    def pop_unmatched(self) -> bytes | None:
        with self._condition:
            self._check_available_locked()
            return self._unmatched.popleft() if self._unmatched else None

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._stop.set()
            with self._condition:
                self._condition.notify_all()
            if self._reader_thread is not None:
                self._reader_thread.join(
                    timeout=max(1.0, self._reader_poll_ms / 1000 * 4)
                )
                if self._reader_thread.is_alive():
                    raise SharedHidError("Shared HID reader did not stop before close.")
            try:
                self._device.close()
            except OSError as error:
                raise SharedHidError(
                    "Could not close the shared Raw HID handle."
                ) from error
            finally:
                with self._condition:
                    self._closed = True
                    self._condition.notify_all()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


class SharedRgbTransportView:
    def __init__(self, session: SharedRawHidSession):
        self._session = session

    @staticmethod
    def _translate(error: SharedHidError) -> RgbTransportError:
        return RgbTransportError(str(error))

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        try:
            return self._session.transact(request, response_matches, timeout_ms)
        except SharedHidError as error:
            raise self._translate(error) from error

    def pop_unmatched(self) -> bytes | None:
        try:
            return self._session.pop_unmatched()
        except SharedHidError as error:
            raise self._translate(error) from error

    def close(self) -> None:
        """Borrowed view: the owning SharedRawHidSession closes the device."""


class SharedInteractionTransportView:
    def __init__(self, session: SharedRawHidSession):
        self._session = session

    @staticmethod
    def _translate(error: SharedHidError) -> InteractionTransportError:
        return InteractionTransportError(str(error))

    def transact(
        self,
        request: Sequence[int] | bytes,
        response_matches: Callable[[bytes], bool],
        timeout_ms: int = 1000,
    ) -> bytes:
        try:
            return self._session.transact(request, response_matches, timeout_ms)
        except SharedHidError as error:
            raise self._translate(error) from error

    def write(self, request: Sequence[int] | bytes) -> None:
        try:
            self._session.write(request)
        except SharedHidError as error:
            raise self._translate(error) from error

    def read(self, timeout_ms: int) -> bytes:
        try:
            return self._session.read(timeout_ms)
        except SharedHidError as error:
            raise self._translate(error) from error

    def pop_unmatched(self) -> bytes | None:
        try:
            return self._session.pop_unmatched()
        except SharedHidError as error:
            raise self._translate(error) from error

    def close(self) -> None:
        """Borrowed view: the owning SharedRawHidSession closes the device."""
