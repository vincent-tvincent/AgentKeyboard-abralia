# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Non-seizing macOS HID listener, filtered to Pause on one USB keyboard.

This observes the ordinary HID usage, not a physical matrix address. It never
changes firmware, suppresses OS input, or collects other keyboard usages.
"""

from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import plistlib
import queue
import sys
import threading
import time


@dataclass(frozen=True)
class PauseTap:
    down_at: float
    up_at: float


class PauseEdges:
    """Ignore repeat values, unmatched releases, and duplicate interface reports."""

    def __init__(self):
        self.held = {}
        self.last_up = float("-inf")

    def feed(self, identity, pressed: bool, when: float) -> PauseTap | None:
        if pressed:
            self.held.setdefault(identity, when)
            return None
        down = self.held.pop(identity, None)
        if down is None or when < down or when - self.last_up < 0.02:
            return None
        self.last_up = when
        return PauseTap(down, when)


class Native:
    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("The host-side Pause listener currently supports macOS only.")
        self.io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.system = C.CDLL("/usr/lib/libSystem.B.dylib")
        pointer, integer, index = C.c_void_p, C.c_uint32, C.c_long
        declarations = (
            (self.io, "IOHIDCheckAccess", integer, [integer]),
            (self.io, "IOHIDManagerCreate", pointer, [pointer, integer]),
            (self.io, "IOHIDManagerSetDeviceMatching", None, [pointer, pointer]),
            (self.io, "IOHIDManagerSetInputValueMatching", None, [pointer, pointer]),
            (self.io, "IOHIDManagerRegisterInputValueCallback", None, [pointer, pointer, pointer]),
            (self.io, "IOHIDManagerRegisterDeviceRemovalCallback", None, [pointer, pointer, pointer]),
            (self.io, "IOHIDManagerScheduleWithRunLoop", None, [pointer, pointer, pointer]),
            (self.io, "IOHIDManagerUnscheduleFromRunLoop", None, [pointer, pointer, pointer]),
            (self.io, "IOHIDManagerOpen", C.c_int32, [pointer, integer]),
            (self.io, "IOHIDManagerClose", C.c_int32, [pointer, integer]),
            (self.io, "IOHIDManagerCopyDevices", pointer, [pointer]),
            (self.io, "IOHIDDeviceGetProperty", pointer, [pointer, pointer]),
            (self.io, "IOHIDValueGetElement", pointer, [pointer]),
            (self.io, "IOHIDValueGetIntegerValue", index, [pointer]),
            (self.io, "IOHIDValueGetTimeStamp", C.c_uint64, [pointer]),
            (self.io, "IOHIDElementGetDevice", pointer, [pointer]),
            (self.io, "IOHIDElementGetUsagePage", integer, [pointer]),
            (self.io, "IOHIDElementGetUsage", integer, [pointer]),
            (self.io, "IOHIDElementGetCookie", integer, [pointer]),
            (self.cf, "CFDataCreate", pointer, [pointer, pointer, index]),
            (self.cf, "CFPropertyListCreateWithData", pointer, [pointer, pointer, C.c_ulong, pointer, pointer]),
            (self.cf, "CFStringCreateWithCString", pointer, [pointer, C.c_char_p, integer]),
            (self.cf, "CFNumberGetValue", C.c_bool, [pointer, index, pointer]),
            (self.cf, "CFSetGetCount", index, [pointer]),
            (self.cf, "CFSetGetValues", None, [pointer, pointer]),
            (self.cf, "CFRunLoopGetCurrent", pointer, []),
            (self.cf, "CFRunLoopRunInMode", C.c_int32, [pointer, C.c_double, C.c_bool]),
            (self.cf, "CFRelease", None, [pointer]),
            (self.system, "mach_absolute_time", C.c_uint64, []),
            (self.system, "mach_timebase_info", C.c_int32, [pointer]),
        )
        for library, name, result, arguments in declarations:
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        self.default_mode = C.c_void_p.in_dll(self.cf, "kCFRunLoopDefaultMode").value
        factors = (C.c_uint32 * 2)()
        if self.system.mach_timebase_info(factors) != 0 or not factors[1]:
            raise RuntimeError("Cannot convert the HID event clock.")
        self.seconds_per_tick = factors[0] / factors[1] / 1e9
        self.clock_offset = time.monotonic() - self.system.mach_absolute_time() * self.seconds_per_tick

    def dictionary(self, values):
        raw = plistlib.dumps(values, fmt=plistlib.FMT_BINARY)
        buffer = C.create_string_buffer(raw)
        data = self.cf.CFDataCreate(None, buffer, len(raw))
        if not data:
            raise RuntimeError("Cannot allocate a HID matching dictionary.")
        try:
            result = self.cf.CFPropertyListCreateWithData(None, data, 0, None, None)
            if not result:
                raise RuntimeError("Cannot decode the HID matching dictionary.")
            return result
        finally:
            self.cf.CFRelease(data)


class PauseListener:
    def __init__(self, vendor_id: int, product_id: int):
        self.vendor_id, self.product_id = vendor_id, product_id
        self.events = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.location_id = None
        self.device_count = 0
        self.thread = None

    def _worker(self):
        manager = loop = native = location_key = None
        retained = []
        try:
            native = Native()
            if native.io.IOHIDCheckAccess(1) != 0:
                raise RuntimeError("macOS Input Monitoring is not granted to this process. Check the terminal/app's Input Monitoring permission; no permission is changed automatically.")
            location_key = native.cf.CFStringCreateWithCString(None, b"LocationID", 0x08000100)
            retained.append(location_key)

            def location(device):
                value = native.io.IOHIDDeviceGetProperty(device, location_key)
                number = C.c_int64()
                if not value or not native.cf.CFNumberGetValue(value, 4, C.byref(number)):
                    raise RuntimeError("Cannot identify the keyboard's USB location.")
                return number.value

            edges = PauseEdges()
            callback_type = C.CFUNCTYPE(None, C.c_void_p, C.c_int32, C.c_void_p, C.c_void_p)

            @callback_type
            def received(_context, result, _sender, value):
                try:
                    if result or self.location_id is None:
                        return
                    element = native.io.IOHIDValueGetElement(value)
                    if (native.io.IOHIDElementGetUsagePage(element), native.io.IOHIDElementGetUsage(element)) != (7, 0x48):
                        return
                    device = native.io.IOHIDElementGetDevice(element)
                    if location(device) != self.location_id:
                        raise RuntimeError("A different matching keyboard appeared; ending the input listener.")
                    when = (native.io.IOHIDValueGetTimeStamp(value) * native.seconds_per_tick
                            + native.clock_offset)
                    identity = (device, native.io.IOHIDElementGetCookie(element))
                    tap = edges.feed(identity, bool(native.io.IOHIDValueGetIntegerValue(value)), when)
                    if tap is not None:
                        self.events.put_nowait(tap)
                except Exception as error:
                    self.error = str(error) or "Pause input queue overflow."
                    self.stop.set()

            @callback_type
            def removed(_context, _result, _sender, _device):
                self.error = "A matched keyboard interface disconnected; restart the demo."
                self.stop.set()

            manager = native.io.IOHIDManagerCreate(None, 0)
            if not manager:
                raise RuntimeError("Cannot create the macOS HID listener.")
            device_filter = native.dictionary({"VendorID": self.vendor_id, "ProductID": self.product_id,
                                               "DeviceUsagePage": 1, "DeviceUsage": 6})
            value_filter = native.dictionary({"UsagePage": 7, "Usage": 0x48})
            retained.extend((device_filter, value_filter))
            native.io.IOHIDManagerSetDeviceMatching(manager, device_filter)
            native.io.IOHIDManagerSetInputValueMatching(manager, value_filter)
            native.io.IOHIDManagerRegisterInputValueCallback(manager, C.cast(received, C.c_void_p), None)
            native.io.IOHIDManagerRegisterDeviceRemovalCallback(manager, C.cast(removed, C.c_void_p), None)
            loop = native.cf.CFRunLoopGetCurrent()
            native.io.IOHIDManagerScheduleWithRunLoop(manager, loop, native.default_mode)
            result = native.io.IOHIDManagerOpen(manager, 0)  # No seize/exclusive option.
            if result:
                raise RuntimeError(f"Cannot open the read-only HID listener: 0x{result & 0xffffffff:08x}.")
            devices = native.io.IOHIDManagerCopyDevices(manager)
            if not devices:
                raise RuntimeError("No matching keyboard input interface is visible to this process.")
            try:
                self.device_count = native.cf.CFSetGetCount(devices)
                values = (C.c_void_p * self.device_count)()
                native.cf.CFSetGetValues(devices, values)
                locations = {location(device) for device in values}
                if len(locations) != 1:
                    raise RuntimeError("The Pause listener requires exactly one matching physical USB keyboard.")
                self.location_id = locations.pop()
            finally:
                native.cf.CFRelease(devices)
            self.ready.set()
            while not self.stop.is_set():
                native.cf.CFRunLoopRunInMode(native.default_mode, 0.02, True)
        except Exception as error:
            self.error = str(error)
        finally:
            self.ready.set()
            if native is not None:
                if manager:
                    if loop:
                        native.io.IOHIDManagerUnscheduleFromRunLoop(manager, loop, native.default_mode)
                    native.io.IOHIDManagerClose(manager, 0)
                    native.cf.CFRelease(manager)
                for value in reversed(retained):
                    if value:
                        native.cf.CFRelease(value)

    def poll(self) -> tuple[PauseTap, ...]:
        if self.error:
            raise RuntimeError(self.error)
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                return tuple(result)

    def __enter__(self):
        self.thread = threading.Thread(target=self._worker, name="abralia-pause-listener", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            self.close()
            raise RuntimeError("Timed out starting the Pause listener.")
        if self.error:
            self.close()
            raise RuntimeError(self.error)
        return self

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def __exit__(self, *_):
        self.close()
