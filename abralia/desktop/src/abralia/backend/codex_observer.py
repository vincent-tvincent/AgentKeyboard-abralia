# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Incremental metadata observation outside the keyboard's device worker."""

from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import shlex
import threading
import tomllib
from uuid import UUID

from .codex_rollout import RolloutObservation

MAX_LINE = 8 * 1024 * 1024


class JsonlCursor:
    def __init__(self, path):
        self.path = Path(path)
        self.identity = None
        self.offset = 0

    def poll(self, *, budget=8 * 1024 * 1024):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        reset = identity != self.identity or stat.st_size < self.offset
        if reset:
            self.offset = 0
            self.identity = identity
        rows, used = [], 0
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            while used < budget:
                position = stream.tell()
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise ValueError('oversized Codex observation record')
                if not line.endswith(b'\n'):
                    stream.seek(position)
                    break
                self.offset = stream.tell()
                used += len(line)
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows, reset, self.offset >= stat.st_size


class RolloutTail:
    def __init__(self, path, project, thread_id):
        self.cursor = JsonlCursor(path)
        self.project = Path(project).resolve()
        self.thread_id = str(UUID(thread_id))
        self.state = RolloutObservation(self.thread_id)
        self.validated = False
        self.baselined = False
        self.completed_turn_ids = OrderedDict()

    def poll(self):
        rows, reset, caught_up = self.cursor.poll()
        if reset:
            self.state = RolloutObservation(self.thread_id)
            self.validated = False
            self.baselined = False
            self.completed_turn_ids.clear()
        for row in rows:
            if not self.validated:
                meta = row.get('payload', {})
                cwd = meta.get('cwd') if isinstance(meta, dict) else None
                if (row.get('type') != 'session_meta' or not isinstance(meta, dict) or meta.get('id') != self.thread_id or
                        not isinstance(cwd, str) or not Path(cwd).resolve().is_relative_to(self.project)):
                    raise ValueError('Codex observation session/project mismatch')
                self.validated = True
                continue
            payload = row.get('payload')
            completed = (self.baselined and row.get('type') == 'event_msg'
                         and isinstance(payload, dict) and payload.get('type') == 'task_complete'
                         and self.state.execution == 'running' and self.state.turn_id is not None
                         and payload.get('turn_id') == self.state.turn_id)
            self.state.feed(row)
            if completed:
                self.completed_turn_ids[self.state.turn_id] = None
                while len(self.completed_turn_ids) > 128:
                    self.completed_turn_ids.popitem(last=False)
        if not self.validated or not caught_up:
            return None
        # The first fully read snapshot establishes a baseline, including after
        # replacement/truncation. Only subsequently appended completions ring.
        # Keep recent IDs in snapshots so a failed broker delivery can retry.
        self.baselined = True
        return {**self.state.snapshot(), 'completed_turn_ids': list(self.completed_turn_ids)}


def configured_hook_journal(project):
    """Read our own probe's output argument; never execute configuration text."""
    path = Path(project) / '.codex/config.toml'
    try:
        config = tomllib.loads(path.read_text())
        outputs = set()
        for groups in config.get('hooks', {}).values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                for handler in group.get('hooks', []):
                    if handler.get('statusMessage') != 'Abralia metadata-only hook probe':
                        continue
                    parts = shlex.split(handler.get('command', ''))
                    if ('--project' in parts and
                            Path(parts[parts.index('--project') + 1]).resolve() == Path(project).resolve()
                            and '--output' in parts):
                        outputs.add(str(Path(parts[parts.index('--output') + 1]).resolve()))
        return Path(outputs.pop()) if len(outputs) == 1 else None
    except (OSError, ValueError, TypeError, IndexError, AttributeError):
        return None


