# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Native terminal-surface selection. Never inject input or launch a terminal.

Contracts: iterm2.com/documentation-scripting.html;
wezterm.org/cli/cli/{list,activate-pane}.html and wezterm/src/cli/mod.rs;
sw.kovidgoyal.net/kitty/remote-control/;
github.com/ghostty-org/ghostty/pull/11922 and macos/Ghostty.sdef.
Terminal.app's installed Terminal.sdef supplies tab.tty/selected and window.id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from .client_lifetime import owner_alive, process_identity


PROVIDERS = frozenset(('terminal', 'iterm2', 'ghostty', 'wezterm', 'kitty'))
ENVIRONMENT_KEYS = frozenset(('ITERM_SESSION_ID', 'WEZTERM_PANE', 'WEZTERM_UNIX_SOCKET',
                              'KITTY_WINDOW_ID', 'KITTY_LISTEN_ON'))
_BUNDLES = {'terminal': 'com.apple.Terminal', 'iterm2': 'com.googlecode.iterm2',
            'ghostty': 'com.mitchellh.ghostty', 'wezterm': 'com.github.wez.wezterm', 'kitty': 'net.kovidgoyal.kitty'}
_APP_NAMES = {'terminal': {'Terminal'}, 'iterm2': {'iTerm2', 'iTerm'}, 'ghostty': {'ghostty', 'Ghostty'},
              'wezterm': {'wezterm-gui', 'wezterm', 'wezterm-gui.exe'}, 'kitty': {'kitty'}}
MAX_OUTPUT = 2 * 1024 * 1024


class Unavailable(Exception):
    pass


def _result(provider, status='unavailable', reason='terminal_unavailable', *, specificity='terminal_surface', verified=False):
    return {'status': status, 'provider': provider, 'specificity': specificity,
            'verification': ('surface_verified' if verified else 'dispatched_unverified') if status == 'focused' else None,
            'reason': reason}


def _tty(value):
    if not isinstance(value, str) or len(value) > 128:
        return None
    if value.startswith(('ttys', 'pts/')):
        value = '/dev/' + value
    return value if re.fullmatch(r'/dev/[A-Za-z0-9_./-]+', value) else None


def _live(context):
    owner = context.get('owner')
    if not isinstance(owner, dict) or owner.get('kind') != 'codex_tui' or not owner_alive(owner):
        raise Unavailable('terminal_owner_stale')
    app = context.get('app')
    if app is not None:
        if not isinstance(app, dict) or Path(app.get('executable', '')).name not in _APP_NAMES[context['provider']]:
            raise Unavailable('terminal_app_mismatch')
        current = process_identity(app.get('pid'))
        if current is None or any(current.get(key) != app.get(key) for key in ('started', 'executable')):
            raise Unavailable('terminal_app_stale')


def _ancestor(context):
    app = context.get('app')
    if not app or not _tty(context['owner'].get('tty')):
        return False
    pid, seen = context['owner']['pid'], set()
    deadline = time.monotonic() + 2
    for _ in range(16):
        if pid in seen or time.monotonic() >= deadline:
            return False
        if pid == app['pid']:
            return True
        seen.add(pid)
        identity = process_identity(pid)
        if identity is None:
            return False
        pid = identity.get('parent')
    return False


def _environment(context):
    supplied = context.get('environment', context.get('env', {}))
    if not isinstance(supplied, dict):
        raise Unavailable('invalid_terminal_environment')
    result = {}
    for key in ENVIRONMENT_KEYS:
        value = supplied.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > 2048 or any(c in value for c in '\0\r\n'):
                raise Unavailable('invalid_terminal_environment')
            result[key] = value
    return result


