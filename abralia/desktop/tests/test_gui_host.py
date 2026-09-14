# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from abralia.backend.core import Broker
from abralia.backend.device import DeviceDriver
from abralia.backend.gui_devices import (SUPPORTED_PROFILES, DeviceSelectionError, device_fingerprint,
    device_id, resolve_device, saved_device_selection, scan_devices)
from abralia.backend.gui_host import GuiHost, GuiHostError, MAX_MESSAGE, serve
from abralia.backend.project_policy import project_identity
from abralia.device_profile import load_device_profile
from abralia.rgb.transport import HidDeviceInfo


PROFILE = SUPPORTED_PROFILES[0]


def keyboard(path=b'keyboard-a', serial='serial-a'):
    return HidDeviceInfo(path, 0x3434, 0x0F30, 0xFF60, 0x61, 1,
                         'Keychron V3 8K', 'Keychron', serial)


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class DeviceIdentityTests(unittest.TestCase):
    def test_serial_identity_survives_path_change_but_path_fallback_does_not(self):
        a = keyboard()
        b = replace(a, path=b'new-address')
        self.assertEqual(device_id(device_fingerprint(a)), device_id(device_fingerprint(b)))
        self.assertNotEqual(device_id(device_fingerprint(replace(a, serial_number=''))),
                            device_id(device_fingerprint(replace(b, serial_number=''))))
        profile = load_device_profile(PROFILE)
        self.assertIs(resolve_device(device_fingerprint(a), profile, devices=[b]), b)
        with self.assertRaisesRegex(DeviceSelectionError, 'disconnected'):
            resolve_device(device_fingerprint(replace(a, serial_number='')), profile,
                           devices=[replace(b, serial_number='')])

    def test_duplicate_identity_is_ambiguous_and_descriptor_matching_is_exact(self):
        a, b = keyboard(), keyboard(b'keyboard-b')
        rows = scan_devices(enumerator=lambda: [a, b, replace(a, usage=1), replace(a, vendor_id=0)])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['selectable'])
        self.assertEqual(rows[0]['ambiguous_count'], 2)
        with self.assertRaisesRegex(DeviceSelectionError, 'ambiguous'):
            resolve_device(device_fingerprint(a), load_device_profile(PROFILE), devices=[a, b])

    def test_all_three_profiles_are_readable_and_scan_does_not_open_hid(self):
        descriptors = []
        for i, name in enumerate(SUPPORTED_PROFILES):
            profile = load_device_profile(name)
            match = profile.device_match
            descriptors.append(replace(keyboard(path=f'board-{i}'.encode(), serial=f'serial-{i}'),
                vendor_id=match.vendor_id, product_id=match.product_id,
                usage_page=match.usage_page, usage=match.usage))
        with patch('abralia.shared_hid.SharedRawHidSession.open_path', side_effect=AssertionError('no owner')):
            rows = scan_devices(enumerator=lambda: descriptors)
        self.assertEqual({d['profile_id'] for d in rows}, set(SUPPORTED_PROFILES))
        self.assertTrue(all(d['firmware_verified'] is False for d in rows))


class GuiHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.state = self.root / 'state'
        self.devices = [keyboard()]
        self.calls = []
        self.backends = {}
        def factory(project, *, endpoint, role):
            parent = self
            class Client:
                def request(client, message):
                    parent.calls.append((project, message))
                    backend = parent.backends.get(endpoint)
                    if backend is None:
                        return {'status': 'skipped', 'reason': 'backend_unavailable'}
                    if message['action'] == 'set_project_muted':
                        backend['projects'][0]['muted'] = message['muted']
                    return {'status': 'accepted', **backend}
                def close(client):
                    pass
            self.assertEqual(role, 'admin')
            return Client()
        self.factory = factory
        self.host = self.new_host()

    def new_host(self):
        host = GuiHost(state_dir=self.state, runtime_dir=self.runtime,
                       enumerator=lambda: self.devices, client_factory=self.factory)
        self.addCleanup(host.close)
        return host

    def backend(self, name='one', *, legacy=False, epoch='epoch-1', profile=PROFILE):
        project = self.root / name
        project.mkdir(exist_ok=True)
        identity = project_identity(project)
        endpoint = self.runtime / (name + '.sock')
        value = {'backend_epoch': epoch, 'project': str(project), 'project_id': identity,
                 'profile': profile, 'slots': [{'agent_connected': True}],
                 'projects': [{'project_id': identity, 'path': str(project), 'name': name,
                               'muted': False, 'task_count': 1, 'connected_count': 1}],
                 'gui_capabilities': {'version': 1, 'project_mute': True, 'device_selection': True}}
        if legacy:
            value.pop('gui_capabilities')
            value.pop('projects')
            private_json(self.runtime / (name + '.slots.json'),
                         {'version': 2, 'project': str(project), 'slots': [{'token': 'not-for-ui'}]})
        else:
            private_json(self.runtime / (name + '.info.json'),
                         {**value, 'version': 1, 'endpoint': str(endpoint), 'pid': 123})
        self.backends[str(endpoint)] = value
        return value

    def test_overview_is_read_only_and_filters_private_state(self):
        current = self.backend()
        old = self.backend('old', legacy=True)
        response = self.host.overview()
        self.assertEqual(len(response['devices']), 1)
        self.assertEqual(len(response['projects']), 2)
        self.assertFalse(self.state.exists())
        self.assertTrue(all(message['action'] == 'status' for _, message in self.calls))
        self.assertNotIn('not-for-ui', json.dumps(response))
        legacy = next(p for p in response['projects'] if p['id'] == old['project_id'])
        self.assertTrue(legacy['restart_required'])
        self.assertIsNone(legacy['muted'])
        self.assertFalse(legacy['can_mute'])

    def test_selection_persists_and_applies_once_with_epoch_then_stays_offline(self):
        backend = self.backend()
        row = self.host.overview()['devices'][0]
        result = self.host.select_device(row['id'])
        self.assertEqual(result['applied'][0]['status'], 'accepted')
        self.assertEqual(saved_device_selection(self.state)['id'], row['id'])
        self.assertEqual((self.state / 'device-selection.json').stat().st_mode & 0o777, 0o600)
        actions = [m for _, m in self.calls if m['action'] == 'select_device']
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['expected_epoch'], backend['backend_epoch'])
        self.devices = [keyboard(b'other-device', 'other-serial')]
        response = self.new_host().overview()
        self.assertFalse(response['selected_device']['connected'])
        self.assertEqual(response['selected_device']['id'], row['id'])

    def test_no_competing_device_owners_are_started(self):
        self.backend('one')
        self.backend('two')
        identity = self.host.overview()['devices'][0]['id']
        result = self.host.select_device(identity)
        self.assertIn('selection_requires_backend_choice', [e['code'] for e in result['errors']])
        self.assertFalse(any(m['action'] == 'select_device' for _, m in self.calls))

    def test_explicit_project_mute_uses_exact_id_epoch_and_rejects_stale(self):
        backend = self.backend()
        result = self.host.set_project_muted(backend['project_id'], True, 'epoch-1')
        self.assertTrue(result['project']['muted'])
        self.host.set_project_muted(backend['project_id'], False, 'epoch-1')
        with self.assertRaisesRegex(GuiHostError, 'restarted'):
            self.host.set_project_muted(backend['project_id'], True, 'old-epoch')
        mutations = [m for _, m in self.calls if m['action'] == 'set_project_muted']
        self.assertEqual([m['muted'] for m in mutations], [True, False])

    def test_mute_ack_survives_disconnect_without_false_unmute_readback(self):
        backend = self.backend()
        original = self.host._request
        def disconnect_after_ack(descriptor, message):
            result = original(descriptor, message)
            if message['action'] == 'set_project_muted':
                self.backends.clear()
            return result
        with patch.object(self.host, '_request', side_effect=disconnect_after_ack):
            reply = self.host.set_project_muted(backend['project_id'], True, 'epoch-1')
            self.assertIs(reply['project']['muted'], True)
            self.assertIs(reply['acknowledged_muted'], True)
            self.assertEqual(reply['confirmation'], 'acknowledged')
            self.assertEqual(reply['project']['id'], backend['project_id'])
            self.assertEqual([m['action'] for _, m in self.calls], ['status', 'set_project_muted'])
            # A later availability poll can report offline, independently of
            # the already acknowledged successful policy change.
            later = self.host.overview()['projects'][0]
            self.assertFalse(later['connected'])
            self.assertIsNone(later['muted'])
            self.assertIs(reply['project']['muted'], True)

    def test_malformed_mute_ack_cannot_report_success_for_wrong_project_or_bool(self):
        backend = self.backend()
        original = self.host._request
        for change in ('project', 'epoch', 'muted'):
            def invalid_ack(descriptor, message):
                result = original(descriptor, message)
                if message['action'] == 'set_project_muted':
                    result = json.loads(json.dumps(result))
                    if change == 'epoch':
                        result['backend_epoch'] = 'wrong-epoch'
                    elif change == 'project':
                        result['projects'][0]['project_id'] = 'wrong-project'
                    else:
                        result['projects'][0]['muted'] = None
                return result
            with self.subTest(change=change), patch.object(self.host, '_request', side_effect=invalid_ack):
                with self.assertRaisesRegex(GuiHostError, 'did not confirm'):
                    self.host.set_project_muted(backend['project_id'], True, 'epoch-1')

    def test_descriptor_mismatch_symlink_and_foreign_endpoint_are_not_trusted(self):
        backend = self.backend()
        descriptor = self.runtime / 'one.info.json'
        metadata = json.loads(descriptor.read_text())
        metadata['endpoint'] = str(self.root / 'foreign.sock')
        private_json(descriptor, metadata)
        self.assertEqual(self.host.overview()['projects'], [])
        descriptor.unlink()
        outside = self.root / 'outside.json'
        private_json(outside, metadata)
        descriptor.symlink_to(outside)
        self.assertEqual(self.host.overview()['projects'], [])
        self.assertEqual(self.calls, [])

    def test_read_only_start_rejects_unsafe_selection_file(self):
        self.state.mkdir(mode=0o700)
        path = self.state / 'device-selection.json'
        path.write_text('{}')
        path.chmod(0o644)
        host = self.new_host()
        self.assertIsNone(host.selected)
        self.assertTrue(host.startup_errors)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_ndjson_reports_one_result_per_message_and_drains_oversized_request(self):
        valid = json.dumps({'id': 1, 'command': 'scan_devices'}) + '\n'
        payload = 'x' * (MAX_MESSAGE + 10) + '\n' + valid + '{broken}\n'
        output = io.StringIO()
        serve(self.host, io.StringIO(payload), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]['error']['code'], 'invalid_message_size')
        self.assertTrue(responses[1]['ok'])
        self.assertEqual(responses[1]['id'], 1)
        self.assertFalse(responses[2]['ok'])

    def test_cached_overview_does_not_repeat_inventory_or_service_reads(self):
        self.backend()
        with patch.object(self.host, 'enumerator', return_value=self.devices) as enumerate_devices:
            self.host.overview()
            calls = len(self.calls)
            self.host.overview()
            self.assertEqual(len(self.calls), calls)
            enumerate_devices.assert_called_once()
            self.host.dispatch('scan_devices', {})
            self.assertEqual(enumerate_devices.call_count, 2)

    def test_slow_metadata_is_bounded_and_background_refresh_is_not_duplicated(self):
        self.backend()
        complete = threading.Event()
        original = self.host._project
        def slow(descriptor):
            complete.wait(4.)
            return original(descriptor)
        with patch.object(self.host, '_project', side_effect=slow) as request:
            started = time.monotonic()
            response = self.host.overview()
            self.assertLess(time.monotonic() - started, 2.5)
            self.assertEqual(response['projects'][0]['status'], 'checking')
            complete.set()
            response = self.host.overview()
            self.assertTrue(response['projects'][0]['connected'])
            request.assert_called_once()


class DriverDeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.driver = DeviceDriver(self.broker, PROFILE, 'hardware')
        self.info = keyboard()
        self.fingerprint = device_fingerprint(self.info)
        self.identity = device_id(self.fingerprint)

    def test_exact_path_open_does_not_use_profile_enumeration_index(self):
        self.driver.selected_device_fingerprint = self.fingerprint
        with patch('abralia.backend.device.resolve_device', return_value=self.info), \
             patch('abralia.backend.device.SharedRawHidSession.open_profile') as automatic, \
             patch('abralia.backend.device.SharedRawHidSession.open_path', side_effect=RuntimeError('stop-before-open')) as exact:
            with self.assertRaisesRegex(RuntimeError, 'stop-before-open'):
                self.driver.start()
        automatic.assert_not_called()
        exact.assert_called_once_with(self.info.path, self.info)

    def test_repeated_selection_is_noop_after_fresh_descriptor_validation(self):
        self.driver.selected_device_id = self.identity
        self.driver.selected_device_fingerprint = self.fingerprint
        self.driver.protocol = object()
        self.broker.delivery = 'written'
        with patch('abralia.backend.device.resolve_device', return_value=self.info), \
             patch.object(self.driver, 'close') as close:
            result = self.driver.select_device(self.fingerprint, self.identity, PROFILE)
        self.assertFalse(result['changed'])
        close.assert_not_called()

    def test_disconnect_or_wrong_profile_does_not_release_previous_keyboard(self):
        with patch('abralia.backend.device.resolve_device', side_effect=DeviceSelectionError('selected_device_disconnected')), \
             patch.object(self.driver, 'close') as close:
            with self.assertRaisesRegex(ValueError, 'disconnected'):
                self.driver.select_device(self.fingerprint, self.identity, PROFILE)
            with self.assertRaisesRegex(ValueError, 'profile_mismatch'):
                self.driver.select_device(self.fingerprint, self.identity, SUPPORTED_PROFILES[1])
        close.assert_not_called()

    def test_failed_switch_suspends_without_fallback_and_preserves_renderer(self):
        renderer = self.driver.renderer
        with patch('abralia.backend.device.resolve_device', return_value=self.info), \
             patch.object(self.driver, 'start', side_effect=RuntimeError('fixture failure')), \
             patch.object(self.driver, 'close'):
            result = self.driver.select_device(self.fingerprint, self.identity, PROFILE)
        self.assertEqual(result['reason'], 'selected_device_open_failed')
        self.assertEqual(self.broker.delivery, 'suspended')
        self.assertEqual(self.driver.selected_device_id, self.identity)
        self.assertIs(self.driver.renderer, renderer)


