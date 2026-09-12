#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Project-local Codex hook probe. Metadata only; no broker, HID or UI calls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import tomllib

BEGIN = '# BEGIN Abralia Codex hook probe (managed)\n'
END = '# END Abralia Codex hook probe (managed)\n'
EVENTS = ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd')
MAX_INPUT = 1024 * 1024


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,256}', value) else None


def object_value(value):
    if isinstance(value, str) and len(value) < MAX_INPUT:
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def summarize(payload, project):
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in EVENTS:
        return None
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).resolve().is_relative_to(project.resolve()):
        return None
    result = {'schema_version': 1, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'hook_event_name': payload['hook_event_name']}
    for name in ('session_id', 'turn_id', 'tool_use_id', 'tool_name', 'agent_id', 'subagent_id'):
        value = identifier(payload.get(name))
        if value is not None:
            result[name] = value
    arguments = object_value(payload.get('tool_input'))
    if isinstance(arguments.get('questions'), list):
        result['question_count'] = len(arguments['questions'])
    response = object_value(payload.get('tool_response'))
    if type(response.get('accepted')) is bool:
        result['output_kind'] = 'accepted' if response['accepted'] else 'not_accepted'
    elif isinstance(response.get('answers'), dict):
        result['output_kind'] = 'answers_object'
    elif response:
        result['output_kind'] = 'other_object'
    if payload['hook_event_name'] == 'SessionStart' and payload.get('source') in ('startup', 'resume', 'clear', 'compact'):
        result['session_source'] = payload['source']
    return result


def append_record(output, row):
    output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False) + '\n').encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def record(project, output, raw):
    # Hook failures never block the user's original Codex operation.
    try:
        if len(raw) > MAX_INPUT:
            return
        row = summarize(json.loads(raw), project)
        if row:
            append_record(output, row)
    except (OSError, ValueError, TypeError):
        pass


def configuration_block(project, output, python):
    command = shlex.join([str(python), '-B', str(Path(__file__).resolve()), 'record',
                          '--project', str(project.resolve()), '--output', str(output.resolve())])
    lines = [BEGIN]
    for event in EVENTS:
        lines.append(f'[[hooks.{event}]]\n')
        if event in ('PreToolUse', 'PostToolUse'):
            lines.append('matcher = "*"\n')
        lines += [f'[[hooks.{event}.hooks]]\n', 'type = "command"\n',
                  f'command = {json.dumps(command, ensure_ascii=False)}\n', 'timeout = 2\n',
                  'statusMessage = "Abralia metadata-only hook probe"\n\n']
    return ''.join(lines) + END


def configure(project, output, python, *, remove=False):
    project = project.resolve()
    directory = project / '.codex'
    path = directory / 'config.toml'
    if not project.is_dir() or directory.is_symlink() or path.is_symlink():
        raise ValueError('existing project and regular configuration path required')
    original = path.read_text() if path.exists() else ''
    tomllib.loads(original)
    block = configuration_block(project, output, python)
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1 or block not in original:
            raise ValueError('probe block was edited; preserving it for manual review')
        updated = original.replace(block, '', 1) if remove else original
    elif remove:
        updated = original
    else:
        if original and not original.endswith('\n'):
            raise ValueError('config needs a trailing newline before installing the probe')
        updated = original + block
    tomllib.loads(updated)
    if updated != original:
        directory.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.abralia-hook-probe-', dir=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(updated)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {'configured': not remove, 'config': str(path), 'output': str(output.resolve()),
            'events': list(EVENTS), 'trust_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('record', 'install', 'remove', 'show'))
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    if args.action == 'record':
        record(args.project, args.output, sys.stdin.buffer.read(MAX_INPUT + 1))
        return 0
    if args.action == 'show':
        print(configuration_block(args.project, args.output, args.python), end='')
        return 0
    try:
        print(json.dumps(configure(args.project, args.output, args.python, remove=args.action == 'remove')))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
