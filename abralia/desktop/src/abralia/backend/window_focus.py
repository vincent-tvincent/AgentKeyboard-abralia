# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Focus one existing native window belonging to a captured app lifetime.

The caller validates the Codex owner/ancestry and cancels stale requests. This
module independently rechecks the captured application's PID and start time.
It never opens a workspace, chooses a title, starts an app, or injects input.

VS Code's CLI IPC open-with-empty-URIs invokes _files.newWindow, not a focus
primitive (src/vs/workbench/api/node/extHostCLIServer.ts and remoteCLICommands).
Until a focus-only originating-window API exists, multiple windows are explicit
ambiguity. Windows Terminal similarly exposes window-only, not WT_SESSION focus.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time

from .client_lifetime import process_identity


def _result(provider, status, reason, verification='not_dispatched'):
    return {'provider': provider, 'status': status, 'specificity': 'window',
            'reason': reason, 'verification': verification}


def _is_expected_app(app, provider):
    executable = app.get('executable', '').replace('\\', '/').lower()
    basename = executable.rsplit('/', 1)[-1]
    if provider == 'windows_terminal':
        return basename in ('windowsterminal.exe', 'windowsterminalpreview.exe')
    if provider == 'wezterm':
        return basename in ('wezterm-gui', 'wezterm-gui.exe')
    return (basename in ('code', 'code.exe', 'code-insiders', 'code-insiders.exe', 'code - insiders.exe', 'code-oss')
            or basename == 'electron' and any(fragment in executable for fragment in
                ('/visual studio code.app/contents/macos/', '/visual studio code - insiders.app/contents/macos/')))


def _app_alive(app):
    current = process_identity(app['pid'])
    return current is not None and all(current.get(key) == app.get(key) for key in ('pid', 'started', 'executable'))


def _run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=2,
                            env={**os.environ, 'LC_ALL': 'C'}, shell=False)
    if result.returncode:
        raise OSError('native_window_command_failed')
    if len(result.stdout) > 1024 * 1024:
        raise OSError('native_window_response_too_large')
    return result.stdout


def _x11_windows(output, pid):
    windows = set()
    for line in output.splitlines():
        fields = line.split(None, 4)
        # wmctrl includes a title after these fields. It is neither retained nor
        # used for target selection; only native window ID and owner PID matter.
        if len(fields) >= 3 and re.fullmatch(r'0x[0-9a-fA-F]{1,8}', fields[0]) and fields[2].isdigit():
            if int(fields[2]) == pid:
                windows.add(int(fields[0], 16))
    return sorted(windows)


def _x11_pid(output):
    match = re.fullmatch(r'_NET_WM_PID\([^)]*\)\s*=\s*(\d+)\s*', output.strip())
    return int(match[1]) if match else None


def _x11_active(output):
    match = re.search(r'_NET_ACTIVE_WINDOW\([^)]*\):\s*window id #\s*(0x[0-9a-fA-F]+)\s*$', output.strip())
    return int(match[1], 16) if match else None


def _focus_x11(app, provider):
    if os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland':
        return _result(provider, 'unavailable', 'wayland_window_focus_unavailable')
    if not os.environ.get('DISPLAY'):
        return _result(provider, 'unavailable', 'x11_display_unavailable')
    wmctrl, xprop = shutil.which('wmctrl'), shutil.which('xprop')
    if wmctrl is None or xprop is None:
        return _result(provider, 'unavailable', 'x11_window_tools_unavailable')
    windows = _x11_windows(_run([wmctrl, '-lp']), app['pid'])
    if len(windows) != 1:
        return _result(provider, 'unavailable', 'multiple_app_windows' if windows else 'app_window_missing')
    window = windows[0]
    identifier = f'0x{window:08x}'
    if not _app_alive(app) or _x11_pid(_run([xprop, '-id', identifier, '_NET_WM_PID'])) != app['pid']:
        return _result(provider, 'unavailable', 'app_window_identity_changed')
    try:
        _run([wmctrl, '-ia', identifier])
        deadline = time.monotonic() + .6
        while True:
            if _x11_active(_run([xprop, '-root', '_NET_ACTIVE_WINDOW'])) == window:
                if _app_alive(app) and _x11_pid(_run([xprop, '-id', identifier, '_NET_WM_PID'])) == app['pid']:
                    return _result(provider, 'focused', 'window_only', 'foreground_window_verified')
                return _result(provider, 'failed', 'app_window_identity_changed', 'dispatched_unverified')
            if time.monotonic() >= deadline:
                return _result(provider, 'failed', 'window_focus_not_confirmed', 'dispatched_unverified')
            time.sleep(.03)
    except (OSError, subprocess.TimeoutExpired):
        return _result(provider, 'failed', 'window_focus_not_confirmed', 'dispatched_unverified')