def _run(command, *, env=None, script=None):
    environment = {key: value for key, value in os.environ.items() if key not in ENVIRONMENT_KEYS}
    environment.update(env or {})
    try:
        result = subprocess.run(command, input=script, capture_output=True, text=True,
                                timeout=2, env=environment)
    except subprocess.TimeoutExpired as error:
        raise Unavailable('terminal_api_timeout') from error
    except OSError as error:
        raise Unavailable('terminal_api_unavailable') from error
    if len(result.stdout) > MAX_OUTPUT:
        raise Unavailable('terminal_response_too_large')
    if result.returncode:
        if '-1743' in result.stderr or 'not authorized' in result.stderr.lower():
            raise Unavailable('terminal_automation_permission_required')
        raise Unavailable('terminal_api_unavailable')
    return result.stdout.strip()


def _cli(provider, context):
    names = ('wezterm', 'wezterm.exe') if provider == 'wezterm' else ('kitten', 'kitty')
    explicit = context.get('cli_executable')
    candidates = [Path(explicit)] if isinstance(explicit, str) else []
    app = context.get('app')
    if app:
        candidates += [Path(app['executable']).parent / name for name in names]
    if explicit is None:
        candidates += [Path(found) for name in names if (found := shutil.which(name))]
    for path in candidates:
        if path.name in names and path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise Unavailable('terminal_cli_unavailable')


_MAC_GUARD = '''use framework "AppKit"
on run argv
    set expectedPID to (item 1 of argv) as integer
    set expectedBundle to item 2 of argv
    set ownerTTY to item 3 of argv
    set requestedID to item 4 of argv
    set operation to item 5 of argv
    set instances to current application's NSRunningApplication's runningApplicationsWithBundleIdentifier:expectedBundle
    if (instances's |count|() as integer) is not 1 then return "unavailable:terminal_app_ambiguous"
    set runningApp to current application's NSRunningApplication's runningApplicationWithProcessIdentifier:expectedPID
    if runningApp is missing value then return "unavailable:terminal_app_stale"
    if (runningApp's bundleIdentifier() as text) is not expectedBundle then return "unavailable:terminal_app_mismatch"
'''

_TERMINAL = _MAC_GUARD + '''
    tell application id "com.apple.Terminal"
        if not running then return "unavailable:terminal_app_stale"
        set matches to {}
        repeat with w in windows
            repeat with t in tabs of w
                if (tty of t as text) is ownerTTY then set end of matches to {w, t}
            end repeat
        end repeat
        if (count matches) is not 1 then return "unavailable:terminal_surface_not_unique"
        set targetWindow to item 1 of item 1 of matches
        set targetTab to item 2 of item 1 of matches
        set targetID to id of targetWindow as text
        if operation is "scan" then return "found:" & targetID
        if targetID is not requestedID then return "unavailable:terminal_surface_changed"
        set selected of targetTab to true
        set frontmost of targetWindow to true
        activate
        if selected of targetTab and frontmost then return "verified"
        return "dispatched"
    end tell
end run
'''

_ITERM = _MAC_GUARD + '''
    tell application id "com.googlecode.iterm2"
        if not running then return "unavailable:terminal_app_stale"
        set matches to {}
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (tty of s as text) is ownerTTY then set end of matches to {w, t, s}
                end repeat
            end repeat
        end repeat
        if (count matches) is not 1 then return "unavailable:terminal_surface_not_unique"
        set targetWindow to item 1 of item 1 of matches
        set targetTab to item 2 of item 1 of matches
        set targetSession to item 3 of item 1 of matches
        set targetID to unique id of targetSession as text
        if operation is "scan" then return "found:" & targetID
        if targetID is not requestedID then return "unavailable:terminal_surface_changed"
        tell targetSession to select
        tell targetTab to select
        tell targetWindow to select
        activate
        if (unique id of current session of current window as text) is targetID and frontmost then return "verified"
        return "dispatched"
    end tell
end run
'''

