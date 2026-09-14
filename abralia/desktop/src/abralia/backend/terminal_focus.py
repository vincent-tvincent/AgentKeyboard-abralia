# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Bounded native focus helper. Runs outside the keyboard's HID worker."""

from __future__ import annotations

import json
import sys


def focus(context):
    from .client_lifetime import normalize_terminal_context
    context = normalize_terminal_context(context, verify=True)
    if context is None:
        return {'status': 'unavailable', 'reason': 'terminal_client_not_live'}
    if context['provider'] in ('vscode', 'windows_terminal'):
        from .window_focus import focus_window
        return focus_window(context)
    from .terminal_adapters import focus_terminal
    return focus_terminal(context)


def public_result(value):
    """Never forward subprocess output, paths, pane IDs or terminal text."""
    if not isinstance(value, dict) or value.get('status') not in ('focused', 'unavailable', 'failed'):
        return {'status': 'failed', 'reason': 'invalid_focus_result'}
    return {key: value[key] for key in ('status', 'provider', 'specificity', 'verification', 'reason')
            if isinstance(value.get(key), str) and len(value[key]) <= 160}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('oversized_context')
        result = public_result(focus(json.loads(raw)))
    except Exception:
        # Native diagnostics may contain titles, paths or unrelated app text.
        result = {'status': 'failed', 'reason': 'native_focus_failed'}
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
