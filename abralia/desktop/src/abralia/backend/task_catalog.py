# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read only user-task metadata from the local Codex index; never read prompts."""

from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

MAX_TASK_BATCH = 256


def requested_task_ids(values, all_project=False):
    if type(all_project) is not bool or (all_project and values is not None):
        raise ValueError('choose_task_ids_or_all')
    if all_project:
        return None
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_TASK_BATCH:
        raise ValueError('task_ids_required_or_batch_too_large')
    if any(not isinstance(value, str) for value in values):
        raise ValueError('invalid_task_id')
    return list(dict.fromkeys(str(UUID(value)) for value in values))


def codex_project_tasks(project, *, codex_home=None, thread_ids=None):
    root = Path(project).resolve()
    home = Path(codex_home or os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    candidates = [(int(match[1]), path) for path in home.glob('state_*.sqlite')
                  if (match := re.fullmatch(r'state_(\d+)\.sqlite', path.name))]
    if not candidates:
        raise ValueError('codex_task_index_unavailable')
    database = max(candidates)[1].resolve()
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
            if not {'id', 'cwd', 'source', 'archived'} <= columns:
                raise ValueError('unsupported_codex_task_index')
            # `title` may contain a whole initial prompt, including internal
            # review transcripts. Read only the actual display-name column.
            name_column = 'name' if 'name' in columns else 'NULL'
            prefix = str(root) + os.sep
            selected = (' AND id IN (' + ','.join('?' for _ in thread_ids) + ')') if thread_ids else ''
            rows = connection.execute(
                f'SELECT id, {name_column}, cwd FROM threads '
                'WHERE archived=0 AND source IN (\'cli\',\'vscode\') '
                'AND (cwd=? OR substr(cwd,1,?)=?)' + selected + ' ORDER BY id LIMIT ?',
                (str(root), len(prefix), prefix, *(thread_ids or []), MAX_TASK_BATCH + 1)).fetchall()
    except sqlite3.Error as error:
        raise ValueError('codex_task_index_unreadable') from error
    tasks = []
    for task_id, name, cwd in rows:
        if not Path(cwd).resolve().is_relative_to(root):
            continue
        task_id = str(UUID(task_id))
        label = ' '.join(name.split())[:80] if isinstance(name, str) and name.strip() else 'Codex task ' + task_id[:8]
        tasks.append({'thread_id': task_id, 'label': label})
    if thread_ids is not None:
        indexed = {task['thread_id']: task for task in tasks}
        if any(task_id not in indexed for task_id in thread_ids):
            raise ValueError('task_not_in_project_or_not_user_task')
        tasks = [indexed[task_id] for task_id in thread_ids]
    if len(tasks) > MAX_TASK_BATCH:
        raise ValueError('project_task_batch_too_large')
    return tasks