_GHOSTTY = _MAC_GUARD + '''
    tell application id "com.mitchellh.ghostty"
        if not running then return "unavailable:terminal_app_stale"
        set allTerminals to every terminal
        set matches to {}
        set ttyAPI to false
        repeat with t in allTerminals
            try
                set actualTTY to «class Gtty» of t as text
                set ttyAPI to true
                if actualTTY is ownerTTY then set end of matches to t
            end try
        end repeat
        set singleFallback to false
        if not ttyAPI and (count allTerminals) is 1 then
            set matches to allTerminals
            set singleFallback to true
        end if
        if (count matches) is not 1 then return "unavailable:ghostty_tty_api_or_unique_surface_required"
        set targetTerminal to item 1 of matches
        set targetID to id of targetTerminal as text
        if operation is "scan" then
            if singleFallback then return "single:" & targetID
            return "found:" & targetID
        end if
        if targetID is not requestedID then return "unavailable:terminal_surface_changed"
        if singleFallback and operation is not "single" then return "unavailable:terminal_surface_changed"
        focus targetTerminal
        if singleFallback then return "single_dispatched"
        if (id of focused terminal of selected tab of front window as text) is targetID and frontmost then return "verified"
        return "dispatched"
    end tell
end run
'''


def _mac_call(context, script, tty, target, operation):
    _live(context)
    if operation == 'single' and not _ancestor(context):
        raise Unavailable('ghostty_single_surface_owner_unverified')
    app = context.get('app')
    if not app:
        raise Unavailable('terminal_app_identity_required')
    return _run(['/usr/bin/osascript', '-', str(app['pid']), _BUNDLES[context['provider']], tty, target, operation], script=script)


def _native_mac(context, environment):
    if sys.platform != 'darwin':
        raise Unavailable('terminal_platform_unsupported')
    provider, tty = context['provider'], _tty(context['owner'].get('tty'))
    if tty is None:
        raise Unavailable('terminal_tty_required')
    script = {'terminal': _TERMINAL, 'iterm2': _ITERM, 'ghostty': _GHOSTTY}[provider]
    found = _mac_call(context, script, tty, '', 'scan')
    if not found.startswith(('found:', 'single:')):
        raise Unavailable(found.removeprefix('unavailable:'))
    method, target = found.split(':', 1)
    if not target or len(target) > 256 or any(c in target for c in '\r\n\0'):
        raise Unavailable('invalid_terminal_surface_id')
    if provider == 'iterm2' and environment.get('ITERM_SESSION_ID'):
        if environment['ITERM_SESSION_ID'].rsplit(':', 1)[-1] != target:
            raise Unavailable('terminal_native_identity_mismatch')
    if method == 'single' and not _ancestor(context):
        raise Unavailable('ghostty_single_surface_owner_unverified')
    answer = _mac_call(context, script, tty, target, 'single' if method == 'single' else 'focus')
    if answer not in ('verified', 'dispatched', 'single_dispatched'):
        raise Unavailable(answer.removeprefix('unavailable:'))
    return _result(provider, 'focused', 'ghostty_single_surface_fallback' if method == 'single' else 'native_surface_selected',
                   specificity='window' if method == 'single' else 'terminal_surface', verified=answer == 'verified')


def _json(command, environment):
    try:
        value = json.loads(_run(command, env=environment))
    except (ValueError, TypeError) as error:
        raise Unavailable('invalid_terminal_response') from error
    if not isinstance(value, list) or len(value) > 1024:
        raise Unavailable('invalid_terminal_response')
    return value


def _native_id(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,12}', value):
        return int(value)
    if type(value) is int and 0 <= value < 10**12:
        return value
    return None


def _activate_app(context):
    """Ask the already-running macOS app to activate, never use open/start."""
    app = context.get('app')
    if sys.platform != 'darwin' or not app:
        return False
    _live(context)
    script = _MAC_GUARD + '''
    runningApp's activateWithOptions:2
    return runningApp's isActive() as boolean
end run
'''
    return _mac_call(context, script, '', '', 'activate') == 'true'


