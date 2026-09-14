# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Explicit Codex plugin deployment through its supported CLI, never config edits."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

from .project_policy import default_state_dir, read_private_json, write_private_json


PLUGIN_ID = 'abralia@abralia'
LAUNCHER_MARKER = '# Abralia managed runtime launcher v1\n'
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
_DEADLINE = ContextVar('abralia_plugin_install_deadline', default=None)


@contextmanager
def _budget(seconds):
    token = _DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _timeout(maximum):
    deadline = _DEADLINE.get()
    remaining = maximum if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise PluginInstallError('plugin_operation_timed_out')
    return min(maximum, remaining)


class PluginInstallError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _paths(state_dir=None):
    state = Path(state_dir) if state_dir is not None else default_state_dir()
    state = state.absolute()
    root = state / 'integrations/codex'
    return {'state': state, 'root': root, 'manifest': root / 'installation.json',
            'marketplace': root / 'marketplace', 'runtimes': state.parent / 'runtimes',
            'launcher': state.parent / 'bin/abralia-host'}


def _directory(path):
    if path.is_symlink():
        raise PluginInstallError('unsafe_installation_directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PluginInstallError('installation_directory_not_private')


def _prepare(paths):
    for path in (paths['state'], paths['state'] / 'integrations', paths['root'],
                 paths['runtimes'], paths['launcher'].parent):
        _directory(path)


@contextmanager
def _locked(paths):
    _prepare(paths)
    fd = os.open(paths['root'] / 'install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _text(path):
    if path.is_symlink():
        raise PluginInstallError('unsafe_installation_file')
    return path.read_text() if path.exists() else None


def _write_text(path, text, mode=0o600):
    _directory(path.parent)
    if path.is_symlink():
        raise PluginInstallError('unsafe_installation_file')
    fd, temporary = tempfile.mkstemp(prefix='.abralia-install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _tree_digest(root, *, max_bytes):
    root = root.resolve()
    digest, size, count = hashlib.blake2s(), 0, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted([*dirs, *files]):
            path = Path(directory) / name
            relative = path.relative_to(root)
            digest.update(os.fsencode(relative) + b'\0')
            count += 1
            if count > 20000:
                raise PluginInstallError('installation_tree_too_large')
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if not path.resolve().is_relative_to(root) or os.path.isabs(os.readlink(path)):
                    raise PluginInstallError('installation_symlink_escapes_runtime')
                digest.update(b'L' + os.fsencode(os.readlink(path)))
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b'D')
            elif stat.S_ISREG(info.st_mode):
                size += info.st_size
                if size > max_bytes:
                    raise PluginInstallError('installation_tree_too_large')
                digest.update(b'F' + str(info.st_mode & 0o111).encode())
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        _timeout(20)
                        digest.update(block)
            else:
                raise PluginInstallError('unsupported_installation_file')
    return digest.hexdigest()


def _relative_file(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise PluginInstallError('invalid_plugin_manifest_path')
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()) or not target.exists():
        raise PluginInstallError('invalid_plugin_manifest_path')
    return target


def validate_bundle(bundle_root):
    root = Path(bundle_root).resolve()
    try:
        marketplace = json.loads((root / '.agents/plugins/marketplace.json').read_text())
        plugin_root = root / 'plugins/abralia'
        plugin = json.loads((plugin_root / '.codex-plugin/plugin.json').read_text())
        entries = marketplace['plugins']
        if marketplace['name'] != 'abralia' or len(entries) != 1 or entries[0]['name'] != 'abralia':
            raise PluginInstallError('invalid_abralia_marketplace')
        source = entries[0]['source']
        source_path = source.get('path') if isinstance(source, dict) else source
        if _relative_file(root, source_path) != plugin_root.resolve():
            raise PluginInstallError('invalid_abralia_marketplace')
        if plugin['name'] != 'abralia' or not isinstance(plugin['version'], str) or not plugin['version']:
            raise PluginInstallError('invalid_abralia_plugin')
        for key in ('mcpServers', 'hooks', 'skills'):
            value = plugin.get(key)
            if isinstance(value, str):
                _relative_file(plugin_root, value)
            elif isinstance(value, list):
                for item in value:
                    _relative_file(plugin_root, item)
            elif value is not None:
                raise PluginInstallError('invalid_plugin_manifest_path')
        return {'version': plugin['version'], 'digest': _tree_digest(root, max_bytes=MAX_BUNDLE_BYTES)}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise PluginInstallError('invalid_plugin_bundle') from error


def find_codex(executable=None):
    candidates = ([str(executable)] if executable else [shutil.which('codex'),
        '/Applications/Codex.app/Contents/Resources/codex',
        '/Applications/ChatGPT.app/Contents/Resources/codex'])
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).absolute())
    raise PluginInstallError('codex_executable_unavailable')