class _Windows:
    def __init__(self):
        from ctypes import wintypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        signatures = {
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsWindow': ([wintypes.HWND], wintypes.BOOL),
            'GetWindow': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            'IsIconic': ([wintypes.HWND], wintypes.BOOL),
            'ShowWindowAsync': ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            'SetForegroundWindow': ([wintypes.HWND], wintypes.BOOL),
            'GetForegroundWindow': ([], wintypes.HWND),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = args, result

    def belongs(self, hwnd, pid):
        actual = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual))
        return bool(self.user32.IsWindow(hwnd)) and actual.value == pid

    def windows(self, pid):
        found = []
        callback_type = ctypes.WINFUNCTYPE(self.wintypes.BOOL, self.wintypes.HWND, self.wintypes.LPARAM)
        @callback_type
        def callback(hwnd, _parameter):
            if (self.user32.IsWindowVisible(hwnd) and not self.user32.GetWindow(hwnd, 4)
                    and self.belongs(hwnd, pid)):
                found.append(int(hwnd))
            return True
        self.user32.EnumWindows.argtypes = [callback_type, self.wintypes.LPARAM]
        self.user32.EnumWindows.restype = self.wintypes.BOOL
        if not self.user32.EnumWindows(callback, 0):
            raise OSError('window_enumeration_failed')
        return found

    def raise_window(self, hwnd):
        if self.user32.IsIconic(hwnd):
            self.user32.ShowWindowAsync(hwnd, 9)  # Restore this existing window.
        return bool(self.user32.SetForegroundWindow(hwnd))

    def foreground(self):
        return int(self.user32.GetForegroundWindow() or 0)


def _focus_windows(app, provider):
    native = _Windows()
    windows = native.windows(app['pid'])
    if len(windows) != 1:
        return _result(provider, 'unavailable', 'multiple_app_windows' if windows else 'app_window_missing')
    hwnd = windows[0]
    if not _app_alive(app) or not native.belongs(hwnd, app['pid']):
        return _result(provider, 'unavailable', 'app_window_identity_changed')
    accepted = native.raise_window(hwnd)
    if not accepted and native.foreground() != hwnd:
        # Windows foreground restrictions are respected. No synthetic Alt key,
        # AttachThreadInput, topmost toggle or permission change is attempted.
        return _result(provider, 'failed', 'foreground_request_denied', 'dispatched_unverified')
    deadline = time.monotonic() + .6
    while True:
        if native.foreground() == hwnd:
            if _app_alive(app) and native.belongs(hwnd, app['pid']):
                reason = 'window_only_tab_not_selected' if provider == 'windows_terminal' else 'window_only'
                return _result(provider, 'focused', reason, 'foreground_window_verified')
            return _result(provider, 'failed', 'app_window_identity_changed', 'dispatched_unverified')
        if time.monotonic() >= deadline:
            return _result(provider, 'failed', 'window_focus_not_confirmed', 'dispatched_unverified')
        time.sleep(.03)


