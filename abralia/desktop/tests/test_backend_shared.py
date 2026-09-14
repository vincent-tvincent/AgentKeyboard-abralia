# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from abralia.backend.device import DeviceDriver
from abralia.backend.project_registry import ProjectRegistry
from abralia.backend.service import BrokerService

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class SharedBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / 'control'
        self.app_root = self.root / 'service'
        self.app_root.mkdir()
        self.a, self.b = self.root / 'one', self.root / 'two'
        self.a.mkdir(); self.b.mkdir()
        self.registry = ProjectRegistry(self.control)
        self.pa, self.pb = self.registry.enroll(self.a), self.registry.enroll(self.b)
        self.ids = [str(uuid4()), str(uuid4())]
        self.service = self.new_service()

    def new_service(self):
        service = BrokerService(self.app_root, PROFILE, mode='simulated', shared=True,
                                state_dir=self.control, endpoint=self.root / 'shared.sock',
                                recovery_owner_check=lambda _owner: True)
        # Invoke the same worker handlers synchronously: no socket listener or
        # HID handle is created by these isolation tests.
        service.driver = DeviceDriver(service.broker, PROFILE, 'simulated')
        service.driver.start()
        self.addCleanup(service.driver.close)
        service.recovery.load()
        service._refresh_projects()
        return service

    def hello(self, connection, project, *, role='agent', service=None, **extra):
        service = service or self.service
        return service._handle({'connection': connection, 'role': role, 'handshake': True,
            'owner_verified': bool(role == 'agent' and extra.get('recovery_key') is not None
                                   and service.recovery.owner_check(extra.get('recovery_owner'))),
            'message': {'type': 'hello', 'role': role, 'project': project['path'],
                        'project_generation': project['generation'], **extra}})

    def call(self, connection, project, thread, operation, arguments, *, service=None):
        return (service or self.service)._handle({'connection': connection, 'role': 'agent',
            'message': {'type': 'call', 'operation': operation, 'arguments': arguments,
                        'metadata': {'thread_id': thread, 'cwd': project['path']}}})

    def acquire(self, connection, project, thread, *, service=None):
        result = self.call(connection, project, thread, 'acquire_slot',
                           {'label': 'Fixture', 'harness': 'codex_desktop', 'idempotency_key': 'acquire'}, service=service)
        self.assertEqual(result['status'], 'accepted')
        return result['allocation']['slot_token']

    def admin(self, action, *, service=None, **fields):
        service = service or self.service
        return service._handle({'connection': 'admin', 'role': 'admin',
            'message': {'type': 'admin', 'action': action, 'expected_epoch': service.broker.epoch, **fields}})

    def hook(self, connection, project, thread, event, **fields):
        return self.service._handle({'connection': connection, 'role': 'hook',
            'message': {'type': 'hook_event', 'event': event, 'thread_id': thread,
                        'cwd': project['path'], **fields}})

    def test_registry_is_idempotent_longest_root_and_new_generation_after_reenroll(self):
        child = self.a / 'nested'
        child.mkdir()
        nested = self.registry.enroll(child)
        self.assertEqual(self.registry.enroll(self.a), self.pa)
        self.assertEqual(self.registry.resolve(child / 'file')['project_id'], nested['project_id'])
        self.assertEqual(self.registry.resolve(self.b)['project_id'], self.pb['project_id'])
        self.assertIsNone(self.registry.resolve(self.root / 'outside'))
        self.registry.disable(self.a)
        restored = self.registry.enroll(self.a)
        self.assertEqual(restored['project_id'], self.pa['project_id'])
        self.assertNotEqual(restored['generation'], self.pa['generation'])
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

    def test_unknown_project_and_wrong_native_context_cannot_allocate(self):
        unknown = {**self.pa, 'path': str(self.root)}
        with self.assertRaisesRegex(ValueError, 'not_enabled'):
            self.hello('unknown', unknown)
        self.hello('a', self.pa)
        with self.assertRaisesRegex(ValueError, 'project_mismatch'):
            self.acquire('a', self.pb, self.ids[0])
        self.assertEqual(self.service.broker.slots, {})

    def test_two_projects_share_slots_but_cannot_adopt_or_update_each_other(self):
        self.hello('a', self.pa); self.hello('b', self.pb)
        token_a = self.acquire('a', self.pa, self.ids[0])
        self.acquire('b', self.pb, self.ids[1])
        before = deepcopy(self.service.broker.admin_snapshot())
        with self.assertRaisesRegex(ValueError, 'project_mismatch'):
            self.acquire('b', self.pb, self.ids[0])
        result = self.call('b', self.pb, self.ids[1], 'set_slot_state',
                           {'slot_token': token_a, 'state': 'error', 'idempotency_key': 'cross'})
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(len(self.service.broker.slots), 2)
        self.assertEqual(self.service.broker.slots[1].caller.project_id, self.pa['project_id'])
        self.assertEqual(self.service.broker.slots[2].caller.project_id, self.pb['project_id'])
        self.assertEqual(before['slots'][0]['state'], self.service.broker.slots[1].state)

    def test_project_mute_and_release_all_are_scoped(self):
        self.hello('a', self.pa); self.hello('b', self.pb)
        tokens = [self.acquire('a', self.pa, self.ids[0]), self.acquire('b', self.pb, self.ids[1])]
        for connection, project, thread, token in zip(('a', 'b'), (self.pa, self.pb), self.ids, tokens):
            self.call(connection, project, thread, 'set_notification',
                      {'slot_token': token, 'enabled': True, 'idempotency_key': 'notify'})
        result = self.admin('set_project_muted', project_id=self.pa['project_id'], muted=True)
        self.assertEqual(result['status'], 'accepted')
        self.assertTrue(self.service.broker.attention_muted(self.service.broker.slots[1]))
        self.assertFalse(self.service.broker.attention_muted(self.service.broker.slots[2]))
        self.admin('release_tasks', project_id=self.pa['project_id'], all_project=True, idempotency_key='release-all')
        self.assertEqual(set(self.service.broker.owners), {'codex:' + self.ids[1]})
        with self.assertRaisesRegex(ValueError, 'project_mismatch'):
            self.admin('release_tasks', project_id=self.pa['project_id'], thread_ids=[self.ids[1]], idempotency_key='bad-release')

    def test_hook_records_do_not_allocate_and_question_retries_do_not_reopen(self):
        self.hello('hook', self.pa, role='hook')
        question = dict(tool_name='request_user_input_async', tool_use_id='question-1', question_count=1)
        self.hook('hook', self.pa, self.ids[0], 'PreToolUse', **question)
        self.hook('hook', self.pa, self.ids[0], 'PostToolUse', output_kind='accepted', **question)
        self.assertEqual(self.service.broker.slots, {})
        self.hello('a', self.pa)
        self.acquire('a', self.pa, self.ids[0])
        slot = self.service.broker.slots[1]
        notice = slot.native_notifications['question-1']
        self.hook('hook', self.pa, self.ids[0], 'PostToolUse', output_kind='accepted', **question)
        self.assertEqual(slot.native_notifications['question-1'].notification_id, notice.notification_id)
        self.hook('hook', self.pa, self.ids[0], 'PostToolUse', output_kind='answers', **question)
        self.assertEqual(slot.native_notifications['question-1'].status, 'cancelled')
        self.hook('hook', self.pa, self.ids[0], 'PreToolUse', **question)
        self.assertEqual(slot.native_notifications['question-1'].status, 'cancelled')
        rows = self.admin('status')['projects']
        self.assertGreater(next(p for p in rows if p['project_id'] == self.pa['project_id'])['hooks_received'], 0)

    def test_rejected_cross_project_hook_has_no_observation_side_effect(self):
        self.hello('a', self.pa); self.acquire('a', self.pa, self.ids[0])
        self.hello('hook-a', self.pa, role='hook'); self.hello('hook-b', self.pb, role='hook')
        self.hook('hook-a', self.pa, self.ids[0], 'SessionStart')
        records, counters = deepcopy(self.service.hook_records), deepcopy(self.service.hook_counters)
        with self.assertRaisesRegex(ValueError, 'project_mismatch'):
            self.hook('hook-b', self.pb, self.ids[0], 'Stop')
        self.assertEqual(self.service.hook_records, records)
        self.assertEqual(self.service.hook_counters, counters)

    def test_remove_releases_only_project_and_rejects_old_generation(self):
        self.hello('a', self.pa); self.hello('b', self.pb)
        old = self.acquire('a', self.pa, self.ids[0]); self.acquire('b', self.pb, self.ids[1])
        self.admin('remove_project', project_id=self.pa['project_id'])
        self.assertEqual(set(self.service.broker.owners), {'codex:' + self.ids[1]})
        renewed = self.registry.enroll(self.a)
        with self.assertRaisesRegex(ValueError, 'stale_project_generation'):
            self.hello('stale', self.pa)
        self.hello('renewed', renewed)
        new = self.acquire('renewed', renewed, self.ids[0])
        self.assertNotEqual(new, old)

    def test_shared_recovery_requires_same_project_and_surviving_enrollment(self):
        owner = {'kind': 'codex_gui', 'pid': 123, 'started': 'fixture', 'executable': '/fixture/codex', 'tty': '?'}
        secret = 'a' * 64
        self.hello('a', self.pa, recovery_key=secret, recovery_owner=owner)
        token = self.acquire('a', self.pa, self.ids[0])
        fresh = self.new_service()
        wrong = self.hello('b', self.pb, service=fresh, recovery_key=secret, recovery_owner=owner,
                           resume_callers=['codex:' + self.ids[0]])
        self.assertEqual(wrong['restored_tokens'], {})
        right = self.hello('a', self.pa, service=fresh, recovery_key=secret, recovery_owner=owner,
                           resume_callers=['codex:' + self.ids[0]])
        self.assertNotEqual(right['restored_tokens']['codex:' + self.ids[0]], token)
        self.registry.remove(self.pa['project_id']); self.registry.enroll(self.a)
        reenrolled = self.new_service()
        self.assertEqual(reenrolled.recovery.pending, {})


if __name__ == '__main__':
    unittest.main()