def _codex(executable, args, cwd):
    try:
        result = subprocess.run([executable, 'plugin', *args], cwd=cwd,
                                capture_output=True, text=True, timeout=_timeout(20))
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise PluginInstallError('codex_plugin_command_failed')
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except subprocess.TimeoutExpired as error:
        raise PluginInstallError('codex_plugin_command_timed_out') from error
    except (OSError, ValueError) as error:
        if isinstance(error, PluginInstallError):
            raise
        raise PluginInstallError('codex_plugin_response_unavailable') from error


def _configured(executable, paths):
    result = _codex(executable, ['marketplace', 'list', '--json'], paths['state'])
    marketplaces = result.get('marketplaces')
    if not isinstance(marketplaces, list):
        raise PluginInstallError('unsupported_codex_plugin_response')
    matches = [entry for entry in marketplaces if entry.get('name') == 'abralia']
    if len(matches) > 1:
        raise PluginInstallError('marketplace_name_conflict')
    marketplace = matches[0] if matches else None
    if marketplace and Path(marketplace.get('root', '')).resolve() != paths['marketplace'].resolve():
        raise PluginInstallError('marketplace_name_conflict')
    installed = None
    if marketplace:
        result = _codex(executable, ['list', '--marketplace', 'abralia', '--json'], paths['state'])
        entries = result.get('installed')
        if not isinstance(entries, list):
            raise PluginInstallError('unsupported_codex_plugin_response')
        installed = next((entry for entry in entries if entry.get('pluginId') == PLUGIN_ID), None)
    return marketplace, installed


def _load_manifest(paths):
    data = read_private_json(paths['manifest'])
    if data is not None and (data.get('version') != 1 or data.get('plugin_id') != PLUGIN_ID
                            or data.get('marketplace_root') != str(paths['marketplace'])):
        raise PluginInstallError('installation_manifest_conflict')
    return data


def inspect_installation(state_dir=None, codex_executable=None, *, verify=False):
    paths = _paths(state_dir)
    result = {'installed': False, 'configured': False, 'version': None, 'source': str(paths['marketplace']),
              'runtime_available': False, 'trust': 'review_required_or_unknown',
              'restart_required': False, 'error': None, 'verification': 'local_receipt'}
    try:
        manifest = _load_manifest(paths)
        if manifest:
            result.update(installed=manifest.get('installed', False), configured=manifest.get('installed', False),
                          version=manifest.get('plugin_version'), restart_required=manifest.get('installed', False))
            runtime = Path(manifest.get('runtime_executable', ''))
            result['runtime_available'] = runtime.is_file() and os.access(runtime, os.X_OK) and _text(paths['launcher']) == manifest.get('launcher_text')
        if verify:
            executable = find_codex(codex_executable)
            _, installed = _configured(executable, paths)
            result.update(installed=bool(installed), configured=bool(installed), verification='codex_cli')
            if installed:
                result['version'] = installed.get('version')
    except (OSError, ValueError) as error:
        result['error'] = getattr(error, 'code', 'installation_state_unavailable')
    return result


def _smoke_runtime(executable):
    try:
        result = subprocess.run([str(executable), 'mcp', '--help'], capture_output=True, timeout=_timeout(15),
                                cwd=executable.parent)
        if result.returncode:
            raise PluginInstallError('packaged_runtime_validation_failed')
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PluginInstallError('packaged_runtime_validation_failed') from error


def stage_runtime(runtime_executable, state_dir=None):
    """Copy the complete frozen onedir distribution before activating its launcher."""
    paths = _paths(state_dir)
    _prepare(paths)
    executable = Path(runtime_executable).absolute()
    if executable.name not in ('abralia-gui-host', 'abralia-host') or not os.access(executable, os.X_OK):
        raise PluginInstallError('packaged_runtime_required')
    source = executable.parent
    if not (source / '_internal').is_dir():
        raise PluginInstallError('complete_packaged_runtime_required')
    digest = _tree_digest(source, max_bytes=512 * 1024 * 1024)
    destination = paths['runtimes'] / digest
    if destination.exists():
        if destination.is_symlink() or _tree_digest(destination, max_bytes=512 * 1024 * 1024) != digest:
            raise PluginInstallError('managed_runtime_edited')
    else:
        temporary = paths['runtimes'] / ('.stage-' + uuid.uuid4().hex)
        try:
            shutil.copytree(source, temporary, symlinks=True)
            if _tree_digest(temporary, max_bytes=512 * 1024 * 1024) != digest:
                raise PluginInstallError('runtime_changed_during_copy')
            _smoke_runtime(temporary / executable.name)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return {'runtime_executable': str(destination / executable.name), 'runtime_digest': digest}