class CodexObserver:
    """Only observes already allocated Codex sessions; never allocates a slot."""

    def __init__(self, service, *, codex_home=None, hook_journal=None, interval=.5):
        self.service = service
        self.home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        self.project = Path(service.project)
        journal = Path(hook_journal) if hook_journal else (None if getattr(service, 'shared', False) else configured_hook_journal(self.project))
        self.hook_cursor = JsonlCursor(journal) if journal else None
        self.hooks = OrderedDict()
        self.tails = {}
        self.retry_at = {}
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None

    def _request(self, kind, **fields):
        return self.service.submit({'connection':'codex-observer', 'role':'observer',
                                    'message':{'type':kind, **fields}})

    def _read_hooks(self):
        if self.hook_cursor is None:
            return
        try:
            rows, reset, _ = self.hook_cursor.poll()
            if reset:
                self.hooks.clear()
            for row in rows:
                if row.get('schema_version') != 1:
                    continue
                thread_id = row.get('session_id')
                try:
                    thread_id = str(UUID(thread_id))
                except (ValueError, TypeError, AttributeError):
                    continue
                # Parent-only metadata cannot identify independent child sessions.
                if row.get('agent_id') or row.get('subagent_id'):
                    continue
                event = row.get('hook_event_name')
                if event not in ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd'):
                    continue
                self.hooks[thread_id] = {key:row[key] for key in
                    ('recorded_at','hook_event_name','turn_id','tool_name','tool_use_id')
                    if isinstance(row.get(key), str) and len(row[key]) <= 256}
                self.hooks.move_to_end(thread_id)
                while len(self.hooks) > 1024:
                    self.hooks.popitem(last=False)
        except (OSError, ValueError):
            pass  # A missing journal does not prevent rollout observation.

    def tick(self):
        import time
        self._read_hooks()
        result = self._request('codex_observation_targets')
        if result.get('status') != 'accepted':
            return
        targets = result['targets']
        live = {target['token'] for target in targets}
        self.tails = {token:tail for token,tail in self.tails.items() if token in live}
        self.retry_at = {token:when for token,when in self.retry_at.items() if token in live}
        for target in targets:
            if self.stop_event.is_set():
                return
            token, thread_id = target['token'], target['thread_id']
            snapshot = None
            error = None
            try:
                if token not in self.tails and time.monotonic() >= self.retry_at.get(token, 0):
                    paths = list((self.home/'sessions').glob(f'*/*/*/rollout-*-{thread_id}.jsonl'))
                    if paths:
                        path = max(paths, key=lambda p:p.stat().st_mtime_ns)
                        self.tails[token] = RolloutTail(path, target.get('project', self.project), thread_id)
                    self.retry_at[token] = time.monotonic() + 5
                tail = self.tails.get(token)
                snapshot = tail.poll() if tail else None
                if tail and snapshot is None:
                    continue  # Do not replay intermediate historical questions during bootstrap.
            except (OSError, ValueError, TypeError):
                error = 'rollout_unavailable_or_invalid'
                self.tails.pop(token, None)
            observation = {'thread_id':thread_id, 'source':'codex_observer',
                           'execution':'unknown', 'turn_id':None, 'mode':None, 'questions':[],
                           'last_hook':self.hooks.get(thread_id), 'error':error}
            if snapshot:
                observation.update({key:snapshot[key] for key in ('execution','turn_id','mode','last_event_at')})
                observation['completed_turn_ids'] = snapshot['completed_turn_ids']
                questions = {q['request_id']:q for q in snapshot['recent_questions']}
                questions.update({q['request_id']:q for q in snapshot['pending_questions']})
                observation['questions'] = list(questions.values())
                observation['question_evidence'] = 'tool/reply records; native visibility/expiry unverified'
            self._request('codex_observation', token=token, observation=observation)

    def start(self):
        def run():
            while not self.stop_event.is_set() and not self.service.stop_event.is_set():
                try:
                    self.tick()
                    self.error = None
                except Exception:
                    self.error = 'observer_failed'  # Never expose a private source record in an error.
                self.stop_event.wait(self.interval)
        self.thread = threading.Thread(target=run, name='abralia-codex-observer', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=7)
            if self.thread.is_alive():
                raise RuntimeError('Codex observer did not stop')
