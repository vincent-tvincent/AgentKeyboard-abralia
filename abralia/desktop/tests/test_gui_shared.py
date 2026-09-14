# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Shared GUI routing with injected descriptors/IPC; never opens hardware."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from abralia.backend.gui_devices import scan_devices
from abralia.backend.gui_host import GuiHost, GuiHostError
from abralia.backend.project_policy import project_identity
from abralia.backend.project_registry import ProjectRegistry
from test_gui_host import keyboard, private_json, PROFILE


class SharedGuiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control, self.runtime = self.root / 'control', self.root / 'runtime'
        self.control.mkdir(mode=0o700); self.runtime.mkdir(mode=0o700)
        self.service_root = self.control / 'service'
        self.service_root.mkdir(mode=0o700)
        self.endpoint = self.runtime / 'shared.sock'
        self.selected = scan_devices(enumerator=lambda: [keyboard(b'new-board', 'new-serial')])[0]
        self.registry = ProjectRegistry(self.control)
        self.rows = []
        for name in ('first', 'second'):
            folder = self.root / name
            folder.mkdir()
            row = self.registry.enroll(folder)
            self.rows.append({**row, 'muted': name == 'first', 'task_count': 2,
                              'connected_count': 1, 'hooks_received': 3 if name == 'first' else 7,
                              'last_hook_at': 123.})
        self.status = {'status': 'accepted', 'shared': True, 'project': str(self.service_root),
                       'project_id': project_identity(self.service_root), 'profile': PROFILE,
                       'mode': 'hardware', 'backend_epoch': 'shared-epoch',
                       'selected_device_id': 'older-physical-device', 'delivery': 'written',
                       'device_error': None, 'projects': deepcopy(self.rows), 'slots': [],
                       'gui_capabilities': {'project_mute': True, 'device_selection': True}}
        self.descriptor = {'version': 1, 'shared': True, 'project': str(self.service_root),
                           'project_id': project_identity(self.service_root), 'profile': PROFILE,
                           'endpoint': str(self.endpoint), 'backend_epoch': 'shared-epoch', 'pid': 123}
        self.calls = []
        self.selection_result = {'status': 'accepted', 'device_id': self.selected['id']}
        parent = self
        def client_factory(project, *, endpoint, role):
            self.assertEqual(project, str(self.service_root))
            self.assertEqual(endpoint, str(self.endpoint))
            self.assertEqual(role, 'admin')
            class Client:
                def request(_client, message):
                    parent.calls.append(deepcopy(message))
                    if message['action'] == 'status':
                        return deepcopy(parent.status)
                    if message['action'] == 'select_device':
                        return deepcopy(parent.selection_result)
                    raise AssertionError('Unexpected fixture operation')
                def close(_client):
                    pass
            return Client()
        self.factory = Mock(side_effect=AssertionError('No second backend may be created'))
        # Hardware branch is intentionally exercised with injected inventory,
        # IPC and a forbidden service factory; there is no device I/O here.
        self.host = GuiHost(state_dir=self.control, runtime_dir=self.runtime, shared_mode=True,
                            managed_mode='hardware', service_factory=self.factory,
                            enumerator=lambda: [keyboard(b'new-board', 'new-serial')],
                            client_factory=client_factory)
        self.addCleanup(self.host.close)
        self.integrations = patch.object(self.host, 'integration_status', return_value={'fixture': True})
        self.integrations.start(); self.addCleanup(self.integrations.stop)

    def publish(self):
        private_json(self.runtime / 'shared.info.json', self.descriptor)

    def test_external_owner_applies_exact_new_device_once_for_two_project_rows(self):
        self.publish()
        reply = self.host._start_shared_backend(self.selected)
        self.assertEqual(reply['backend']['state'], 'external')
        self.assertFalse(reply['backend']['owned'])
        self.factory.assert_not_called()
        selections = [call for call in self.calls if call['action'] == 'select_device']
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0]['device_id'], self.selected['id'])
        self.assertEqual(selections[0]['fingerprint'], self.selected['fingerprint'])
        self.assertEqual(selections[0]['expected_epoch'], 'shared-epoch')

    def test_rejected_external_selection_is_not_reported_as_reuse_success(self):
        self.publish()
        self.selection_result = {'status': 'rejected', 'reason': 'selected_device_disconnected'}
        with self.assertRaisesRegex(GuiHostError, 'selected_device_disconnected'):
            self.host._start_shared_backend(self.selected)
        self.assertIsNone(self.host._external_descriptor)
        self.assertEqual(self.host._backend_snapshot()['state'], 'error')
        self.assertEqual(self.host._backend_snapshot()['reason'], 'device_selection_failed')
        self.factory.assert_not_called()

    def test_shared_rows_keep_separate_mute_health_and_same_endpoint(self):
        self.publish()
        result = self.host.overview()
        self.assertEqual(len(result['projects']), 2)
        rows = {row['id']: row for row in result['projects']}
        for original in self.rows:
            row = rows[original['project_id']]
            self.assertEqual(row['path'], original['path'])
            self.assertEqual(row['muted'], original['muted'])
            self.assertEqual(row['hooks_received'], original['hooks_received'])
            self.assertEqual(row['last_hook_at'], original['last_hook_at'])
            self.assertEqual(row['backend_epoch'], 'shared-epoch')
            self.assertEqual(row['delivery'], 'written')
            self.assertNotIn('_project_rows', row)
        pairs, _ = self.host._projects()
        self.assertEqual({descriptor['endpoint'] for _, descriptor in pairs}, {str(self.endpoint)})

    def test_zero_enrolled_projects_still_detects_and_reuses_shared_service(self):
        for row in self.rows:
            self.registry.remove(row['project_id'])
        self.status['projects'] = []
        self.status['selected_device_id'] = self.selected['id']
        self.publish()
        self.assertEqual(self.host.overview()['projects'], [])
        reply = self.host._start_shared_backend(self.selected)
        self.assertEqual(reply['backend']['state'], 'external')
        self.assertIsNone(reply['backend']['project_id'])
        self.assertFalse(any(call['action'] == 'select_device' for call in self.calls))
        self.factory.assert_not_called()

    def test_missing_external_endpoint_allows_offline_enroll_and_remove(self):
        folder = self.root / 'third'
        folder.mkdir()
        self.host._external_descriptor = self.descriptor
        self.host._backend.update(state='external')
        with patch.object(self.host, '_endpoint_listening', return_value=False), \
             patch.object(self.host, '_request', side_effect=AssertionError('No stale IPC request')):
            self.host.register_project(str(folder))
            self.assertIsNone(self.host._external_descriptor)
            self.assertEqual(self.host._backend['state'], 'stopped')
            self.assertEqual(self.registry.resolve(folder)['project_id'], project_identity(folder))
            self.host._external_descriptor = self.descriptor
            self.host.remove_project(project_identity(folder))
            self.assertIsNone(self.registry.resolve(folder))
        self.factory.assert_not_called()

    def test_uncertain_external_endpoint_never_falls_back_to_offline_mutation(self):
        folder = self.root / 'third'
        folder.mkdir()
        self.host._external_descriptor = self.descriptor
        with patch.object(self.host, '_endpoint_listening', return_value=None), \
             patch.object(self.host, '_request', return_value={'status': 'skipped', 'reason': 'backend_unavailable'}):
            with self.assertRaises(GuiHostError):
                self.host.register_project(str(folder))
        self.assertIsNone(self.registry.resolve(folder))
        self.assertIsNotNone(self.host._external_descriptor)


if __name__ == '__main__':
    unittest.main()
