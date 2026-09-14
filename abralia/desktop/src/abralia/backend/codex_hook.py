# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Abralia Codex lifecycle receiver: bounded metadata-only IPC, no decisions."""

import argparse
import json
from pathlib import Path
import re
import sys
from uuid import UUID

from .ipc import BrokerClient, shared_socket_path
from .project_registry import ProjectRegistry

EVENTS = ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd')
MAX_INPUT = 1024 * 1024


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,256}', value) else None


def object_value(value):
    if isinstance(value, str) and len(value) <= MAX_INPUT:
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def summarize(payload, registry):
    """Whitelist only routing/lifecycle facts, discarding prompts and answers."""
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in EVENTS:
        return None
    if payload.get('agent_id') or payload.get('subagent_id'):
        # Some Codex hooks identify a child only through its parent's session.
        # Do not turn child activity into a parent task's question or status.
        return None
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return None
    project = registry.resolve(cwd)
    if project is None:
        return None
    try:
        task_id = str(UUID(payload.get('session_id')))
    except (ValueError, TypeError, AttributeError):
        return None
    event = {'type': 'hook_event', 'event': payload['hook_event_name'],
             'thread_id': task_id, 'cwd': str(Path(cwd).resolve())}
    for name in ('turn_id', 'tool_use_id', 'event_id'):
        value = identifier(payload.get(name))
        if value is not None:
            event[name] = value
    tool = identifier(payload.get('tool_name'))
    if tool:
        # Native function transport prefixes are not part of the tool identity.
        match = re.search(r'(?:^|[._])(?P<tool>request_user_input(?:_async)?)$', tool)
        event['tool_name'] = match['tool'] if match else tool
    arguments = object_value(payload.get('tool_input'))
    if isinstance(arguments.get('questions'), list):
        event['question_count'] = len(arguments['questions'])
    response = object_value(payload.get('tool_response'))
    if payload.get('is_error') is True or response.get('isError') is True:
        event['output_kind'] = 'failed'
    elif type(response.get('accepted')) is bool:
        event['output_kind'] = 'accepted' if response['accepted'] else 'failed'
    elif isinstance(response.get('answers'), dict):
        event['output_kind'] = 'answers' if response['answers'] else 'returned_empty'
    return project, event


def forward(raw, *, registry=None, endpoint=None):
    """Never start hardware, change trust, block a tool, or print hook output."""
    try:
        if len(raw) > MAX_INPUT:
            return {'status': 'skipped', 'reason': 'input_too_large'}
        selected = summarize(json.loads(raw), registry or ProjectRegistry())
        if selected is None:
            return {'status': 'skipped', 'reason': 'event_not_applicable'}
        project, event = selected
        with BrokerClient(project['path'], endpoint=endpoint or shared_socket_path(),
                          role='hook', timeout=.35, project_generation=project.get('generation')) as client:
            return client.request(event)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {'status': 'skipped', 'reason': 'invalid_or_unavailable'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', help='isolated local test endpoint')
    args = parser.parse_args(argv)
    try:
        forward(sys.stdin.buffer.read(MAX_INPUT + 1), endpoint=args.socket)
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
