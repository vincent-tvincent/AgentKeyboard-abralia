# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Installed desktop helper and owned backend lifecycle over NDJSON stdio.

Hardware remains exclusively inside BrokerService's existing device worker.
Inventory and overview requests never start a service or open a HID controller.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import stat
import socket
import signal
import sys
import tempfile
import time

from .gui_devices import scan_devices, saved_device_selection
from .ipc import BrokerClient, socket_path
from .project_policy import project_identity, default_state_dir

MAX_MESSAGE = 65536


class GuiHostError(ValueError):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code.replace('_', ' '))


class GuiBrokerClient(BrokerClient):
    """Bound each local response so a stale service cannot freeze the app."""
    def _connect(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(.6)
        self.socket.connect(self.endpoint)
        self.reader = self.socket.makefile('rb')
        response = self._exchange({'type': 'hello', 'project': self.project, 'role': 'admin',
                                   'registered_thread_id': None})
        if response.get('status') != 'accepted':
            raise OSError(response.get('reason', 'registration rejected'))
        self.epoch = response['backend_epoch']

    def _exchange(self, message):
        # A user-requested switch includes old-device restoration and new-device
        # capability checks. Discovery stays fast; mutations get the broker's
        # existing bounded command window instead of timing out mid-operation.
        self.socket.settimeout(8. if message.get('action') in ('select_device', 'set_project_muted') else .6)
        return super()._exchange(message)


def default_runtime_dir():
    return Path(f'/tmp/abralia-{os.getuid()}')


def _private_directory(path: Path, *, create=False):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise GuiHostError('directory_not_private')


def _read_private_json(path: Path, *, limit=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GuiHostError('metadata_not_private')
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise GuiHostError('metadata_too_large')
    result = json.loads(value)
    if not isinstance(result, dict):
        raise GuiHostError('invalid_metadata')
    return result


def _atomic_private_json(path, value):
    _private_directory(path.parent, create=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, allow_nan=False, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GuiHost:
    def __init__(self, *, state_dir=None, runtime_dir=None, enumerator=None,
                 client_factory=GuiBrokerClient, managed_mode='hardware', service_factory=None,
                 shared_mode=False, plugin_bundle=None, runtime_executable=None, codex_executable=None):
        if managed_mode not in ('hardware', 'simulated'):
            raise ValueError('invalid_managed_backend_mode')
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()
        self.enumerator = enumerator
        self.client_factory = client_factory
        self.managed_mode = managed_mode
        self.service_factory = service_factory
        self.shared_mode = shared_mode
        self.plugin_bundle = Path(plugin_bundle) if plugin_bundle else None
        self.runtime_executable = runtime_executable or (sys.executable if getattr(sys, 'frozen', False) else None)
        self.codex_executable = codex_executable
        self._service_project = (self.state_dir / 'service').resolve()
        self._owned_service = None
        self._owned_descriptor = None
        self._external_descriptor = None
        self._internal_project = (self.state_dir / 'device-only').resolve()
        self._backend = {'state': 'stopped', 'owned': False, 'project_id': None,
                         'project_name': None, 'error': None, 'reason': None,
                         'mode': managed_mode, 'project_choices': []}
        self.selected = None
        self.startup_errors = []
        self._device_cache = None
        self._project_cache = {}
        self._project_futures = {}
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='abralia-gui-discovery')
        self._load_selection()

    def close(self):
        try:
            result = self.stop_backend()
            if result['backend'].get('reason') == 'backend_cleanup_unverified':
                raise GuiHostError('backend_cleanup_unverified', result['backend'].get('error'))
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _backend_snapshot(self, pairs=None):
        result = dict(self._backend)
        service = self._owned_service
        if service is not None:
            result['owned'] = True
            failure = getattr(service, 'worker_error', None)
            broker = getattr(service, 'broker', None)
            if not failure and broker is not None and getattr(broker, 'delivery', None) in ('failed', 'suspended'):
                failure = getattr(broker, 'device_error', None) or 'Keyboard output is suspended.'
            if failure:
                result.update(state='error', error=str(failure), reason='backend_device_error')
        elif self._external_descriptor is not None and pairs is not None:
            public = next((p for p, d in pairs if d['endpoint'] == self._external_descriptor['endpoint']), None)
            if public is None or not public['connected']:
                result.update(state='stopped', error='The external backend is no longer available.',
                              reason='external_backend_unavailable')
            elif public.get('device_error'):
                result.update(state='error', error=public['device_error'], reason='backend_device_error')
        return result

    def _start_error(self, code, message, *, choices=None, state='error'):
        self._backend.update(state=state, error=None if state == 'stopped' else message,
                             reason=code, project_choices=choices or [],
                             owned=self._owned_service is not None)
        raise GuiHostError(code, message)

    def _invalidate_projects(self):
        self._project_cache.clear()
        for future in self._project_futures.values():
            future.cancel()
        self._project_futures.clear()

    def _reuse_external(self, public, descriptor):
        internal = descriptor['project'] == str(self._internal_project)
        self._external_descriptor = descriptor
        self._backend.update(state='external', owned=False, project_id=None if internal else public['id'],
                             project_name='Keyboard service' if internal else public['name'],
                             error=public.get('device_error'), reason=None,
                             mode=public.get('mode') or self.managed_mode, project_choices=[])

    def _stop_for_restart(self):
        stopped = self.stop_backend()
        if stopped['backend'].get('reason') == 'backend_cleanup_unverified':
            self._start_error('backend_cleanup_unverified', stopped['backend'].get('error') or
                              'Previous keyboard cleanup could not be verified. No replacement backend was started.')

    @staticmethod
    def _profile_matches(actual, expected):
        return isinstance(actual, str) and actual.removeprefix('builtin:') == expected.removeprefix('builtin:')

    @staticmethod
    def _endpoint_listening(endpoint):
        """Check only local IPC; never unlink another service's socket."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            try:
                probe.connect(endpoint)
                return True
            except (FileNotFoundError, ConnectionRefusedError):
                return False
            except OSError:
                return None

    def start_backend(self, project_id=None):
        if project_id is not None and (not isinstance(project_id, str) or not project_id):
            raise GuiHostError('invalid_project_id')
        try:
            self.selected = saved_device_selection(self.state_dir)
        except (OSError, ValueError) as error:
            self._start_error('selection_unavailable', str(error))
        if self.selected is None:
            self._start_error('selection_required', 'Choose a keyboard to start Abralia.', state='stopped')
        devices, scan_errors = self._devices(force=True)
        selected = self._selected_view(devices)
        if scan_errors:
            self._start_error('device_scan_failed', scan_errors[0]['message'])
        if not selected or not selected['connected']:
            self._start_error('device_disconnected', 'Your selected keyboard is disconnected. Reconnect it or select another.')
        if not selected['selectable']:
            self._start_error('device_ambiguous', 'More than one connected keyboard shares the saved identity. No device was opened.')
        self.selected = selected
        if self.shared_mode:
            return self._start_shared_backend(selected)
        if self._owned_service is not None:
            if (self._profile_matches(self._owned_service.profile, selected['profile_id'])
                    and self._backend_snapshot()['state'] == 'ready'):
                if project_id is not None and project_id != self._owned_descriptor['project_id']:
                    self._start_error('backend_already_running', 'Stop the current backend before choosing another project.')
                driver = getattr(self._owned_service, 'driver', None)
                if self.managed_mode == 'hardware' and getattr(driver, 'selected_device_id', None) != selected['id']:
                    result = self._request(self._owned_descriptor, {'type': 'admin', 'action': 'select_device',
                        'device_id': selected['id'], 'profile_id': selected['profile_id'],
                        'fingerprint': selected['fingerprint'], 'expected_epoch': self._owned_service.broker.epoch})
                    if result.get('status') != 'accepted':
                        self._start_error('device_selection_failed', result.get('detail') or result.get('reason') or 'The keyboard selection was not applied.')
                return {'backend': self._backend_snapshot(), 'reused': True}
            if project_id is None and self._owned_descriptor['project'] != str(self._internal_project):
                project_id = self._owned_descriptor['project_id']
            self._stop_for_restart()
        pairs, discovery_errors = self._projects(force=True)
        if any(e['code'] == 'backend_discovery_failed' for e in discovery_errors):
            self._start_error('backend_discovery_failed', discovery_errors[0]['message'])
        live = [(p, d) for p, d in pairs if p['connected'] and (
            p.get('mode') == self.managed_mode or self.managed_mode == 'hardware'
            and p.get('mode') is None and p.get('delivery') != 'simulated')]
        compatible = [(p, d) for p, d in live if self._profile_matches(p.get('profile'), selected['profile_id'])]
        matching_device = [(p, d) for p, d in compatible if p.get('selected_device_id') == selected['id']]
        candidates = matching_device or compatible
        if project_id is not None:
            chosen_live = next(((p, d) for p, d in candidates if p['id'] == project_id), None)
            if chosen_live:
                candidates = [chosen_live]
            elif candidates:
                self._start_error('selected_device_in_use',
                                  'The selected keyboard already has a backend for another project. No project was switched.')
        if candidates:
            if len(candidates) != 1:
                choices = [{'id': p['id'], 'name': p['name'], 'path': p['path']} for p, _ in candidates]
                self._start_error('project_choice_required', 'Choose which running project Abralia should use.', choices=choices)
            public, descriptor = candidates[0]
            self._reuse_external(public, descriptor)
            if public['can_select_device'] and public.get('selected_device_id') != selected['id']:
                applied = self._request(descriptor, {'type': 'admin', 'action': 'select_device',
                    'device_id': selected['id'], 'profile_id': selected['profile_id'],
                    'fingerprint': selected['fingerprint'], 'expected_epoch': public['backend_epoch']})
                if applied.get('status') != 'accepted':
                    self._start_error('device_selection_failed', applied.get('detail') or applied.get('reason') or 'The existing backend could not apply this keyboard.')
            return {'backend': self._backend_snapshot(pairs), 'reused': True}
        if any(not p.get('profile') for p, _ in live):
            self._start_error('external_backend_unverified',
                              'An older backend is already running. Abralia will not open a competing keyboard connection.')
        # An unresponsive listener is not an absent owner. Only a refused or
        # missing endpoint can be considered offline for service startup.
        for public, descriptor in pairs:
            if not public['connected'] and self._endpoint_listening(descriptor['endpoint']) is not False:
                self._start_error('backend_busy', 'An existing backend is still responding slowly. Wait, then try Start again.')
        known = {}
        for public, descriptor in pairs:
            if descriptor['project'] == str(self._internal_project) or not Path(descriptor['project']).is_dir():
                continue
            known.setdefault(public['id'], (public, descriptor))
        if project_id is not None:
            if project_id not in known:
                self._start_error('project_unavailable', 'The chosen project is no longer available. Refresh the project list.')
            public, descriptor = known[project_id]
            if public['connected']:
                self._start_error('project_backend_conflict', 'This project already has a backend using a different device profile.')
        elif len(known) == 1:
            public, descriptor = next(iter(known.values()))
            if public['connected']:
                self._start_error('project_backend_conflict', 'This project already has a backend using a different device profile.')
        elif len(known) > 1:
            choices = [{'id': p['id'], 'name': p['name'], 'path': p['path']} for p, _ in known.values()]
            self._start_error('project_choice_required', 'Choose a project before starting the backend.', choices=choices)
        else:
            _private_directory(self.state_dir, create=True)
            _private_directory(self._internal_project, create=True)
            descriptor = {'project': str(self._internal_project), 'project_id': project_identity(self._internal_project),
                          'endpoint': str(self.runtime_dir / socket_path(self._internal_project).name), 'legacy': False}
            public = {'id': descriptor['project_id'], 'name': 'Keyboard service'}
        internal = descriptor['project'] == str(self._internal_project)
        self._external_descriptor = None
        self._backend.update(state='starting', owned=True, project_id=None if internal else public['id'],
                             project_name='Keyboard service' if internal else public['name'], error=None,
                             reason=None, mode=self.managed_mode, project_choices=[])
        factory = self.service_factory
        if factory is None:
            from .service import BrokerService
            factory = BrokerService
        try:
            service = factory(descriptor['project'], selected['profile_id'], mode=self.managed_mode,
                              endpoint=descriptor['endpoint'], state_dir=self.state_dir,
                              observe_codex=self.managed_mode == 'hardware' and not internal)
        except Exception as error:
            self._start_error('backend_start_failed', str(error))
        self._owned_service, self._owned_descriptor = service, descriptor
        try:
            service.start()
        except Exception as error:
            try:
                service.close()
            except Exception as cleanup_error:
                self._start_error('backend_cleanup_unverified', f'{error}; cleanup: {cleanup_error}')
            self._owned_service = self._owned_descriptor = None
            self._start_error('backend_start_failed', str(error))
        self._invalidate_projects()
        self._backend.update(state='ready', owned=True, error=None, reason=None)
        return {'backend': self._backend_snapshot(), 'reused': False}

    def _start_shared_backend(self, selected):
        if self._owned_service is not None:
            if (getattr(self._owned_service, 'shared', False)
                    and self._profile_matches(self._owned_service.profile, selected['profile_id'])
                    and self._backend_snapshot()['state'] == 'ready'):
                driver = getattr(self._owned_service, 'driver', None)
                if self.managed_mode == 'hardware' and getattr(driver, 'selected_device_id', None) != selected['id']:
                    result = self._request(self._owned_descriptor, {'type': 'admin', 'action': 'select_device',
                        'device_id': selected['id'], 'profile_id': selected['profile_id'],
                        'fingerprint': selected['fingerprint'], 'expected_epoch': self._owned_service.broker.epoch})
                    if result.get('status') != 'accepted':
                        self._start_error('device_selection_failed', result.get('detail') or result.get('reason'))
                return {'backend': self._backend_snapshot(), 'reused': True}
            self._stop_for_restart()
        pairs, errors = self._projects(force=True)
        if any(e['code'] == 'backend_discovery_failed' for e in errors):
            self._start_error('backend_discovery_failed', errors[0]['message'])
        endpoint = str(self.runtime_dir / 'shared.sock')
        peers = {d['endpoint']: (p, d) for p, d in pairs if p['connected'] and
                 (p.get('mode') == self.managed_mode or self.managed_mode == 'hardware' and p.get('mode') is None)}
        for public, descriptor in peers.values():
            if descriptor['endpoint'] == endpoint and public.get('shared'):
                if not self._profile_matches(public.get('profile'), selected['profile_id']):
                    self._start_error('device_profile_conflict', 'The running Abralia service uses a different keyboard. Quit that app before switching.')
                if self.managed_mode == 'hardware' and public.get('selected_device_id') != selected['id']:
                    if not public.get('can_select_device'):
                        self._start_error('device_selection_unavailable', 'The running backend cannot apply this keyboard selection. Restart it first.')
                    result = self._request(descriptor, {'type': 'admin', 'action': 'select_device',
                        'device_id': selected['id'], 'profile_id': selected['profile_id'],
                        'fingerprint': selected['fingerprint'], 'expected_epoch': public['backend_epoch']})
                    if result.get('status') != 'accepted':
                        self._start_error('device_selection_failed', result.get('detail') or result.get('reason'))
                self._reuse_external(public, descriptor)
                self._backend.update(project_id=None, project_name='Shared keyboard service')
                return {'backend': self._backend_snapshot(pairs), 'reused': True}
            if self._profile_matches(public.get('profile'), selected['profile_id']) or not public.get('profile'):
                self._start_error('legacy_backend_running', 'An earlier Abralia backend is still running. Quit it before opening this version.')
        if self._endpoint_listening(endpoint) is not False:
            self._start_error('backend_busy', 'The shared Abralia service is already starting or unavailable. Try again shortly.')
        _private_directory(self.state_dir, create=True)
        _private_directory(self._service_project, create=True)
        descriptor = {'project': str(self._service_project), 'project_id': project_identity(self._service_project),
                      'endpoint': endpoint, 'legacy': False, 'shared': True}
        factory = self.service_factory
        if factory is None:
            from .service import BrokerService
            factory = BrokerService
        self._external_descriptor = None
        self._backend.update(state='starting', owned=False, project_id=None,
                             project_name='Shared keyboard service', error=None, reason=None,
                             mode=self.managed_mode, project_choices=[])
        try:
            service = factory(descriptor['project'], selected['profile_id'], shared=True,
                              mode=self.managed_mode, endpoint=endpoint, state_dir=self.state_dir,
                              observe_codex=self.managed_mode == 'hardware')
        except Exception as error:
            self._start_error('backend_start_failed', str(error))
        self._owned_service, self._owned_descriptor = service, descriptor
        try:
            service.start()
        except Exception as error:
            try:
                self._stop_for_restart()
            except Exception as cleanup_error:
                self._start_error('backend_cleanup_unverified', f'{error}; cleanup: {cleanup_error}')
            self._start_error('backend_start_failed', str(error))
        self._invalidate_projects()
        self._backend.update(state='ready', owned=True, error=None, reason=None)
        return {'backend': self._backend_snapshot(), 'reused': False}

    def _registry(self):
        from .project_registry import ProjectRegistry
        return ProjectRegistry(self.state_dir)

    def _management_descriptor(self):
        if self._owned_descriptor is not None:
            return self._owned_descriptor
        if (self._external_descriptor is not None
                and self._endpoint_listening(self._external_descriptor['endpoint']) is False):
            self._external_descriptor = None
            self._backend.update(state='stopped', owned=False, project_id=None, project_name=None,
                                 error=None, reason=None)
        return self._external_descriptor

    def register_project(self, path):
        if not self.shared_mode:
            raise GuiHostError('shared_backend_required')
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_dir():
            raise GuiHostError('invalid_project', 'Choose an existing project folder.')
        descriptor = self._management_descriptor()
        if descriptor is not None:
            result = self._request(descriptor, {'type': 'admin', 'action': 'register_project',
                'path': path, 'expected_epoch': self._service_epoch(descriptor)})
            if result.get('status') != 'accepted':
                raise GuiHostError(result.get('reason', 'project_registration_failed'))
        else:
            self._registry().enroll(path)
        self._invalidate_projects()
        return self.overview()

    def _service_epoch(self, descriptor):
        if self._owned_service is not None and descriptor is self._owned_descriptor:
            return self._owned_service.broker.epoch
        result = self._request(descriptor, {'type': 'admin', 'action': 'status'})
        if result.get('status') != 'accepted':
            raise GuiHostError('backend_unavailable')
        return result['backend_epoch']

    def remove_project(self, project_id):
        if not self.shared_mode or not isinstance(project_id, str):
            raise GuiHostError('invalid_project_id')
        descriptor = self._management_descriptor()
        if descriptor:
            result = self._request(descriptor, {'type': 'admin', 'action': 'remove_project',
                'project_id': project_id, 'expected_epoch': self._service_epoch(descriptor)})
            if result.get('status') != 'accepted':
                raise GuiHostError(result.get('reason', 'project_removal_failed'))
        else:
            self._registry().remove(project_id)
        self._invalidate_projects()
        return self.overview()

    def integration_status(self):
        from .plugin_install import inspect_installation
        return inspect_installation(state_dir=self.state_dir, codex_executable=self.codex_executable)

    def install_integration(self):
        if self.plugin_bundle is None or self.runtime_executable is None:
            raise GuiHostError('packaged_app_required', 'Open the packaged Abralia app to install its bundled Codex plugin.')
        from .plugin_install import install_plugin
        return install_plugin(self.plugin_bundle, self.runtime_executable, state_dir=self.state_dir,
                              codex_executable=self.codex_executable)

    def remove_integration(self):
        from .plugin_install import uninstall_plugin
        return uninstall_plugin(state_dir=self.state_dir, codex_executable=self.codex_executable)

    def manual_hooks(self):
        if self.plugin_bundle is None:
            raise GuiHostError('plugin_bundle_unavailable')
        from .plugin_install import render_manual_hooks
        return {'text': render_manual_hooks(self.plugin_bundle, state_dir=self.state_dir)}

    def stop_backend(self):
        service = self._owned_service
        if service is None:
            return {'backend': self._backend_snapshot(), 'stopped': False}
        try:
            service.close()
        except Exception as error:
            self._backend.update(state='error', owned=True, error=str(error), reason='backend_cleanup_unverified')
            raise GuiHostError('backend_cleanup_unverified', str(error)) from error
        cleanup_error = getattr(service, 'cleanup_error', None)
        self._owned_service = self._owned_descriptor = None
        self._invalidate_projects()
        self._backend.update(state='stopped', owned=False, project_id=None, project_name=None,
                             error=cleanup_error, reason='backend_cleanup_unverified' if cleanup_error else None,
                             project_choices=[])
        return {'backend': self._backend_snapshot(), 'stopped': True}

    def _load_selection(self):
        try:
            self.selected = saved_device_selection(self.state_dir)
        except (OSError, ValueError) as error:
            self.startup_errors.append({'code': 'selection_unavailable', 'message': str(error)})

    def _devices(self, *, force=False):
        now = time.monotonic()
        if not force and self._device_cache and now - self._device_cache[0] < 2.:
            return self._device_cache[1:]
        try:
            result = scan_devices(enumerator=self.enumerator), []
        except (OSError, ValueError, RuntimeError) as error:
            result = [], [{'code': 'device_scan_failed', 'message': str(error)}]
        self._device_cache = (now, *result)
        return result

    def _selected_view(self, devices):
        if self.selected is None:
            return None
        matched = next((d for d in devices if d['id'] == self.selected['id']), None)
        return dict(matched) if matched else {**self.selected, 'connected': False,
                                             'selectable': False, 'selection_reason': 'device_disconnected'}

    def _descriptors(self):
        if not self.runtime_dir.exists():
            return [], []
        try:
            _private_directory(self.runtime_dir)
        except (OSError, ValueError) as error:
            return [], [{'code': 'backend_discovery_failed', 'message': str(error)}]
        descriptors, errors, covered = [], [], set()
        for path in sorted(self.runtime_dir.glob('*.info.json'))[:64]:
            try:
                metadata = _read_private_json(path)
                endpoint = path.with_name(path.name.removesuffix('.info.json') + '.sock')
                project = metadata.get('project')
                if (metadata.get('version') != 1 or not isinstance(project, str) or not Path(project).is_absolute()
                        or metadata.get('project_id') != project_identity(project)
                        or not isinstance(metadata.get('backend_epoch'), str)
                        or not isinstance(metadata.get('endpoint'), str)
                        or Path(metadata['endpoint']).resolve() != endpoint.resolve()):
                    raise GuiHostError('invalid_backend_descriptor')
                if endpoint.is_symlink():
                    raise GuiHostError('invalid_backend_endpoint')
                descriptors.append({**metadata, 'project': str(Path(project).resolve()),
                                    'endpoint': str(endpoint), 'legacy': False})
                covered.add(endpoint.name)
            except (OSError, ValueError) as error:
                errors.append({'code': 'backend_descriptor_skipped', 'message': str(error)})
        # Historical recovery files contain private tokens. Read only to derive
        # the project address; never forward or retain their allocation records.
        for path in sorted(self.runtime_dir.glob('*.slots.json'))[:64]:
            endpoint = path.with_name(path.name.removesuffix('.slots.json') + '.sock')
            if endpoint.name in covered or endpoint.is_symlink():
                continue
            try:
                metadata = _read_private_json(path, limit=1024 * 1024)
                project = metadata.get('project')
                if not isinstance(project, str) or not Path(project).is_absolute():
                    continue
                project = str(Path(project).resolve())
                descriptors.append({'project': project, 'project_id': project_identity(project),
                    'endpoint': str(endpoint), 'legacy': True})
            except (OSError, ValueError):
                continue
        return descriptors, errors

    def _request(self, descriptor, message):
        client = self.client_factory(descriptor['project'], endpoint=descriptor['endpoint'], role='admin')
        try:
            return client.request(message)
        finally:
            client.close()

    def _project(self, descriptor):
        try:
            result = self._request(descriptor, {'type': 'admin', 'action': 'status'})
        except (OSError, ValueError) as error:
            result = {'status': 'skipped', 'reason': 'backend_unavailable', 'detail': str(error)}
        connected = result.get('status') == 'accepted'
        if connected and not descriptor['legacy']:
            if (result.get('backend_epoch') != descriptor['backend_epoch']
                    or str(Path(result.get('project', '')).resolve()) != descriptor['project']):
                connected = False
                result = {'reason': 'backend_descriptor_changed'}
        capabilities = result.get('gui_capabilities', {}) if connected else {}
        rows = result.get('projects', []) if connected else []
        row = next((r for r in rows if isinstance(r, dict) and r.get('project_id') == descriptor['project_id']), {})
        slots = result.get('slots', []) if connected else []
        restart = connected and not capabilities.get('project_mute', False)
        public = {'id': descriptor['project_id'], 'path': descriptor['project'],
                  'name': row.get('name') or Path(descriptor['project']).name,
                  'muted': row.get('muted') if capabilities.get('project_mute') else None,
                  'task_count': row.get('task_count', len(slots)),
                  'connected_count': row.get('connected_count', sum(bool(s.get('agent_connected')) for s in slots)),
                  'connected': connected, 'backend_epoch': result.get('backend_epoch') if connected else None,
                  'delivery': result.get('delivery') if connected else None,
                  'mode': result.get('mode') if connected else None,
                  'device_error': result.get('device_error') if connected else None,
                  'profile': result.get('profile', descriptor.get('profile')),
                  'can_mute': bool(connected and capabilities.get('project_mute')),
                  'can_select_device': bool(connected and capabilities.get('device_selection')),
                  'selected_device_id': result.get('selected_device_id') if connected else None,
                  'restart_required': restart,
                  'status': 'restart_required' if restart else 'connected' if connected else 'unavailable',
                  'error': None if connected else result.get('reason', 'backend_unavailable')}
        if connected and result.get('shared'):
            public['shared'] = True
            public['_project_rows'] = [r for r in rows if isinstance(r, dict)
                and isinstance(r.get('path'), str) and Path(r['path']).is_absolute()
                and r.get('project_id') == project_identity(r['path'])]
        return public, descriptor

    def _projects(self, *, force=False):
        descriptors, errors = self._descriptors()
        live = {d['endpoint'] for d in descriptors}
        self._project_cache = {key: value for key, value in self._project_cache.items() if key in live}
        for key in list(self._project_futures):
            if key not in live:
                self._project_futures.pop(key).cancel()
        # Refresh existing records concurrently. Work may finish between UI
        # polls; do not submit another copy or wait once per stale socket.
        now = time.monotonic()
        for descriptor in descriptors:
            endpoint = descriptor['endpoint']
            cached = self._project_cache.get(endpoint)
            existing = self._project_futures.get(endpoint)
            if existing and existing.done():
                self._project_cache[endpoint] = (now, existing.result())
                self._project_futures.pop(endpoint)
                cached = self._project_cache[endpoint]
            if endpoint not in self._project_futures and (force or cached is None or now - cached[0] >= 2.):
                self._project_futures[endpoint] = self._pool.submit(self._project, descriptor)
        relevant = [self._project_futures[d['endpoint']] for d in descriptors
                    if d['endpoint'] in self._project_futures]
        if relevant:
            wait(relevant, timeout=1.5)
        pairs = []
        for descriptor in descriptors:
            endpoint = descriptor['endpoint']
            future = self._project_futures.get(endpoint)
            if future and future.done():
                self._project_cache[endpoint] = (time.monotonic(), future.result())
                self._project_futures.pop(endpoint)
                future = None
            cached = self._project_cache.get(endpoint)
            if cached is not None and not (force and future):
                pairs.append(cached[1])
            else:
                public = {'id': descriptor['project_id'], 'path': descriptor['project'],
                          'name': Path(descriptor['project']).name, 'muted': None,
                          'task_count': 0, 'connected_count': 0, 'connected': False,
                          'backend_epoch': None, 'profile': descriptor.get('profile'),
                          'can_mute': False, 'can_select_device': False, 'selected_device_id': None,
                          'restart_required': False, 'status': 'checking', 'error': None,
                          'delivery': None, 'device_error': None, 'mode': None}
                pairs.append((public, descriptor))
        expanded = []
        for public, descriptor in pairs:
            rows = public.get('_project_rows')
            if rows:
                for row in rows:
                    expanded.append(({**public, 'id': row['project_id'], 'path': row['path'],
                        'name': row.get('name') or Path(row['path']).name, 'muted': row.get('muted'),
                        'task_count': row.get('task_count', 0), 'connected_count': row.get('connected_count', 0),
                        'hooks_received': row.get('hooks_received', 0), 'last_hook_at': row.get('last_hook_at'),
                        'enrolled': True}, descriptor))
            else:
                expanded.append((public, descriptor))
        if self.shared_mode:
            present = {p['id'] for p, _ in expanded}
            for row in self._registry().list():
                if row['project_id'] not in present:
                    descriptor = {'project': str(self._service_project), 'project_id': project_identity(self._service_project),
                                  'endpoint': str(self.runtime_dir / 'shared.sock'), 'legacy': False, 'shared': True}
                    expanded.append(({'id': row['project_id'], 'path': row['path'], 'name': row['name'],
                        'muted': None, 'task_count': 0, 'connected_count': 0, 'connected': False,
                        'backend_epoch': None, 'can_mute': False, 'can_select_device': False,
                        'restart_required': False, 'shared': True, 'enrolled': True}, descriptor))
            unique = {}
            for public, descriptor in expanded:
                previous = unique.get(public['id'])
                if previous is None or public['connected'] and not previous[0]['connected']:
                    unique[public['id']] = (public, descriptor)
            expanded = list(unique.values())
        return expanded, errors

    def overview(self):
        devices, errors = self._devices()
        projects, project_errors = self._projects()
        result = {'devices': devices, 'selected_device': self._selected_view(devices),
                'projects': [{k:v for k,v in p.items() if not k.startswith('_')} for p, d in projects
                             if p['path'] not in (str(self._internal_project), str(self._service_project))],
                'backend': self._backend_snapshot(projects),
                'errors': [*self.startup_errors, *errors, *project_errors]}
        if self.shared_mode:
            result['shared'] = True
            result['integration'] = self.integration_status()
        return result

    def select_device(self, requested_id):
        devices, errors = self._devices(force=True)
        selected = next((d for d in devices if d['id'] == requested_id), None)
        if selected is None:
            raise GuiHostError('device_disconnected', 'The selected keyboard is no longer connected. Scan again.')
        if not selected['selectable']:
            raise GuiHostError('device_ambiguous', 'Multiple interfaces share this identity. No keyboard was selected.')
        _atomic_private_json(self.state_dir / 'device-selection.json', {'version': 1, 'selected_device': selected})
        self.selected = dict(selected)
        if self.shared_mode:
            try:
                started = self.start_backend()
                return {'selected_device': self._selected_view(devices), 'saved': True,
                        'backend': started['backend'], 'applied': [{'project_id': None, 'status': 'accepted'}], 'errors': errors}
            except GuiHostError as error:
                return {'selected_device': self._selected_view(devices), 'saved': True, 'applied': [],
                        'backend': self._backend_snapshot(), 'errors': [*errors, {'code': error.code, 'message': str(error)}]}
        pairs, discovery_errors = self._projects(force=True)
        compatible = [(p, d) for p, d in pairs if self.managed_mode == 'hardware' and p['connected'] and p['can_select_device']
                      and str(p['profile']).removeprefix('builtin:') == selected['profile_id'].removeprefix('builtin:')]
        applied = []
        if len(compatible) > 1:
            errors.append({'code': 'selection_requires_backend_choice',
                           'message': 'More than one backend could use this keyboard. No competing device owners were started.'})
        elif compatible:
            public, descriptor = compatible[0]
            if self._owned_service is not None and descriptor['endpoint'] != self._owned_descriptor['endpoint']:
                try:
                    self._stop_for_restart()
                    started = self.start_backend()
                    applied.append({'project_id': started['backend']['project_id'], 'status': 'accepted',
                                    'reason': 'external_backend_reused' if started['reused'] else 'backend_started'})
                except GuiHostError as error:
                    errors.append({'code': error.code, 'message': str(error)})
                return {'selected_device': self._selected_view(devices), 'applied': applied,
                        'saved': True, 'backend': self._backend_snapshot(), 'errors': [*errors, *discovery_errors]}
            result = self._request(descriptor, {'type': 'admin', 'action': 'select_device',
                'device_id': selected['id'], 'profile_id': selected['profile_id'],
                'fingerprint': selected['fingerprint'], 'expected_epoch': public['backend_epoch']})
            applied.append({'project_id': public['id'], 'status': result.get('status'),
                            'reason': result.get('reason'), 'detail': result.get('detail')})
            if result.get('status') == 'accepted':
                if self._owned_service is None:
                    self._reuse_external(public, descriptor)
                else:
                    self._backend.update(state='ready', error=None, reason=None)
        else:
            try:
                started = self.start_backend()
                applied.append({'project_id': started['backend']['project_id'], 'status': 'accepted',
                                'reason': 'external_backend_reused' if started['reused'] else 'backend_started'})
            except GuiHostError as error:
                errors.append({'code': error.code, 'message': str(error)})
        return {'selected_device': self._selected_view(devices), 'applied': applied,
                'saved': True, 'backend': self._backend_snapshot(), 'errors': [*errors, *discovery_errors]}

    def set_project_muted(self, project_id, muted, expected_epoch):
        if type(muted) is not bool or not isinstance(expected_epoch, str) or not expected_epoch:
            raise GuiHostError('invalid_project_mute')
        pairs, _ = self._projects(force=True)
        found = next(((p, d) for p, d in pairs if p['id'] == project_id), None)
        if found is None:
            raise GuiHostError('project_unavailable')
        public, descriptor = found
        if not public['can_mute']:
            raise GuiHostError('backend_restart_required' if public['restart_required'] else 'backend_unavailable')
        if public['backend_epoch'] != expected_epoch:
            raise GuiHostError('backend_changed', 'The backend restarted. Refresh its state before changing it.')
        result = self._request(descriptor, {'type': 'admin', 'action': 'set_project_muted',
                                           'project_id': project_id, 'muted': muted,
                                           'expected_epoch': expected_epoch})
        if result.get('status') != 'accepted':
            raise GuiHostError(result.get('reason', 'project_mute_failed'))
        self._project_cache.pop(descriptor['endpoint'], None)
        # The serialized mutation ACK already contains the persisted policy and
        # exact project/epoch. A second status read can lose the connection or
        # observe another change; it must not turn a successful mute into an
        # inferred unmute through ``muted=None``. Normal overview polling will
        # discover later availability/state independently.
        rows = result.get('projects')
        acknowledged = next((row for row in rows if isinstance(row, dict)
                             and row.get('project_id') == project_id), None) if isinstance(rows, list) else None
        if (result.get('backend_epoch') != expected_epoch or acknowledged is None
                or type(acknowledged.get('muted')) is not bool or acknowledged['muted'] != muted
                or not isinstance(acknowledged.get('path'), str)
                or str(Path(acknowledged['path']).resolve()) != public['path']):
            raise GuiHostError('project_mute_unconfirmed',
                               'The backend did not confirm this project mute setting. Refresh its state.')
        confirmed = {**public, 'muted': acknowledged['muted']}
        for key in ('name', 'task_count', 'connected_count'):
            if key in acknowledged:
                confirmed[key] = acknowledged[key]
        return {'project': confirmed, 'confirmation': 'acknowledged', 'acknowledged_muted': acknowledged['muted']}

    def dispatch(self, command, args):
        if not isinstance(args, dict):
            raise GuiHostError('invalid_arguments')
        if command == 'overview':
            return self.overview()
        if command == 'scan_devices':
            devices, errors = self._devices(force=True)
            return {'devices': devices, 'selected_device': self._selected_view(devices), 'errors': errors}
        if command == 'select_device':
            return self.select_device(args.get('device_id'))
        if command == 'set_project_muted':
            return self.set_project_muted(args.get('project_id'), args.get('muted'), args.get('expected_epoch'))
        if command == 'start_backend':
            return self.start_backend(args.get('project_id'))
        if command == 'stop_backend':
            return self.stop_backend()
        if command == 'register_project':
            return self.register_project(args.get('path'))
        if command == 'remove_project':
            return self.remove_project(args.get('project_id'))
        if command == 'integration_status':
            return self.integration_status()
        if command == 'install_integration':
            return self.install_integration()
        if command == 'remove_integration':
            return self.remove_integration()
        if command == 'manual_hooks':
            return self.manual_hooks()
        raise GuiHostError('unknown_command')


def serve(host, input_stream, output_stream):
    while True:
        line = input_stream.readline(MAX_MESSAGE + 1)
        if not line:
            return
        identity = None
        try:
            if len(line) > MAX_MESSAGE or not line.endswith('\n'):
                while not line.endswith('\n'):
                    line = input_stream.readline(MAX_MESSAGE + 1)
                    if not line:
                        break
                raise GuiHostError('invalid_message_size')
            request = json.loads(line)
            if not isinstance(request, dict):
                raise GuiHostError('invalid_request')
            identity = request.get('id')
            if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                identity = None
                raise GuiHostError('invalid_request_id')
            result = host.dispatch(request.get('command'), request.get('args', {}))
            response = {'id': identity, 'ok': True, 'result': result}
        except (OSError, ValueError, TypeError, KeyError) as error:
            response = {'id': identity, 'ok': False,
                        'error': {'code': getattr(error, 'code', 'request_failed'), 'message': str(error)}}
        output_stream.write(json.dumps(response, allow_nan=False) + '\n')
        output_stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--shared', action='store_true')
    parser.add_argument('--plugin-bundle', type=Path)
    parser.add_argument('--runtime-executable')
    parser.add_argument('--codex-executable')
    parser.add_argument('--managed-mode', choices=('hardware', 'simulated'), default='hardware',
                        help='Use simulated only for an explicit GUI lifecycle test; it never opens HID.')
    args = parser.parse_args(argv)
    host = GuiHost(state_dir=args.state_dir, runtime_dir=args.runtime_dir, managed_mode=args.managed_mode,
                   shared_mode=args.shared, plugin_bundle=args.plugin_bundle,
                   runtime_executable=args.runtime_executable, codex_executable=args.codex_executable)
    def terminate(_signum, _frame):
        raise SystemExit(0)
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        serve(host, sys.stdin, sys.stdout)
    finally:
        try:
            host.close()
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