def _wezterm(context, environment):
    executable = _cli('wezterm', context)
    prefix = [executable, 'cli', '--no-auto-start']
    rows = _json([*prefix, 'list', '--format', 'json'], environment)
    tty = _tty(context['owner'].get('tty'))
    native = _native_id(environment.get('WEZTERM_PANE'))
    if tty is None and (native is None or not context.get('app')):
        raise Unavailable('terminal_tty_or_native_pane_required')
    matches = [row for row in rows if isinstance(row, dict)
               and (native is None or row.get('pane_id') == native)
               and (tty is None or _tty(row.get('tty_name')) == tty)]
    if len(matches) != 1 or _native_id(matches[0].get('pane_id')) is None:
        raise Unavailable('terminal_surface_not_unique')
    target = matches[0]['pane_id']
    _live(context)
    _run([*prefix, 'activate-pane', '--pane-id', str(target)], env=environment)
    _activate_app(context)
    if sys.platform in ('win32', 'linux') and context.get('app'):
        from .window_focus import focus_window
        focus_window(context)
    current = _json([*prefix, 'list', '--format', 'json'], environment)
    selected = [row for row in current if isinstance(row, dict) and row.get('pane_id') == target
                and (tty is None or _tty(row.get('tty_name')) == tty) and row.get('is_active') is True]
    # is_active confirms a pane inside its tab, not which native OS window is
    # frontmost. App activation alone must not upgrade that to foreground proof.
    return _result('wezterm', 'focused', 'pane_selected_os_window_unverified' if len(selected) == 1
                   else 'focus_readback_unverified')


def _kitty(context, environment):
    if sys.platform not in ('darwin', 'linux'):
        raise Unavailable('terminal_platform_unsupported')
    address = environment.get('KITTY_LISTEN_ON', '')
    if not (address.startswith('unix:/') or address.startswith('unix:@')
            or re.fullmatch(r'tcp:(?:127\.0\.0\.1|\[::1\]):[0-9]{1,5}', address)):
        raise Unavailable('kitty_local_remote_control_socket_required')
    prefix = [_cli('kitty', context), '@', '--to', address]
    rows = _json([*prefix, 'ls'], environment)
    native, pid = _native_id(environment.get('KITTY_WINDOW_ID')), context['owner']['pid']
    windows = [window for os_window in rows if isinstance(os_window, dict)
               for tab in os_window.get('tabs', []) if isinstance(tab, dict)
               for window in tab.get('windows', []) if isinstance(window, dict)]
    matches = [window for window in windows if (native is None or window.get('id') == native)
               and (window.get('pid') == pid or any(p.get('pid') == pid
                    for p in window.get('foreground_processes', []) if isinstance(p, dict)))]
    if len(matches) != 1 or _native_id(matches[0].get('id')) is None:
        raise Unavailable('terminal_owner_surface_not_unique')
    target = matches[0]['id']
    _live(context)
    _run([*prefix, 'focus-window', '--match', f'id:{target}'], env=environment)
    activated = _activate_app(context)
    current = _json([*prefix, 'ls'], environment)
    focused = [window for os_window in current if isinstance(os_window, dict)
               for tab in os_window.get('tabs', []) if isinstance(tab, dict)
               for window in tab.get('windows', []) if isinstance(window, dict)
               and window.get('id') == target and window.get('is_focused') is True]
    return _result('kitty', 'focused', 'native_surface_selected' if focused else 'focus_readback_unverified',
                   verified=len(focused) == 1 and (activated or sys.platform == 'linux'))


def focus_terminal(context: dict) -> dict:
    provider = context.get('provider') if isinstance(context, dict) else None
    if not isinstance(provider, str) or provider not in PROVIDERS:
        return _result(provider, reason='terminal_provider_unsupported')
    try:
        _live(context)
        environment = _environment(context)
        if provider in ('terminal', 'iterm2', 'ghostty'):
            return _native_mac(context, environment)
        if provider == 'wezterm':
            return _wezterm(context, environment)
        return _kitty(context, environment)
    except Unavailable as error:
        return _result(provider, reason=str(error))
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        return _result(provider, 'failed', 'invalid_terminal_context_or_response')