def _update_runtime_only(paths, old, old_launcher, runtime, launcher):
    """Retarget future launches without reinstalling the unchanged Codex plugin.

    Already-running MCP bridges retain their process, runtime and recovery proof.
    Both runtime generations remain available; this changes no Codex files.
    """
    if _text(paths['launcher']) != old_launcher or _load_manifest(paths) != old:
        raise PluginInstallError('installation_changed_during_runtime_update')
    receipt = {**old, **runtime, 'launcher_text': launcher}
    try:
        _write_text(paths['launcher'], launcher, 0o700)
        write_private_json(paths['manifest'], receipt)
    except Exception:
        # Restore only values still owned by this update; never erase edits
        # made by another process while a write was in flight.
        try:
            current = _load_manifest(paths)
            if current == receipt:
                write_private_json(paths['manifest'], old)
            elif current != old:
                raise PluginInstallError('runtime_update_rollback_incomplete')
            current_launcher = _text(paths['launcher'])
            if current_launcher == launcher:
                if old_launcher is None:
                    paths['launcher'].unlink(missing_ok=True)
                else:
                    _write_text(paths['launcher'], old_launcher, 0o700)
            elif current_launcher != old_launcher:
                raise PluginInstallError('runtime_update_rollback_incomplete')
        except (OSError, ValueError) as error:
            raise PluginInstallError('runtime_update_rollback_incomplete') from error
        raise


def install_plugin(bundle_root, runtime_executable, state_dir=None, codex_executable=None,
                   migrate_project=None, migration_backup_dir=None):
    paths = _paths(state_dir)
    bundle = validate_bundle(bundle_root)
    executable = find_codex(codex_executable)
    if migrate_project is not None and migration_backup_dir is None:
        raise PluginInstallError('migration_backup_directory_required')
    migration_plan = preview_project_migration(migrate_project) if migrate_project is not None else None
    with _budget(85), _locked(paths):
        old = _load_manifest(paths)
        old_launcher = _text(paths['launcher'])
        if old_launcher is not None and (not old or old_launcher != old.get('launcher_text')):
            raise PluginInstallError('managed_launcher_conflict')
        marketplace, installed = _configured(executable, paths)
        if paths['marketplace'].exists() and (not old or validate_bundle(paths['marketplace'])['digest'] != old.get('bundle_digest')):
            raise PluginInstallError('managed_marketplace_edited')
        runtime = stage_runtime(runtime_executable, paths['state'])
        launcher = '#!/bin/sh\n' + LAUNCHER_MARKER + 'exec ' + shlex.quote(runtime['runtime_executable']) + ' "$@"\n'
        plugin_current = (old and old.get('installed') and installed and installed.get('enabled') is True
                          and paths['marketplace'].is_dir() and old.get('bundle_digest') == bundle['digest']
                          and old.get('plugin_version') == bundle['version']
                          and installed.get('version') == bundle['version'])
        if plugin_current:
            runtime_changed = old.get('runtime_digest') != runtime['runtime_digest'] or old_launcher != launcher
            if runtime_changed:
                _update_runtime_only(paths, old, old_launcher, runtime, launcher)
            migration = apply_project_migration(migration_plan, migration_backup_dir) if migration_plan is not None else None
            return {**inspect_installation(paths['state']), 'status': 'configured', 'changed': runtime_changed,
                    'runtime_changed': runtime_changed, 'plugin_changed': False,
                    'running_clients_unchanged': True, 'restart_required': False, 'migration': migration}
        staged = paths['root'] / ('.marketplace-' + uuid.uuid4().hex)
        backup = paths['root'] / ('.previous-' + uuid.uuid4().hex)
        new_marketplace = marketplace is None
        plugin_attempted = False
        marketplace_attempted = False
        replaced = False
        completed = False
        try:
            shutil.copytree(Path(bundle_root).resolve(), staged, symlinks=True)
            if validate_bundle(staged) != bundle:
                raise PluginInstallError('plugin_bundle_changed_during_copy')
            if paths['marketplace'].exists():
                os.replace(paths['marketplace'], backup)
            os.replace(staged, paths['marketplace'])
            replaced = True
            _write_text(paths['launcher'], launcher, 0o700)
            if new_marketplace:
                marketplace_attempted = True
                _codex(executable, ['marketplace', 'add', str(paths['marketplace']), '--json'], paths['state'])
            plugin_attempted = True
            result = _codex(executable, ['add', PLUGIN_ID, '--json'], paths['state'])
            if result.get('pluginId') != PLUGIN_ID or result.get('version') != bundle['version']:
                raise PluginInstallError('plugin_installation_unconfirmed')
            receipt = {'version': 1, 'plugin_id': PLUGIN_ID, 'installed': True,
                       'plugin_version': bundle['version'], 'bundle_digest': bundle['digest'],
                       'marketplace_root': str(paths['marketplace']), 'launcher_text': launcher, **runtime}
            write_private_json(paths['manifest'], receipt)
            completed = True
        except Exception:
            # Keep the complete failure path under the GUI's 120-second window:
            # 85 seconds for install plus 25 seconds reserved for compensation.
            _DEADLINE.set(time.monotonic() + 25)
            if replaced:
                if validate_bundle(paths['marketplace'])['digest'] != bundle['digest']:
                    raise PluginInstallError('plugin_installation_rollback_incomplete')
                shutil.rmtree(paths['marketplace'])
            if backup.exists() and not paths['marketplace'].exists():
                os.replace(backup, paths['marketplace'])
            current_launcher = _text(paths['launcher'])
            if current_launcher not in (old_launcher, launcher):
                raise PluginInstallError('plugin_installation_rollback_incomplete')
            if current_launcher == launcher:
                if old_launcher is None:
                    paths['launcher'].unlink(missing_ok=True)
                else:
                    _write_text(paths['launcher'], old_launcher, 0o700)
            # Compensating operations are restricted to this exact plugin/source.
            try:
                if plugin_attempted and installed and old:
                    _codex(executable, ['add', PLUGIN_ID, '--json'], paths['state'])
                elif plugin_attempted:
                    _codex(executable, ['remove', PLUGIN_ID, '--json'], paths['state'])
                if marketplace_attempted:
                    _codex(executable, ['marketplace', 'remove', 'abralia', '--json'], paths['state'])
            except PluginInstallError as error:
                raise PluginInstallError('plugin_installation_rollback_incomplete') from error
            raise
        finally:
            for directory in (staged, *([backup] if completed else [])):
                if directory.exists():
                    shutil.rmtree(directory)
        migration = None
        if migration_plan is not None:
            migration = apply_project_migration(migration_plan, migration_backup_dir)
        return {**inspect_installation(paths['state']), 'status': 'configured', 'changed': True,
                'migration': migration}


