# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Remove only recognized legacy Abralia project entries after plugin setup."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import tempfile
import tomllib
import uuid

from .project import BEGIN, END, _paths, _read
from .plugin_install import PluginInstallError, _directory, _write_text


HOOK_BEGIN = '# BEGIN Abralia Codex hook probe (managed)\n'
HOOK_END = '# END Abralia Codex hook probe (managed)\n'
HOOK_EVENTS = ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd')


@dataclass
class ProjectMigration:
    project: Path
    config: Path
    before: str
    after: str
    segments: tuple[str, ...]
    files: dict[Path, str]
    preserved: tuple[str, ...]

    def summary(self):
        return {'project': str(self.project), 'managed_blocks': len(self.segments),
                'removed_files': [str(path) for path in self.files],
                'preserved_user_files': list(self.preserved),
                'changed': bool(self.segments or self.files)}


def _hook_segment(content, root):
    if HOOK_BEGIN not in content and HOOK_END not in content:
        return None
    if content.count(HOOK_BEGIN) != 1 or content.count(HOOK_END) != 1:
        raise PluginInstallError('legacy_hook_block_conflict')
    start, end = content.index(HOOK_BEGIN), content.index(HOOK_END) + len(HOOK_END)
    if end <= start:
        raise PluginInstallError('legacy_hook_block_conflict')
    block = content[start:end]
    try:
        hooks = tomllib.loads(block)['hooks']
        if set(hooks) != set(HOOK_EVENTS):
            raise ValueError()
        commands = set()
        for event in HOOK_EVENTS:
            groups = hooks[event]
            expected = {'matcher', 'hooks'} if event in ('PreToolUse', 'PostToolUse') else {'hooks'}
            if len(groups) != 1 or set(groups[0]) != expected or groups[0].get('matcher', '*') != '*':
                raise ValueError()
            handlers = groups[0]['hooks']
            if len(handlers) != 1:
                raise ValueError()
            handler = handlers[0]
            if (set(handler) != {'type', 'command', 'timeout', 'statusMessage'} or handler['type'] != 'command'
                    or handler['timeout'] != 2 or handler['statusMessage'] != 'Abralia metadata-only hook probe'):
                raise ValueError()
            commands.add(handler['command'])
        if len(commands) != 1:
            raise ValueError()
        command = commands.pop()
        parts = shlex.split(command)
        if (len(parts) != 8 or parts[1] != '-B' or parts[3:5] != ['record', '--project']
                or parts[6] != '--output' or Path(parts[5]).resolve() != root
                or not Path(parts[0]).is_absolute() or not Path(parts[2]).is_absolute()
                or not Path(parts[7]).is_absolute()
                or not parts[2].endswith('/experiments/codex-question-hooks/probe.py')):
            raise ValueError()
        expected = HOOK_BEGIN
        for event in HOOK_EVENTS:
            expected += f'[[hooks.{event}]]\n'
            if event in ('PreToolUse', 'PostToolUse'):
                expected += 'matcher = "*"\n'
            expected += (f'[[hooks.{event}.hooks]]\n' + 'type = "command"\n'
                         + f'command = {json.dumps(command, ensure_ascii=False)}\n'
                         + 'timeout = 2\nstatusMessage = "Abralia metadata-only hook probe"\n\n')
        if block != expected + HOOK_END:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise PluginInstallError('legacy_hook_block_conflict') from error
    return block


def preview_project_migration(project):
    root, config, skill, manifest = _paths(project)
    before = _read(config)
    tomllib.loads(before)
    segments, files, preserved = [], {}, []
    state_text = _read(manifest)
    if state_text:
        try:
            state = json.loads(state_text)
            block = state['block']
            if (set(state) != {'version', 'project', 'socket', 'block', 'separator', 'skill', 'skill_created', 'config_created'}
                    or state['version'] != 1 or Path(state['project']).resolve() != root
                    or not block.startswith(BEGIN) or not block.endswith(END)
                    or before.count(block) != 1 or before.count(BEGIN) != 1 or before.count(END) != 1
                    or set(tomllib.loads(block)) != {'mcp_servers'}
                    or set(tomllib.loads(block)['mcp_servers']) != {'abralia_experiment'}):
                raise ValueError()
            segments.append(block)
            if state.get('skill_created') and skill.exists():
                if _read(skill) == state['skill']:
                    files[skill] = state['skill']
                else:
                    preserved.append(str(skill))
            elif skill.exists():
                preserved.append(str(skill))
            files[manifest] = state_text
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise PluginInstallError('legacy_mcp_block_conflict') from error
    elif BEGIN in before or END in before or 'abralia_experiment' in tomllib.loads(before).get('mcp_servers', {}):
        raise PluginInstallError('unmanaged_legacy_mcp_configuration')
    elif skill.exists():
        preserved.append(str(skill))
    hook = _hook_segment(before, root)
    if hook:
        segments.append(hook)
    after = before
    for segment in segments:
        after = after.replace(segment, '', 1)
    tomllib.loads(after)
    return ProjectMigration(root, config, before, after, tuple(segments), files, tuple(preserved))


def _atomic_config(path, text):
    # Project configuration can contain unrelated user settings. Preserve its
    # permissions and never require/chmod the user's existing .codex directory.
    if path.is_symlink():
        raise PluginInstallError('legacy_configuration_symlink')
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix='.abralia-migrate-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_project_migration(plan, backup_dir):
    if not isinstance(plan, ProjectMigration):
        raise PluginInstallError('migration_preview_required')
    _paths(plan.project)
    if _read(plan.config) != plan.before or any(_read(path) != text for path, text in plan.files.items()):
        raise PluginInstallError('project_changed_since_migration_preview')
    if not plan.segments and not plan.files:
        return {'status': 'unchanged', **plan.summary()}
    backup_dir = Path(backup_dir).absolute()
    _directory(backup_dir)
    backup = backup_dir / ('abralia-managed-' + uuid.uuid4().hex + '.json')
    # Only Abralia segments/files are persisted. Never back up the entire TOML.
    record = {'version': 1, 'project': str(plan.project), 'config_segments': list(plan.segments),
              'files': {str(path.relative_to(plan.project)): text for path, text in plan.files.items()}}
    _write_text(backup, json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    removed, changed_config = [], False
    try:
        if plan.segments:
            if _read(plan.config) != plan.before:
                raise PluginInstallError('project_changed_since_migration_preview')
            _atomic_config(plan.config, plan.after)
            changed_config = True
        for path, expected in plan.files.items():
            if _read(path) != expected:
                raise PluginInstallError('project_changed_during_migration')
            path.unlink()
            removed.append(path)
    except Exception as error:
        conflicts = []
        for path in removed:
            if path.exists() or path.is_symlink():
                conflicts.append(str(path))
            else:
                _atomic_config(path, plan.files[path])
        if changed_config:
            current = _read(plan.config)
            if current == plan.after:
                _atomic_config(plan.config, plan.before)
            else:
                try:
                    parsed = tomllib.loads(current)
                    if BEGIN in current or HOOK_BEGIN in current or 'abralia_experiment' in parsed.get('mcp_servers', {}):
                        raise ValueError()
                    restored = current + ('\n' if current and not current.endswith('\n') else '') + ''.join(plan.segments)
                    tomllib.loads(restored)
                    _atomic_config(plan.config, restored)
                except (ValueError, OSError):
                    conflicts.append(str(plan.config))
        if conflicts:
            raise PluginInstallError('migration_rollback_needs_review') from error
        raise
    return {'status': 'migrated', **plan.summary(), 'backup_path': str(backup)}
