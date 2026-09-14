# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Resolve native Codex task context without reading prompt/response contents."""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

MAX_META = 128 * 1024


def _source_kind(source):
    if source in ('cli', 'vscode'):
        return source
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except (ValueError, RecursionError):
            return 'unknown'
    if source in ('cli', 'vscode'):
        return source
    if isinstance(source, dict) and any(str(key).lower().replace('_', '') == 'subagent' for key in source):
        return 'subagent'
    return 'unknown'


def native_task_source(thread_id, *, codex_home=None):
    """Distinguish native root tasks from subagents without guessing from labels."""
    try:
        thread_id = str(UUID(thread_id))
        home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        databases = sorted(((int(m[1]), p) for p in home.glob('state_*.sqlite')
                            if (m := re.fullmatch(r'state_(\d+)\.sqlite', p.name))), reverse=True)
        if databases:
            with closing(sqlite3.connect(databases[0][1].resolve().as_uri()+'?mode=ro', uri=True, timeout=.15)) as connection:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
                if not {'id', 'source'} <= columns:
                    return 'unknown'
                archived = 'archived' if 'archived' in columns else '0'
                row = connection.execute(f'SELECT source,{archived} FROM threads WHERE id=?', (thread_id,)).fetchone()
                if row:
                    return 'unknown' if row[1] else _source_kind(row[0])
        paths = list((home/'sessions').glob(f'*/*/*/*-{thread_id}.jsonl'))
        if len(paths) != 1:
            return 'unknown'
        with paths[0].open('rb') as stream:
            line = stream.readline(MAX_META + 1)
        if len(line) > MAX_META:
            return 'unknown'
        record = json.loads(line)
        meta = record.get('payload', {})
        if record.get('type') != 'session_meta' or meta.get('id') != thread_id:
            return 'unknown'
        return _source_kind(meta.get('source'))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, sqlite3.Error):
        return 'unknown'


def native_task_cwd(thread_id, *, codex_home=None):
    """Fail closed when native context is absent, archived, or contradictory.

    Plugin MCP starts in its installation directory, not the user's project.
    The current native index is preferred. A first-record session_meta fallback
    covers a task whose index has not been written yet; it never scans its chat.
    """
    thread_id = str(UUID(thread_id))
    home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    databases = sorted(((int(m[1]), p) for p in home.glob('state_*.sqlite')
                        if (m := re.fullmatch(r'state_(\d+)\.sqlite', p.name))), reverse=True)
    if databases:
        try:
            with closing(sqlite3.connect(databases[0][1].resolve().as_uri() + '?mode=ro', uri=True,
                                         timeout=.15)) as connection:
                columns = {r[1] for r in connection.execute('PRAGMA table_info(threads)')}
                if not {'id', 'cwd'} <= columns:
                    raise ValueError('unsupported_codex_task_index')
                archived = 'archived' if 'archived' in columns else '0'
                row = connection.execute(f'SELECT cwd,{archived} FROM threads WHERE id=?', (thread_id,)).fetchone()
                if row:
                    if row[1] or not isinstance(row[0], str) or not Path(row[0]).is_absolute():
                        raise ValueError('native_project_unavailable')
                    return Path(row[0]).resolve()
        except sqlite3.Error:
            # An unreadable current index is uncertainty, not permission to use
            # an old transcript whose working directory may have changed.
            raise ValueError('codex_task_index_unreadable') from None
    matches = list((home / 'sessions').glob(f'*/*/*/*-{thread_id}.jsonl'))
    if len(matches) != 1:
        raise ValueError('native_project_unavailable')
    try:
        with matches[0].open('rb') as stream:
            raw = stream.readline(MAX_META + 1)
        if len(raw) > MAX_META:
            raise ValueError('native_project_unavailable')
        record = json.loads(raw)
        meta = record.get('payload', {})
        cwd = meta.get('cwd')
        if (record.get('type') != 'session_meta' or meta.get('id') != thread_id
                or not isinstance(cwd, str) or not Path(cwd).is_absolute()):
            raise ValueError('native_project_unavailable')
        return Path(cwd).resolve()
    except (OSError, TypeError, AttributeError, json.JSONDecodeError, RecursionError):
        raise ValueError('native_project_unavailable') from None
