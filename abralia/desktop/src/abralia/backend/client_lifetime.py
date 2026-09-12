# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read-only lifetime evidence; no other process's arguments or environment are read."""

import os
from pathlib import Path
import subprocess


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        return None
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid=,ppid=,lstart=,tty=,comm='],
                                capture_output=True, text=True, timeout=1,
                                env={**os.environ, 'LC_ALL':'C', 'TZ':'UTC'})
        parts = result.stdout.strip().split(None, 8)
        if result.returncode or len(parts) != 9 or int(parts[0]) != pid:
            return None
        return {'pid':pid, 'parent':int(parts[1]), 'started':' '.join(parts[2:7]),
                'tty':parts[7], 'executable':parts[8]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def capture_client_owner(start_pid=None):
    pid = os.getppid() if start_pid is None else start_pid
    seen, terminal = set(), None
    for _ in range(24):
        if pid in seen:
            break
        seen.add(pid)
        identity = process_identity(pid)
        if identity is None:
            break
        executable = identity['executable']
        name = Path(executable).name.lower()
        if '/Contents/MacOS/' in executable and name in ('codex', 'chatgpt'):
            owner = {**identity, 'kind':'codex_gui'}
            if terminal:
                owner['client_process'] = terminal
            return owner
        if name == 'codex' and identity['tty'] not in ('??', '?', '-'):
            terminal = terminal or {**identity, 'kind':'codex_tui'}
        pid = identity['parent']
    return terminal


def owner_alive(owner, _depth=0):
    if not isinstance(owner, dict) or owner.get('kind') not in ('codex_gui','codex_tui'):
        return False
    current = process_identity(owner.get('pid'))
    if current is None or any(current.get(k) != owner.get(k) for k in ('started','executable','tty')):
        return False
    name = Path(current['executable']).name.lower()
    if owner['kind'] == 'codex_gui':
        valid = '/Contents/MacOS/' in current['executable'] and name in ('codex','chatgpt')
        nested = owner.get('client_process')
        if nested is not None:
            return bool(valid and _depth == 0 and isinstance(nested, dict)
                        and nested.get('kind') == 'codex_tui' and 'client_process' not in nested
                        and owner_alive(nested, 1))
        return valid
    return name == 'codex' and current['tty'] not in ('??','?','-')
