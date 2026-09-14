# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read-only lifetime evidence; no other process's arguments or environment are read."""

import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
from uuid import UUID

TERMINAL_ENV_VARS = ('TERM_PROGRAM', 'TERM_PROGRAM_VERSION', 'GHOSTTY_SURFACE_ID',
    'ITERM_SESSION_ID', 'WEZTERM_PANE', 'WEZTERM_UNIX_SOCKET', 'KITTY_WINDOW_ID', 'KITTY_LISTEN_ON',
    'VSCODE_IPC_HOOK_CLI', 'VSCODE_PID', 'WT_SESSION')
TERMINAL_PROVIDERS = ('ghostty', 'terminal', 'iterm2', 'wezterm', 'kitty', 'vscode', 'windows_terminal')


def _name(executable):
    return (PureWindowsPath(executable).name if '\\' in executable else Path(executable).name).lower()


def _windows_process_identity(pid):
    """Native process metadata only: no command line, memory, or environment reads."""
    import ctypes as c
    from ctypes import wintypes as w
    class Entry(c.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD), ('heap', c.c_size_t),
            ('module', w.DWORD), ('threads', w.DWORD), ('parent', w.DWORD), ('priority', w.LONG),
            ('flags', w.DWORD), ('exe', w.WCHAR * 260)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = w.BOOL
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == c.c_void_p(-1).value:
        return None
    parent = None
    try:
        entry = Entry(); entry.size = c.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, c.byref(entry))
        while more:
            if entry.pid == pid:
                parent = int(entry.parent)
                break
            more = kernel.Process32NextW(snapshot, c.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if parent is None:
        return None
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size, buffer = w.DWORD(32768), c.create_unicode_buffer(32768)
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, c.byref(size)):
            return None
        if not kernel.GetProcessTimes(handle, *(c.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {'pid': pid, 'parent': parent, 'started': f'windows:{created}',
                'tty': '?', 'executable': buffer.value}
    finally:
        kernel.CloseHandle(handle)


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return None
    if sys.platform == 'win32':
        try:
            return _windows_process_identity(pid)
        except (OSError, ValueError, AttributeError):
            return None
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid=,ppid=,lstart=,tty=,comm='],
                                capture_output=True, text=True, timeout=1,
                                env={**os.environ, 'LC_ALL':'C', 'TZ':'UTC'})
        parts = result.stdout.strip().split(None, 8)
        if result.returncode or len(parts) != 9 or int(parts[0]) != pid:
            return None
        executable = parts[8]
        if sys.platform.startswith('linux'):
            try:
                executable = os.readlink(f'/proc/{pid}/exe')
            except OSError:
                pass
        return {'pid':pid, 'parent':int(parts[1]), 'started':' '.join(parts[2:7]),
                'tty':parts[7], 'executable':executable}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def capture_client_owner(start_pid=None):
    pid = os.getppid() if start_pid is None else start_pid
    seen, terminal, editor = set(), None, None
    for _ in range(24):
        if pid in seen:
            break
        seen.add(pid)
        identity = process_identity(pid)
        if identity is None:
            break
        executable = identity['executable']
        name = _name(executable)
        if '/Contents/MacOS/' in executable and name in ('codex', 'chatgpt'):
            owner = {**identity, 'kind':'codex_gui'}
            if terminal:
                owner['client_process'] = terminal
            return owner
        if name in ('codex', 'codex.exe') and (identity['tty'] not in ('??', '?', '-') or
                sys.platform == 'win32' and _native_locator(_terminal_environment(os.environ))):
            terminal = terminal or {**identity, 'kind':'codex_tui'}
        elif name in ('codex', 'codex.exe'):
            editor = editor or {**identity, 'kind': 'codex_editor'}
        pid = identity['parent']
    if terminal is None and editor is not None:
        app, ambiguous = _terminal_ancestor(editor)
        if not ambiguous and app is not None and _provider(app) == 'vscode':
            return editor
    return terminal


def owner_alive(owner, _depth=0):
    if not isinstance(owner, dict) or owner.get('kind') not in ('codex_gui','codex_tui','codex_editor'):
        return False
    current = process_identity(owner.get('pid'))
    if current is None or any(current.get(k) != owner.get(k) for k in ('started','executable','tty')):
        return False
    name = _name(current['executable'])
    if owner['kind'] == 'codex_editor':
        if (name not in ('codex', 'codex.exe') or current['tty'] not in ('??', '?', '-')
                or current['parent'] != owner.get('parent')):
            return False
        app, ambiguous = _terminal_ancestor(owner)
        return not ambiguous and app is not None and _provider(app) == 'vscode'
    if owner['kind'] == 'codex_gui':
        valid = '/Contents/MacOS/' in current['executable'] and name in ('codex','chatgpt')
        nested = owner.get('client_process')
        if nested is not None:
            return bool(valid and _depth == 0 and isinstance(nested, dict)
                        and nested.get('kind') == 'codex_tui' and 'client_process' not in nested
                        and owner_alive(nested, 1))
        return valid
    return name in ('codex', 'codex.exe') and (current['tty'] not in ('??','?','-') or sys.platform == 'win32')


def _terminal_environment(environment):
    result = {}
    if not isinstance(environment, dict):
        environment = dict(environment)
    for name in TERMINAL_ENV_VARS:
        value = environment.get(name)
        if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
            continue
        if name in ('WEZTERM_PANE', 'KITTY_WINDOW_ID', 'VSCODE_PID'):
            if not value.isascii() or not value.isdigit() or len(value) > 20:
                continue
        if name == 'GHOSTTY_SURFACE_ID' and not re.fullmatch(r'0x[0-9a-fA-F]{1,16}', value):
            continue
        if name == 'GHOSTTY_SURFACE_ID' and int(value, 16) == 0:
            continue
        if name == 'WT_SESSION':
            try: value = str(UUID(value))
            except (ValueError, AttributeError): continue
        if name == 'ITERM_SESSION_ID' and not re.fullmatch(r'(?:w\d+t\d+p\d+:)?[0-9a-fA-F-]{36}', value):
            continue
        if name == 'ITERM_SESSION_ID':
            try: UUID(value.rsplit(':', 1)[-1])
            except ValueError: continue
        if name.endswith('SOCKET') or name in ('KITTY_LISTEN_ON', 'VSCODE_IPC_HOOK_CLI'):
            # A native local IPC address is data, never a shell/network command.
            address = value.removeprefix('unix:') if name == 'KITTY_LISTEN_ON' else value
            if not (Path(address).is_absolute() or PureWindowsPath(address).is_absolute() or
                    name == 'KITTY_LISTEN_ON' and address.startswith('@')):
                continue
        result[name] = value
    return result


def _native_locator(environment):
    return any(environment.get(name) for name in ('GHOSTTY_SURFACE_ID', 'ITERM_SESSION_ID',
        'WEZTERM_PANE', 'KITTY_WINDOW_ID', 'VSCODE_IPC_HOOK_CLI', 'WT_SESSION'))


def _provider(identity):
    name = _name(identity['executable'])
    if '/Visual Studio Code.app/' in identity['executable'] or '/Visual Studio Code - Insiders.app/' in identity['executable']:
        return 'vscode'
    names = {'ghostty': 'ghostty', 'terminal': 'terminal', 'iterm2': 'iterm2',
             'wezterm-gui': 'wezterm', 'wezterm-gui.exe': 'wezterm', 'kitty': 'kitty',
             'windowsterminal.exe': 'windows_terminal', 'windowsterminalpreview.exe': 'windows_terminal',
             'code': 'vscode', 'code.exe': 'vscode', 'code - insiders': 'vscode', 'code - insiders.exe': 'vscode',
             'code-insiders': 'vscode', 'code-oss': 'vscode'}
    if name.startswith('code helper'):
        return 'vscode'
    return names.get(name)


def _terminal_ancestor(owner):
    pid, seen, candidate = owner['parent'], {owner['pid']}, None
    for _ in range(24):
        if pid <= 1:
            return candidate, False
        if pid in seen:
            return None, True
        seen.add(pid)
        row = process_identity(pid)
        if row is None:
            return None, True
        name = _name(row['executable'])
        provider = _provider(row)
        if candidate is not None and provider != 'vscode':
            return candidate, False
        if name.startswith(('tmux', 'sshd', 'mosh')) or name in ('screen', 'screen.exe', 'ssh', 'ssh.exe', 'byobu'):
            return None, True
        if provider == 'vscode':
            # Helper/renderer processes usually own no windows. Continue to
            # the outer Code process; only its native window can be raised.
            if not name.startswith('code helper'):
                candidate = row
        elif provider:
            return row, False
        pid = row['parent']
    return None, True


def _environment_provider(environment):
    for key, provider in (('VSCODE_IPC_HOOK_CLI', 'vscode'), ('WT_SESSION', 'windows_terminal'),
            ('ITERM_SESSION_ID', 'iterm2'), ('WEZTERM_PANE', 'wezterm'), ('KITTY_WINDOW_ID', 'kitty'),
            ('GHOSTTY_SURFACE_ID', 'ghostty')):
        if environment.get(key):
            return provider
    return {'ghostty': 'ghostty', 'apple_terminal': 'terminal', 'iterm.app': 'iterm2',
            'wezterm': 'wezterm', 'vscode': 'vscode'}.get(environment.get('TERM_PROGRAM', '').lower())


def _identity(value, *, owner=False):
    if not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 1:
        return None
    if type(value.get('parent')) is not int or value['parent'] < 0:
        return None
    for key, maximum in (('started', 128), ('tty', 64), ('executable', 4096)):
        text = value.get(key)
        if not isinstance(text, str) or not text or len(text) > maximum or any(ord(c) < 32 for c in text):
            return None
    if owner and (value.get('kind') not in ('codex_tui', 'codex_editor') or _name(value['executable']) not in ('codex', 'codex.exe')):
        return None
    keys = ('pid', 'parent', 'started', 'tty', 'executable', 'kind') if owner else ('pid', 'parent', 'started', 'tty', 'executable')
    return {key: value[key] for key in keys}


def normalize_terminal_context(candidate, *, verify=False):
    """Validate a fixed routing schema; callers cannot submit commands or URLs."""
    if not isinstance(candidate, dict) or set(candidate) - {'owner', 'provider', 'app', 'environment'}:
        return None
    owner = _identity(candidate.get('owner'), owner=True)
    provider = candidate.get('provider')
    environment = candidate.get('environment', {})
    if owner is None or provider not in TERMINAL_PROVIDERS or not isinstance(environment, dict):
        return None
    if set(environment) - set(TERMINAL_ENV_VARS) or _terminal_environment(environment) != environment:
        return None
    app = _identity(candidate['app']) if candidate.get('app') is not None else None
    if candidate.get('app') is not None and (app is None or _provider(app) != provider):
        return None
    if owner['kind'] == 'codex_editor' and (provider != 'vscode' or app is None):
        return None
    if app is None and (not _native_locator(environment) or _environment_provider(environment) != provider):
        return None
    if owner['kind'] != 'codex_editor' and owner['tty'] in ('??', '?', '-') and not _native_locator(environment):
        return None
    if verify:
        if not owner_alive(owner):
            return None
        current_owner = process_identity(owner['pid'])
        if current_owner is None or current_owner['parent'] != owner['parent']:
            return None
        current, ambiguous = _terminal_ancestor(owner)
        if ambiguous or app is not None and (current is None or any(current[k] != app[k] for k in ('pid', 'started', 'executable'))):
            return None
        if current is not None and _provider(current) != provider:
            return None
    return {'owner': owner, 'provider': provider, 'app': app, 'environment': dict(environment)}


def capture_terminal_context(owner, *, environment=None):
    """Capture only our inherited terminal hints and verified process ancestors."""
    owner = _identity(owner, owner=True)
    if owner is None or not owner_alive(owner):
        return None
    current_owner = process_identity(owner['pid'])
    if current_owner is None or current_owner['parent'] != owner['parent']:
        return None
    app, ambiguous = _terminal_ancestor(owner)
    if ambiguous:
        return None
    allowed = _terminal_environment(os.environ if environment is None else environment)
    provider = _provider(app) if app is not None else _environment_provider(allowed)
    return normalize_terminal_context({'owner': owner, 'provider': provider, 'app': app, 'environment': allowed})