class ManagedLifecycleTests(unittest.TestCase):
    new_host = GuiHostTests.new_host
    backend = GuiHostTests.backend

    def setUp(self):
        GuiHostTests.setUp(self)
        self.host.close()
        self.created = []
        self.fail_start = False
        self.events = []
        parent = self

        class ManagedService:
            def __init__(service, project, profile, **kwargs):
                service.project = str(Path(project).resolve())
                service.profile = profile
                service.kwargs = kwargs
                service.endpoint = Path(kwargs['endpoint'])
                service.mode = kwargs['mode']
                service.start_count = service.close_count = 0
                service.worker_error = service.cleanup_error = None
                service.broker = SimpleNamespace(delivery='simulated' if service.mode == 'simulated' else 'ready',
                                                device_error=None, epoch=f'owned-{len(parent.created) + 1}')
                service.driver = SimpleNamespace(selected_device_id=saved_device_selection(kwargs['state_dir'])['id'])
                parent.created.append(service)

            def start(service):
                service.start_count += 1
                parent.events.append(('start', service.project))
                if parent.fail_start:
                    raise RuntimeError('fixture startup failure')
                identity = project_identity(service.project)
                value = {'status': 'accepted', 'project': service.project, 'project_id': identity,
                         'profile': service.profile, 'mode': service.mode, 'backend_epoch': service.broker.epoch,
                         'delivery': service.broker.delivery, 'device_error': None,
                         'selected_device_id': service.driver.selected_device_id, 'slots': [],
                         'projects': [{'project_id': identity, 'path': service.project,
                                       'name': Path(service.project).name, 'muted': False,
                                       'task_count': 0, 'connected_count': 0}],
                         'gui_capabilities': {'project_mute': True, 'device_selection': service.mode == 'hardware'}}
                parent.backends[str(service.endpoint)] = value
                private_json(service.endpoint.with_suffix('.info.json'),
                             {**value, 'version': 1, 'pid': 123, 'endpoint': str(service.endpoint)})
                private_json(service.endpoint.with_suffix('.slots.json'),
                             {'version': 2, 'project': service.project, 'allocations': []})
                return service

            def close(service):
                service.close_count += 1
                parent.events.append(('close', service.project))
                parent.backends.pop(str(service.endpoint), None)
                service.endpoint.with_suffix('.info.json').unlink(missing_ok=True)

        self.host = GuiHost(state_dir=self.state, runtime_dir=self.runtime, enumerator=lambda: self.devices,
                            client_factory=self.factory, managed_mode='simulated', service_factory=ManagedService)
        self.addCleanup(self.cleanup_managed)
        self.probe = patch.object(self.host, '_endpoint_listening', return_value=False)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def cleanup_managed(self):
        if self.host._owned_service:
            self.host._owned_service.cleanup_error = None
        self.host._backend.update(error=None, reason=None)
        self.host.close()

    def save_device(self, identity=None):
        self.state.mkdir(mode=0o700, exist_ok=True)
        choices = scan_devices(enumerator=lambda: self.devices)
        selected = next((d for d in choices if d['id'] == identity), choices[0])
        private_json(self.state / 'device-selection.json', {'version': 1, 'selected_device': selected})
        return selected

    def offline_project(self, name='known'):
        value = self.backend(name, legacy=True)
        endpoint = str(self.runtime / (name + '.sock'))
        self.backends.pop(endpoint)
        return value, endpoint

    def test_missing_or_disconnected_selection_starts_no_owner_and_reports_reason(self):
        with self.assertRaisesRegex(GuiHostError, 'Choose a keyboard'):
            self.host.start_backend()
        view = self.host.overview()['backend']
        self.assertEqual(view['state'], 'stopped')
        self.assertEqual(view['reason'], 'selection_required')
        self.assertIsNone(view['error'])
        self.save_device()
        self.devices = []
        with self.assertRaisesRegex(GuiHostError, 'disconnected'):
            self.host.start_backend()
        self.assertEqual(self.created, [])
        self.assertIn('disconnected', self.host.overview()['backend']['error'])

    def test_one_known_offline_project_starts_same_endpoint_and_is_reused(self):
        self.save_device()
        known, endpoint = self.offline_project()
        reply = self.host.start_backend()
        self.assertTrue(reply['backend']['owned'])
        service = self.created[0]
        self.assertEqual(str(service.endpoint), endpoint)
        self.assertEqual(service.profile, PROFILE)
        self.assertEqual(service.kwargs['state_dir'], self.state)
        self.assertFalse(service.kwargs['observe_codex'])
        self.host.start_backend()
        self.assertEqual(service.start_count, 1)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.host.overview()['projects'][0]['id'], known['project_id'])

    def test_multiple_projects_require_choice_visible_after_rejection(self):
        self.save_device()
        first, _ = self.offline_project('first')
        second, _ = self.offline_project('second')
        with self.assertRaisesRegex(GuiHostError, 'Choose a project'):
            self.host.start_backend()
        choices = self.host.overview()['backend']['project_choices']
        self.assertEqual({p['id'] for p in choices}, {first['project_id'], second['project_id']})
        self.assertEqual(self.created, [])
        self.host.start_backend(second['project_id'])
        self.assertEqual(self.created[0].project, str(Path(second['project']).resolve()))

    def test_device_only_backend_is_hidden_and_overview_does_not_restart_after_stop(self):
        self.save_device()
        self.host.start_backend()
        self.assertEqual(self.host.overview()['projects'], [])
        self.assertIsNone(self.host.overview()['backend']['project_id'])
        self.assertFalse(self.created[0].kwargs['observe_codex'])
        self.assertEqual(Path(self.created[0].project).stat().st_mode & 0o777, 0o700)
        self.host.stop_backend()
        for _ in range(3):
            self.assertEqual(self.host.overview()['backend']['state'], 'stopped')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0].close_count, 1)

    def test_external_service_is_reused_and_never_stopped_by_close(self):
        self.save_device()
        value = self.backend()
        value.update(mode='simulated', delivery='simulated')
        value['gui_capabilities']['device_selection'] = False
        reply = self.host.start_backend()
        self.assertEqual(reply['backend']['state'], 'external')
        self.assertFalse(reply['backend']['owned'])
        self.host.stop_backend()
        self.host.close()
        self.assertEqual(self.created, [])
        self.assertTrue(self.backends)

    def test_requested_offline_project_cannot_silently_reuse_another_live_project(self):
        self.save_device()
        live = self.backend('live')
        live.update(mode='simulated', delivery='simulated')
        requested, _ = self.offline_project('requested')
        with self.assertRaisesRegex(GuiHostError, 'another project'):
            self.host.start_backend(requested['project_id'])
        self.assertEqual(self.created, [])

    def test_start_failure_is_visible_and_partial_owner_is_closed(self):
        self.save_device()
        self.fail_start = True
        with self.assertRaisesRegex(GuiHostError, 'fixture startup failure'):
            self.host.start_backend()
        self.assertEqual(self.created[0].close_count, 1)
        self.assertEqual(self.host.overview()['backend']['state'], 'error')
        self.assertIn('fixture startup failure', self.host.overview()['backend']['error'])

    def test_cleanup_error_blocks_replacement_and_makes_close_fail(self):
        self.save_device()
        self.host.start_backend()
        service = self.created[0]
        service.cleanup_error = 'fixture cleanup unverified'
        service.worker_error = 'fixture worker stopped'
        with self.assertRaisesRegex(GuiHostError, 'cleanup unverified'):
            self.host.start_backend()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.host.overview()['backend']['reason'], 'backend_cleanup_unverified')
        with self.assertRaisesRegex(GuiHostError, 'cleanup unverified'):
            self.host.close()

    def test_selecting_external_device_stops_previous_owned_profile_before_reuse(self):
        other_profile = load_device_profile(SUPPORTED_PROFILES[1])
        match = other_profile.device_match
        other = replace(keyboard(b'other', 'other'), vendor_id=match.vendor_id, product_id=match.product_id,
                        usage_page=match.usage_page, usage=match.usage)
        self.devices = [keyboard(), other]
        first_id = device_id(device_fingerprint(self.devices[0]))
        second_id = device_id(device_fingerprint(other))
        self.save_device(first_id)
        self.host.managed_mode = 'hardware'  # Fake service only; no physical I/O.
        self.host.start_backend()
        owned = self.created[0]
        external = self.backend('external', profile=SUPPORTED_PROFILES[1])
        external.update(mode='hardware', delivery='ready', selected_device_id=second_id)
        reply = self.host.select_device(second_id)
        self.assertEqual(owned.close_count, 1)
        self.assertIsNone(self.host._owned_service)
        self.assertEqual(reply['backend']['state'], 'external')
        self.assertEqual(reply['backend']['project_id'], external['project_id'])
        self.assertEqual(len(self.created), 1)

    def test_select_device_starts_service_explicitly_and_close_releases_it(self):
        identity = scan_devices(enumerator=lambda: self.devices)[0]['id']
        reply = self.host.select_device(identity)
        self.assertTrue(reply['saved'])
        self.assertEqual(reply['backend']['state'], 'ready')
        service = self.created[0]
        self.host.close()
        self.assertEqual(service.close_count, 1)
        self.assertFalse(self.backends)


if __name__ == '__main__':
    unittest.main()
