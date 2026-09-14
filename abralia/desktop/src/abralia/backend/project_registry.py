# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Explicit, persistent project enrollment for a shared application backend."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import secrets

from .project_policy import (_private_directory, default_state_dir, project_identity,
                             read_private_json, write_private_json)

MAX_PROJECTS = 256
MAX_REGISTRY_BYTES = 256 * 1024


class ProjectRegistry:
    def __init__(self, state_dir=None):
        self.state_dir = Path(state_dir or os.environ.get('ABRALIA_STATE_DIR') or default_state_dir())
        self.path = self.state_dir / 'projects.json'

    def _load(self):
        if not self.state_dir.exists():
            return []
        info = self.state_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('project_registry_directory_not_private')
        value = read_private_json(self.path, max_bytes=MAX_REGISTRY_BYTES)
        if value is None:
            return []
        rows = value.get('projects')
        if value.get('version') != 1 or not isinstance(rows, list) or len(rows) > MAX_PROJECTS:
            raise ValueError('invalid_project_registry')
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('path'), str) or len(row['path']) > 4096:
                raise ValueError('invalid_registered_project')
            path = Path(row['path'])
            if not path.is_absolute() or str(path.resolve()) != row['path']:
                raise ValueError('registered_project_path_changed')
            identity = project_identity(path)
            if row.get('project_id') != identity or identity in seen or row.get('enabled') is not True:
                raise ValueError('invalid_registered_project')
            seen.add(identity)
            generation = row.get('generation')
            if not isinstance(generation, str) or len(generation) != 24 or any(c not in '0123456789abcdef' for c in generation):
                raise ValueError('invalid_project_generation')
            result.append({'project_id': identity, 'path': str(path), 'name': path.name or str(path),
                           'enabled': True, 'generation': generation})
        return sorted(result, key=lambda row: row['path'])

    @contextmanager
    def _locked(self):
        _private_directory(self.state_dir)
        fd = os.open(self.state_dir / '.projects.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('project_registry_lock_not_private')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def list(self):
        return self._load()

    def enroll(self, path):
        canonical = Path(path).resolve()
        if not canonical.is_dir():
            raise ValueError('project_directory_missing')
        row = {'project_id': project_identity(canonical), 'path': str(canonical),
               'name': canonical.name or str(canonical), 'enabled': True, 'generation': secrets.token_hex(12)}
        with self._locked():
            rows = self._load()
            found = next((r for r in rows if r['project_id'] == row['project_id']), None)
            if found:
                return found
            if len(rows) >= MAX_PROJECTS:
                raise ValueError('project_registry_capacity_exceeded')
            write_private_json(self.path, {'version': 1, 'projects': [*rows, row]}, max_bytes=MAX_REGISTRY_BYTES)
        return row

    def remove(self, path_or_id):
        if not isinstance(path_or_id, (str, Path)):
            raise ValueError('invalid_project')
        raw = str(path_or_id)
        identity = raw if len(raw) == 32 and all(c in '0123456789abcdef' for c in raw) else project_identity(raw)
        with self._locked():
            rows = self._load()
            kept = [r for r in rows if r['project_id'] != identity]
            if len(kept) == len(rows):
                return False
            write_private_json(self.path, {'version': 1, 'projects': kept}, max_bytes=MAX_REGISTRY_BYTES)
        return True

    disable = remove

    def resolve(self, cwd):
        if not isinstance(cwd, (str, Path)) or not Path(cwd).is_absolute():
            return None
        canonical = Path(cwd).resolve()
        matches = [row for row in self._load() if canonical.is_relative_to(Path(row['path']))]
        return max(matches, key=lambda row: len(Path(row['path']).parts)) if matches else None