def uninstall_plugin(state_dir=None, codex_executable=None):
    paths = _paths(state_dir)
    executable = find_codex(codex_executable)
    with _budget(85), _locked(paths):
        receipt = _load_manifest(paths)
        if receipt is None:
            return {**inspect_installation(paths['state']), 'status': 'not_managed', 'changed': False}
        marketplace, installed = _configured(executable, paths)
        if installed:
            _codex(executable, ['remove', PLUGIN_ID, '--json'], paths['state'])
        if marketplace:
            _codex(executable, ['marketplace', 'remove', 'abralia', '--json'], paths['state'])
        if _text(paths['launcher']) == receipt.get('launcher_text'):
            paths['launcher'].unlink(missing_ok=True)
        receipt['installed'] = False
        write_private_json(paths['manifest'], receipt)
        return {**inspect_installation(paths['state']), 'status': 'removed',
                'changed': bool(installed or marketplace), 'runtime_retained': True,
                'restart_required': True}


def render_manual_hooks(bundle_root, state_dir=None):
    """Read-only fallback; copy this instead of enabling the plugin hooks."""
    validate_bundle(bundle_root)
    plugin = Path(bundle_root).resolve() / 'plugins/abralia'
    manifest = json.loads((plugin / '.codex-plugin/plugin.json').read_text())
    hook_file = _relative_file(plugin, manifest.get('hooks', './hooks/hooks.json'))
    try:
        hooks = json.loads(hook_file.read_text())['hooks']
        command = shlex.join([str(_paths(state_dir)['launcher']), 'codex-hook'])
        lines = ['# Abralia manual hooks: alternative to the plugin; do not enable both.\n']
        for event, groups in hooks.items():
            if event not in ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd'):
                raise ValueError()
            for group in groups:
                lines.append(f'[[hooks.{event}]]\n')
                if 'matcher' in group:
                    lines.append('matcher = ' + json.dumps(group['matcher'], ensure_ascii=False) + '\n')
                for handler in group['hooks']:
                    if handler['type'] != 'command' or not handler['statusMessage'].startswith('Abralia'):
                        raise ValueError()
                    lines += [f'[[hooks.{event}.hooks]]\n', 'type = "command"\n',
                              'command = ' + json.dumps(command, ensure_ascii=False) + '\n',
                              'timeout = ' + json.dumps(handler['timeout']) + '\n',
                              'statusMessage = ' + json.dumps(handler['statusMessage'], ensure_ascii=False) + '\n\n']
        return ''.join(lines)
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as error:
        raise PluginInstallError('invalid_plugin_hooks') from error


def preview_project_migration(project):
    from .plugin_migration import preview_project_migration as preview
    return preview(project)


def apply_project_migration(plan, backup_dir):
    from .plugin_migration import apply_project_migration as apply
    return apply(plan, backup_dir)