class _Accessibility:
    """Public macOS AX APIs, with a non-prompting permission check."""
    def __init__(self):
        self.ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        signatures = [
            (self.ax, 'AXIsProcessTrusted', [], ctypes.c_bool),
            (self.ax, 'AXUIElementCreateApplication', [ctypes.c_int], ctypes.c_void_p),
            (self.ax, 'AXUIElementCopyAttributeValue', [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            (self.ax, 'AXUIElementSetAttributeValue', [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            (self.ax, 'AXUIElementPerformAction', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
            (self.ax, 'AXUIElementSetMessagingTimeout', [ctypes.c_void_p, ctypes.c_float], ctypes.c_int),
            (self.cf, 'CFStringCreateWithCString', [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_void_p),
            (self.cf, 'CFArrayGetCount', [ctypes.c_void_p], ctypes.c_long),
            (self.cf, 'CFArrayGetValueAtIndex', [ctypes.c_void_p, ctypes.c_long], ctypes.c_void_p),
            (self.cf, 'CFBooleanGetValue', [ctypes.c_void_p], ctypes.c_bool),
            (self.cf, 'CFEqual', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_bool),
            (self.cf, 'CFRelease', [ctypes.c_void_p], None),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result
        self.references = []
        self.app = self.window = None

    def string(self, text):
        value = self.cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)
        self.references.append(value)
        return value

    def read(self, element, attribute):
        result = ctypes.c_void_p()
        code = self.ax.AXUIElementCopyAttributeValue(element, self.string(attribute), ctypes.byref(result))
        if code or not result.value:
            raise OSError('accessibility_attribute_unavailable')
        self.references.append(result.value)
        return result.value

    def trusted(self):
        return bool(self.ax.AXIsProcessTrusted())

    def windows(self, pid):
        self.app = self.ax.AXUIElementCreateApplication(pid)
        if not self.app:
            raise OSError('accessibility_app_unavailable')
        self.references.append(self.app)
        self.ax.AXUIElementSetMessagingTimeout(self.app, 1.)
        array = self.read(self.app, 'AXWindows')
        count = self.cf.CFArrayGetCount(array)
        if count == 1:
            self.window = self.cf.CFArrayGetValueAtIndex(array, 0)
            self.ax.AXUIElementSetMessagingTimeout(self.window, 1.)
        return count

    def raise_window(self):
        true = ctypes.c_void_p.in_dll(self.cf, 'kCFBooleanTrue').value
        false = ctypes.c_void_p.in_dll(self.cf, 'kCFBooleanFalse').value
        try:
            minimized = self.cf.CFBooleanGetValue(self.read(self.window, 'AXMinimized'))
        except OSError:
            minimized = False
        if minimized and self.ax.AXUIElementSetAttributeValue(self.window, self.string('AXMinimized'), false):
            return False
        if self.ax.AXUIElementSetAttributeValue(self.app, self.string('AXFrontmost'), true):
            return False
        return self.ax.AXUIElementPerformAction(self.window, self.string('AXRaise')) == 0

    def confirmed(self):
        frontmost = self.cf.CFBooleanGetValue(self.read(self.app, 'AXFrontmost'))
        focused = self.read(self.app, 'AXFocusedWindow')
        return bool(frontmost and self.cf.CFEqual(focused, self.window))

    def close(self):
        for ref in reversed(self.references):
            if ref:
                self.cf.CFRelease(ref)
        self.references.clear()


def _focus_macos(app, provider):
    native = _Accessibility()
    try:
        if not native.trusted():
            return _result(provider, 'unavailable', 'accessibility_permission_required')
        count = native.windows(app['pid'])
        if count != 1:
            return _result(provider, 'unavailable', 'multiple_app_windows' if count else 'app_window_missing')
        if not _app_alive(app):
            return _result(provider, 'unavailable', 'app_window_identity_changed')
        if not native.raise_window():
            return _result(provider, 'failed', 'window_focus_denied', 'dispatched_unverified')
        deadline = time.monotonic() + .6
        while True:
            try:
                confirmed = native.confirmed()
            except OSError:
                return _result(provider, 'failed', 'window_focus_not_confirmed', 'dispatched_unverified')
            if confirmed:
                return (_result(provider, 'focused', 'window_only', 'foreground_window_verified') if _app_alive(app)
                        else _result(provider, 'failed', 'app_window_identity_changed', 'dispatched_unverified'))
            if time.monotonic() >= deadline:
                return _result(provider, 'failed', 'window_focus_not_confirmed', 'dispatched_unverified')
            time.sleep(.03)
    finally:
        native.close()


def focus_window(context: dict) -> dict:
    """Use captured ancestry, never a model-supplied PID or environment guess."""
    if not isinstance(context, dict) or context.get('provider') not in ('vscode', 'windows_terminal', 'wezterm'):
        return _result('unknown', 'unavailable', 'unsupported_window_provider')
    provider, app = context['provider'], context.get('app')
    if (not isinstance(app, dict) or type(app.get('pid')) is not int or app['pid'] <= 1
            or not isinstance(app.get('started'), str) or not app['started']
            or not isinstance(app.get('executable'), str) or not _is_expected_app(app, provider)):
        return _result(provider, 'unavailable', 'captured_app_identity_required')
    if not _app_alive(app):
        return _result(provider, 'unavailable', 'app_process_identity_changed')
    try:
        if sys.platform == 'win32':
            return _focus_windows(app, provider)
        if provider == 'windows_terminal':
            return _result(provider, 'unavailable', 'windows_terminal_requires_windows')
        if sys.platform == 'darwin':
            return _focus_macos(app, provider)
        if sys.platform.startswith('linux'):
            return _focus_x11(app, provider)
        return _result(provider, 'unavailable', 'window_platform_unsupported')
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return _result(provider, 'unavailable', 'native_window_api_unavailable')
