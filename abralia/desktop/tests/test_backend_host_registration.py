# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from abralia.backend.cli import main
from abralia.backend.core import BrokerConfig
from abralia.backend.ipc import BrokerClient
from abralia.backend.service import BrokerService
from abralia.backend.task_catalog import codex_project_tasks

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
OWNER = {'kind':'codex_gui', 'pid':1234, 'parent':1, 'started':'fixture',
         'tty':'??', 'executable':'/Applications/Codex.app/Contents/MacOS/Codex'}


class HostRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-host-registration-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'codex'
        self.home.mkdir()
        self.endpoint = self.root / 'broker.sock'
        with contextlib.closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, source TEXT, archived INTEGER)')
            for n in range(1,26):
                db.execute('INSERT INTO threads VALUES (?,?,?,?,?,0)',
                           (self.task(n), f'Task {n}', 'PRIVATE PROMPT MUST NOT BE READ', str(self.project), 'vscode'))
            db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?)', [
                (self.task(26), 'Foreign', 'private', str(self.root/'other'), 'vscode', 0),
                (self.task(27), 'Internal review', 'PRIVATE TRANSCRIPT', str(self.project), '{"subagent":{"other":"guardian"}}', 0),
                (self.task(28), 'Archived', 'private', str(self.project), 'vscode', 1),
                (self.task(29), None, 'PRIVATE PROMPT MUST NOT BE A LABEL', str(self.project), 'cli', 0)])
        self.clients = []
        self.service = self.start_service()
        self.admin = self.client(role='admin')
        self.agent = self.client(recover_slots=True, recovery_owner=OWNER)

    @staticmethod
    def task(n):
        return str(UUID(int=n))

    def start_service(self):
        return BrokerService(self.project, PROFILE, endpoint=self.endpoint, codex_home=self.home,
                             config=BrokerConfig(disconnect_grace_seconds=1),
                             recovery_owner_check=lambda owner: owner == OWNER).start()

    def client(self, **kwargs):
        client = BrokerClient(self.project, endpoint=self.endpoint, **kwargs)
        self.clients.append(client)
        return client

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.service.close()
        self.temp.cleanup()

    def command(self, action, ids=None, all_project=False, client=None):
        return (client or self.admin).request({'type':'admin', 'action':action,
            'thread_ids':ids, 'all_project':all_project, 'idempotency_key':str(uuid4())})

    def test_retried_release_does_not_release_new_allocation_and_stale_epoch_is_rejected(self):
        self.command('register_tasks',[self.task(1)])
        message={'type':'admin', 'action':'release_tasks', 'thread_ids':[self.task(1)],
                 'idempotency_key':'one-release'}
        self.assertEqual(self.admin.request(message)['status'],'accepted')
        self.command('register_tasks',[self.task(1)])
        self.assertTrue(self.admin.request(message)['replayed'])
        self.assertEqual(len(self.service.broker.slots),1)
        conflict={**message,'thread_ids':[self.task(2)]}
        self.assertEqual(self.admin.request(conflict)['reason'],'idempotency_conflict')
        self.service.close()
        self.admin._close()
        self.service=self.start_service()
        self.command('register_tasks',[self.task(1)])
        self.assertEqual(self.admin.request(message)['reason'],'stale_backend_epoch')
        self.assertEqual(len(self.service.broker.slots),1)

    def call(self, n, op, client=None, **args):
        return (client or self.agent).request({'type':'call', 'operation':op,
            'metadata':{'thread_id':self.task(n)}, 'arguments':args})

    def acquire(self, n, client=None):
        return self.call(n, 'acquire_slot', client=client, label=f'Live {n}',
                         harness='codex_desktop', idempotency_key='acquire')

    def test_catalog_excludes_other_projects_internal_and_archived_and_never_uses_prompts(self):
        tasks = codex_project_tasks(self.project, codex_home=self.home)
        self.assertEqual(len(tasks),26)
        self.assertTrue({self.task(26),self.task(27),self.task(28)}.isdisjoint(t['thread_id'] for t in tasks))
        self.assertNotIn('PRIVATE',json.dumps(tasks))
        self.assertTrue(tasks[-1]['label'].startswith('Codex task '))

    def test_bulk_register_is_idempotent_quiet_and_does_not_claim_agent_liveness(self):
        first = self.command('register_tasks', all_project=True)
        self.assertEqual((first['status'],first['allocated_slots'],first['page_count']),('accepted',26,3))
        again = self.command('register_tasks', all_project=True)
        self.assertFalse(any(t['created'] for t in again['tasks']))
        for a,b in zip(first['tasks'],again['tasks']):
            self.assertEqual((a['slot_id'],a['identity_color']),(b['slot_id'],b['identity_color']))
        self.assertNotIn('slot_token',json.dumps(first))
        self.assertTrue(all(not s.agent_attached and s.host_registered for s in self.service.broker.slots.values()))
        self.assertIsNone(self.service.broker.current_call)
        self.assertFalse(self.service.connections[next(iter(self.service.connections))])

    def test_invalid_batch_is_atomic_and_agent_connection_cannot_administer(self):
        for bad in (self.task(26),self.task(27),self.task(28),'not-a-uuid'):
            result = self.command('register_tasks',[self.task(1),bad])
            self.assertEqual(result['status'],'rejected')
            self.assertFalse(self.service.broker.slots)
        result = self.command('register_tasks',[self.task(1)],client=self.agent)
        self.assertEqual(result['status'],'rejected')
        self.command('register_tasks',[self.task(1)])
        self.assertEqual(self.command('release_tasks',all_project=True,client=self.agent)['status'],'rejected')
        self.assertEqual(len(self.service.broker.slots),1)

    def test_existing_agent_is_preserved_and_host_slot_is_adopted_by_its_owner(self):
        existing = self.acquire(1)['allocation']
        self.call(1,'set_slot_state',slot_token=existing['slot_token'],state='progressing',idempotency_key='progress')
        registered = self.command('register_tasks',[self.task(1),self.task(2)])['tasks']
        self.assertFalse(registered[0]['created'])
        self.assertEqual(registered[0]['registration_source'],'agent')
        self.assertEqual(self.service.broker.slots[1].state,'progressing')
        own = self.call(2,'get_status')['allocation']
        self.assertFalse(own['agent_attached'])
        self.assertFalse(own['agent_connected'])
        self.assertNotIn('slot_token',own)
        other = self.call(3,'set_slot_state',slot_token=self.service.broker.slots[2].slot_token,
                          state='error',idempotency_key='not-owner')
        self.assertEqual(other['status'],'rejected')
        adopted = self.acquire(2)['allocation']
        self.assertTrue(adopted['agent_attached'])
        self.assertTrue(adopted['agent_connected'])
        self.assertEqual((adopted['slot_id'],adopted['identity_color']),
                         (registered[1]['slot_id'],registered[1]['identity_color']))
        self.assertEqual(self.service.broker.slots[1].slot_token,existing['slot_token'])

    def test_host_registration_survives_admin_and_agent_connection_cleanup(self):
        self.command('register_tasks',[self.task(1)])
        self.acquire(1)
        self.agent.close()
        self.admin.close()
        time.sleep(1.1)
        admin = self.client(role='admin')
        status = admin.request({'type':'admin','action':'status'})
        self.assertEqual(len(status['slots']),1)
        self.assertFalse(status['slots'][0]['agent_connected'])

    def test_release_specific_and_all_clear_calls_controls_and_do_not_resurrect_on_reconnect(self):
        self.command('register_tasks',[self.task(1),self.task(2)])
        slot = self.acquire(1)['allocation']
        self.call(1,'report_question',slot_token=slot['slot_token'],question_id='q',kind='free_text',
                  options=[],allow_other=True,idempotency_key='question')
        self.assertTrue(self.command('release_tasks',[self.task(1)])['tasks'][0]['released'])
        self.assertEqual(list(self.service.broker.slots),[2])
        self.assertFalse(self.command('release_tasks',[self.task(1)])['tasks'][0]['released'])
        self.command('release_tasks', all_project=True)
        self.assertFalse(self.service.broker.slots)
        self.assertIsNone(self.service.broker.current_call)
        self.assertIsNone(self.service.broker.attention_target)
        self.service.close()
        self.agent._close()
        self.service = self.start_service()
        self.agent.request({'type':'ping'})
        self.assertFalse(self.service.broker.slots)
        self.assertEqual(self.call(1,'set_slot_state',slot_token=slot['slot_token'],state='error',idempotency_key='stale')['status'],'rejected')

    def test_release_all_clears_pending_recovery_and_storage_failure_rolls_back(self):
        self.acquire(1)
        self.service.close()
        self.agent._close()
        self.admin._close()
        self.service = self.start_service()
        self.assertTrue(self.service.recovery.pending)
        with patch('abralia.backend.recovery.os.replace',side_effect=OSError('fixture')):
            failed = self.command('release_tasks',all_project=True)
        self.assertEqual(failed['reason'],'recovery_storage_unavailable')
        self.assertTrue(self.service.recovery.pending)
        self.service.recovery.error = None
        self.assertEqual(self.command('release_tasks',all_project=True)['status'],'accepted')
        self.assertFalse(self.service.recovery.pending)
        self.agent.request({'type':'ping'})
        self.assertFalse(self.service.broker.slots)

    def test_host_registration_preserves_pending_recovery_position_and_bridge_adoption(self):
        original = self.acquire(1)['allocation']
        self.service.close()
        self.agent._close()
        self.admin._close()
        self.service = self.start_service()
        registered = self.command('register_tasks',[self.task(1)])['tasks'][0]
        self.assertEqual((registered['slot_id'],registered['identity_color']),
                         (original['slot_id'],original['identity_color']))
        self.assertFalse(registered['agent_attached'])
        self.agent.request({'type':'ping'})
        self.assertTrue(self.service.broker.slots[1].agent_attached)
        self.assertNotEqual(self.service.broker.slots[1].slot_token,original['slot_token'])

    def test_cli_bulk_commands_use_admin_channel_and_print_no_ownership_tokens(self):
        for command in ('register-tasks','release-tasks'):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                result=main([command,'--project',str(self.project),'--socket',str(self.endpoint),'--all'])
            self.assertEqual(result,0)
            self.assertNotIn('slot_token',output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['status'],'accepted')
        self.assertFalse(self.service.broker.slots)


if __name__ == '__main__': unittest.main()
