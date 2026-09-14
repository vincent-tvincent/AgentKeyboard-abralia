# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Private project attention policy and bounded local service metadata files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


MAX_PRIVATE_JSON_BYTES = 16 * 1024


def default_state_dir() -> Path:
    """Private control data beneath Electron's ordinary Abralia user-data root."""
    return Path.home() / 'Library/Application Support/Abralia/control'


def _private_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private_metadata_directory_required')


def project_identity(project: str | Path) -> str:
    canonical = Path(project).resolve()
    return hashlib.blake2s(os.fsencode(canonical), digest_size=16).hexdigest()


def _private_regular(info, max_bytes=MAX_PRIVATE_JSON_BYTES):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > max_bytes):
        raise ValueError('unsafe_private_metadata_file')


def read_private_json(path: Path, *, max_bytes=MAX_PRIVATE_JSON_BYTES) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        _private_regular(os.fstat(stream.fileno()), max_bytes)
        encoded = stream.read(max_bytes + 1)
    if len(encoded) > max_bytes:
        raise ValueError('private_metadata_too_large')
    data = json.loads(encoded)
    if not isinstance(data, dict):
        raise ValueError('invalid_private_metadata')
    return data


def write_private_json(path: Path, data: dict, *, max_bytes=MAX_PRIVATE_JSON_BYTES) -> None:
    parent = path.parent
    if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
        raise ValueError('private_metadata_directory_required')
    if path.exists() or path.is_symlink():
        _private_regular(path.lstat(), max_bytes)
    encoded = (json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()
    if len(encoded) > max_bytes:
        raise ValueError('private_metadata_too_large')
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.abralia-policy-', dir=parent)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


class ProjectPolicy:
    """One service's project policy; never scan or write another project."""

    def __init__(self, project: str | Path, path: str | Path, *, state_dir: str | Path | None = None):
        self.project = str(Path(project).resolve())
        self.project_id = project_identity(project)
        self.path = Path(path)
        self.state_dir = Path(state_dir) if state_dir is not None else None

    def load(self) -> bool:
        data = read_private_json(self.path)
        if data is None:
            return False
        if (type(data.get('version')) is not int or data['version'] != 1
                or data.get('project_id') != self.project_id or data.get('project') != self.project
                or type(data.get('muted')) is not bool):
            raise ValueError('invalid_project_policy')
        return data['muted']

    def save(self, muted: bool) -> None:
        if type(muted) is not bool:
            raise ValueError('project_muted_must_be_boolean')
        if self.load() == muted and self.path.exists():
            return
        if self.state_dir is not None:
            # Create the application-owned root separately: mkdir(parents=True)
            # does not apply its requested mode to intermediate directories.
            _private_directory(self.state_dir)
            _private_directory(self.path.parent)
        write_private_json(self.path, {'version': 1, 'project_id': self.project_id,
                                      'project': self.project, 'muted': muted})
