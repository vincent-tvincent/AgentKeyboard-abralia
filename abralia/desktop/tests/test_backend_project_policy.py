# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import Broker, Caller
from abralia.backend.ipc import BrokerClient
from abralia.backend.project_policy import ProjectPolicy, project_identity, read_private_json, write_private_json
from abralia.backend.service import BrokerService
from abralia.rgb import load_profile


PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class Clock:
    def __init__(self): self.now = 0.
    def __call__(self): return self.now


class ProjectMuteTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.b = Broker(clock=self.clock)
        self.counter = 0

    def agent(self, number):
        caller = Caller(str(number), str(UUID(int=number)), surface='codex_desktop')
        result = self.b.call(caller, 'acquire_slot', {'label': str(number), 'idempotency_key': 'acquire'})
        return self.b.slots[result['allocation']['slot_id']]

    def call(self, slot, operation, **arguments):
        self.counter += 1
        result = self.b.call(slot.caller, operation, {
            'slot_token': slot.slot_token, 'idempotency_key': str(self.counter), **arguments})
        self.assertEqual(result['status'], 'accepted', result)
        return result

    def test_mute_suppresses_current_queued_and_new_attention_without_changing_work(self):
        first, second = self.agent(1), self.agent(2)
        self.call(first, 'set_slot_state', state='progressing', progress=.6)
        for slot in (first, second):
            self.call(slot, 'set_notification', enabled=True)
        self.b.set_project_muted(True)
        self.assertIsNone(self.b.current_call)
        self.assertIsNone(self.b.pending_target())
        self.assertFalse(self.b.has_attention())
        self.assertEqual((first.state, first.progress), ('progressing', .6))
        third = self.agent(3)
        self.call(third, 'set_notification', enabled=True)
        self.call(third, 'set_slot_state', state='completed')
        for slot in (first, second, third):
            self.assertTrue(self.b.attention_muted(slot))
            self.assertEqual(self.b.attention_snapshot(slot)['source'], 'project')
        visuals = self.b.notification_visuals()
        self.assertIsNone(visuals['presentation'])
        self.assertEqual(len(visuals['orbs']), 3)
        self.assertTrue(all(orb['opacity'] == 0 and orb['hidden'] for orb in visuals['orbs']))

    def test_unmute_keeps_visual_age_and_does_not_replay_old_onset(self):
        slot = self.agent(1)
        self.call(slot, 'set_notification', enabled=True)
        self.clock.now = 12
        self.b.step()
        original = self.b.notification_visuals()['orbs'][0]
        self.b.set_project_muted(True)
        self.clock.now += 10
        self.b.step()
        self.b.set_project_muted(False)
        current = self.b.notification_visuals()['orbs'][0]
        self.assertEqual(current['orb_key'], original['orb_key'])
        self.assertEqual(current['formed_at'], original['formed_at'])
        self.assertFalse(current['hidden'])
        self.assertIsNone(self.b.notification_visuals()['presentation'])
        self.assertIsNotNone(self.b.pending_target())

    def test_manual_pickup_and_question_are_preserved_during_project_mute(self):
        self.b.set_project_muted(True)
        slot = self.agent(1)
        self.call(slot, 'report_question', question_id='question', kind='free_text', options=[])
        self.b.set_active(True)
        self.b.select_slot(slot.slot_token)
        self.assertEqual(self.b.selected, slot.slot_id)
        self.assertTrue(slot.question.picked_up)
        self.assertEqual(list(self.b.focus_requests), [(slot.slot_token, slot.caller.thread_id)])
        self.assertTrue(self.b.project_muted)
        self.assertEqual(slot.notification.status, 'picked_up')
        self.call(slot, 'clear_question', question_id='question', outcome='answered')
        self.assertIsNone(slot.question)

    def test_persistent_mute_layers_over_timed_mutes_without_resetting_them(self):
        first, second = self.agent(1), self.agent(2)
        self.b.agent_mutes = {first.slot_token: 40}
        self.b.set_active(True)
        self.b.toggle_navigation()
        self.b.set_project_muted(True)
        self.assertFalse(self.b.attention_controls())
        self.clock.now = 20
        self.b.step()
        self.b.set_project_muted(False)
        self.assertTrue(self.b.attention_muted(first))
        self.assertFalse(self.b.attention_muted(second))
        self.assertEqual(self.b.agent_mutes[first.slot_token], 40)

    def test_project_mute_has_no_timer_and_repeated_set_is_noop(self):
        self.assertTrue(self.b.set_project_muted(True))
        revision = self.b.attention_policy_revision
        self.assertFalse(self.b.set_project_muted(True))
        self.assertEqual(self.b.attention_policy_revision, revision)
        self.clock.now = 100000
        self.b.step()
        self.assertTrue(self.b.project_muted)
        self.assertFalse(self.b.call(Caller('test'), 'set_project_muted', {})['status'] == 'accepted')


class ProjectPolicyFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-policy-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.policy = ProjectPolicy(self.root, self.root / 'broker.policy.json')

    def tearDown(self): self.temp.cleanup()

    def test_persistent_roundtrip_is_private_and_idempotent(self):
        self.assertFalse(self.policy.load())
        self.policy.save(True)
        before = self.policy.path.stat().st_mtime_ns
        self.assertEqual(self.policy.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(ProjectPolicy(self.root, self.policy.path).load())
        self.policy.save(True)
        self.assertEqual(before, self.policy.path.stat().st_mtime_ns)
        self.policy.save(False)
        self.assertFalse(ProjectPolicy(self.root, self.policy.path).load())
        self.assertEqual(project_identity(self.root / '.'), self.policy.project_id)

    def test_other_project_and_corrupt_policy_fail_without_overwrite(self):
        self.policy.save(True)
        original = self.policy.path.read_bytes()
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'invalid_project_policy'):
            ProjectPolicy(other, self.policy.path).save(False)
        self.assertEqual(self.policy.path.read_bytes(), original)
        self.policy.path.write_text('{corrupt')
        with self.assertRaises(ValueError):
            self.policy.save(False)
        self.assertEqual(self.policy.path.read_text(), '{corrupt')

    def test_symlink_and_nonprivate_file_are_rejected(self):
        target = self.root / 'target.json'
        target.write_text('{}')
        self.policy.path.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            self.policy.save(True)
        self.assertEqual(target.read_text(), '{}')
        self.policy.path.unlink()
        self.policy.save(True)
        self.policy.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'unsafe_private'):
            self.policy.load()

    def test_failed_atomic_replace_preserves_previous_policy(self):
        self.policy.save(True)
        original = self.policy.path.read_bytes()
        with patch('abralia.backend.project_policy.os.replace', side_effect=OSError('fixture full disk')):
            with self.assertRaises(OSError):
                self.policy.save(False)
        self.assertEqual(self.policy.path.read_bytes(), original)
        self.assertFalse(list(self.root.glob('.abralia-policy-*')))

    def test_default_endpoint_policy_is_durable_application_data_not_tmp(self):
        state = self.root / 'Abralia'
        with patch('abralia.backend.service.default_state_dir', return_value=state):
            service = BrokerService(self.root, PROFILE)
        self.assertEqual(service.project_policy.path, state / 'project-policies' / f'{service.project_id}.json')
        service.project_policy.save(True)
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / 'project-policies').stat().st_mode & 0o777, 0o700)
        self.assertTrue(ProjectPolicy(self.root, service.project_policy.path).load())

    def test_explicit_state_directory_overrides_test_endpoint_sidecar(self):
        state = self.root / 'isolated-state'
        service = BrokerService(self.root, PROFILE, endpoint=self.root / 'custom.sock', state_dir=state)
        self.assertEqual(service.project_policy.path.parent, state / 'project-policies')
        service.project_policy.save(True)
        self.assertTrue(service.project_policy.load())

    def test_worker_start_applies_real_gui_builtin_profile_selection(self):
        from abralia.backend.gui_devices import device_id
        fingerprint = {'vendor_id': 0x3434, 'product_id': 0x0B31,
                       'usage_page': 0xFF60, 'usage': 0x61, 'interface_number': 2,
                       'serial_number': 'SIMULATED-selection-fixture',
                       'path_b64': base64.b64encode(b'SIMULATED-hid-path').decode('ascii')}
        selected = {'id': device_id(fingerprint), 'profile_id': PROFILE, 'fingerprint': fingerprint}
        write_private_json(self.root / 'device-selection.json', {'version': 1, 'selected_device': selected})
        service = BrokerService(self.root, PROFILE, mode='hardware',
                                endpoint=self.root / 'unused.sock', state_dir=self.root)
        observed = []
        driver = SimpleNamespace(profile=load_profile(PROFILE), selected_device_id=None,
                                 selected_device_fingerprint=None, close=lambda: None)
        def start():
            observed.append((driver.selected_device_id, driver.selected_device_fingerprint))
            service.stop_event.set()
        driver.start = start
        with patch('abralia.backend.service.DeviceDriver', return_value=driver):
            service._run_worker()  # Hardware I/O is replaced; real saved JSON/profile parsing runs.
        self.assertTrue(service.started.result())
        self.assertEqual(observed, [(selected['id'], fingerprint)])
        self.assertIsNone(service.worker_error)

    def test_saved_choice_does_not_override_an_explicitly_different_profile(self):
        from abralia.backend.gui_devices import device_id
        fingerprint = {'vendor_id': 0x3434, 'product_id': 0x0B31,
                       'usage_page': 0xFF60, 'usage': 0x61, 'interface_number': 2,
                       'serial_number': '', 'path_b64': base64.b64encode(b'fixture').decode('ascii')}
        selected = {'id': device_id(fingerprint),
                    'profile_id': 'builtin:keychron-v3-ansi-effect25', 'fingerprint': fingerprint}
        write_private_json(self.root / 'device-selection.json', {'version': 1, 'selected_device': selected})
        service = BrokerService(self.root, PROFILE, mode='hardware',
                                endpoint=self.root / 'unused.sock', state_dir=self.root)
        driver = SimpleNamespace(profile=load_profile(PROFILE), selected_device_id=None,
                                 selected_device_fingerprint=None, start=service.stop_event.set,
                                 close=lambda: None)
        with patch('abralia.backend.service.DeviceDriver', return_value=driver):
            service._run_worker()
        self.assertTrue(service.started.result())
        self.assertIsNone(driver.selected_device_id)
        self.assertIsNone(driver.selected_device_fingerprint)


class ProjectPolicyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-project-ui-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.socket = self.root / 'broker.sock'
        self.service = BrokerService(self.root, PROFILE, endpoint=self.socket).start()

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def client(self, role='admin'):
        return BrokerClient(self.root, endpoint=self.socket, role=role)

    def mute_command(self, muted=True):
        return {'type': 'admin', 'action': 'set_project_muted', 'muted': muted,
                'project_id': self.service.project_id, 'expected_epoch': self.service.broker.epoch}

    def test_gui_status_counts_actual_project_and_connected_agents(self):
        with self.client() as admin, self.client('agent') as agent:
            self.assertEqual(admin.request({'type': 'admin', 'action': 'seed_fixtures', 'count': 2})['status'], 'accepted')
            acquired = agent.request({'type': 'call', 'operation': 'acquire_slot',
                'metadata': {'thread_id': str(UUID(int=5))},
                'arguments': {'label': 'Live task', 'idempotency_key': 'acquire'}})
            self.assertEqual(acquired['status'], 'accepted')
            status = admin.request({'type': 'admin', 'action': 'status'})
            self.assertEqual(status['gui_capabilities']['version'], 1)
            self.assertTrue(status['gui_capabilities']['project_mute'])
            self.assertFalse(status['gui_capabilities']['device_selection'])
            self.assertNotIn('select_device', status['admin_operations'])
            self.assertEqual(len(status['projects']), 1)
            project = status['projects'][0]
            self.assertEqual(project['path'], str(self.root.resolve()))
            self.assertEqual((project['task_count'], project['connected_count']), (3, 1))
            self.assertNotIn('slot_token', json.dumps(status))

    def test_simulated_service_never_accepts_physical_device_selection(self):
        with self.client() as admin:
            with patch.object(self.service.driver, 'select_device') as select:
                response = admin.request({'type': 'admin', 'action': 'select_device',
                                          'expected_epoch': self.service.broker.epoch,
                                          'profile_id': PROFILE, 'device_id': 'fixture', 'fingerprint': {}})
            self.assertEqual(response['reason'], 'device_selection_unavailable')
            select.assert_not_called()

    def test_only_admin_with_matching_project_and_epoch_can_persist_mute(self):
        with self.client() as admin, self.client('agent') as agent:
            command = self.mute_command()
            self.assertEqual(agent.request(command)['status'], 'rejected')
            self.assertEqual(admin.request({**command, 'project_id': 'other'})['status'], 'rejected')
            self.assertEqual(admin.request({**command, 'expected_epoch': 'old'})['status'], 'rejected')
            self.assertEqual(admin.request({**command, 'muted': 1})['status'], 'rejected')
            self.assertFalse(self.service.project_policy.path.exists())
            result = admin.request(command)
            self.assertEqual(result['status'], 'accepted')
            self.assertTrue(result['projects'][0]['muted'])
            self.assertFalse(admin.request(command)['changed'])
            self.assertTrue(self.service.project_policy.load())

    def test_mute_recovers_at_restart_and_old_epoch_command_is_rejected(self):
        with self.client() as admin:
            command = self.mute_command()
            self.assertEqual(admin.request(command)['status'], 'accepted')
        self.service.close()
        self.service = BrokerService(self.root, PROFILE, endpoint=self.socket).start()
        with self.client() as admin:
            self.assertTrue(admin.request({'type': 'admin', 'action': 'status'})['projects'][0]['muted'])
            self.assertEqual(admin.request({**command, 'muted': False})['status'], 'rejected')
            self.assertEqual(admin.request(self.mute_command(False))['status'], 'accepted')

    def test_storage_failure_rejects_without_changing_running_policy(self):
        with self.client() as admin:
            with patch.object(self.service.project_policy, 'save', side_effect=OSError('fixture storage failure')):
                result = admin.request(self.mute_command())
            self.assertEqual(result['reason'], 'project_policy_storage_unavailable')
            self.assertFalse(admin.request({'type': 'admin', 'action': 'status'})['projects'][0]['muted'])

    def test_corrupt_policy_prevents_silent_unmute_on_restart(self):
        self.service.close()
        write_private_json(self.service.project_policy.path, {'version': 1, 'muted': 'invalid'})
        self.service = BrokerService(self.root, PROFILE, endpoint=self.socket)
        with self.assertRaisesRegex(ValueError, 'invalid_project_policy'):
            self.service.start()
        self.assertFalse(self.service.info_path.exists())

    def test_project_mute_does_not_change_another_running_simulated_project(self):
        other = self.root / 'other'
        other.mkdir()
        endpoint = self.root / 'other.sock'
        with BrokerService(other, PROFILE, endpoint=endpoint) as other_service:
            with self.client() as admin, BrokerClient(other, endpoint=endpoint, role='admin') as other_admin:
                self.assertEqual(admin.request(self.mute_command())['status'], 'accepted')
                status = other_admin.request({'type': 'admin', 'action': 'status'})
                self.assertFalse(status['projects'][0]['muted'])
                self.assertNotEqual(status['projects'][0]['project_id'], self.service.project_id)
                self.assertFalse(other_service.project_policy.path.exists())

    def test_private_discovery_metadata_matches_running_service_and_is_cleaned(self):
        path = self.service.info_path
        info = read_private_json(path)
        self.assertEqual(info['version'], 1)
        self.assertEqual(info['backend_epoch'], self.service.broker.epoch)
        self.assertEqual(info['project_id'], self.service.project_id)
        self.assertEqual(info['project'], str(self.root.resolve()))
        self.assertEqual(info['endpoint'], str(self.socket.resolve()))
        self.assertEqual(info['pid'], os.getpid())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(info), {'version', 'backend_version', 'pid', 'project', 'project_id',
                                     'endpoint', 'backend_epoch', 'profile'})
        self.service.close()
        self.assertFalse(path.exists())

    def test_close_preserves_metadata_replaced_by_another_epoch(self):
        path = self.service.info_path
        info = read_private_json(path)
        info['backend_epoch'] = 'other'
        write_private_json(path, info)
        self.service.close()
        self.assertEqual(read_private_json(path), info)


if __name__ == '__main__':
    unittest.main()
