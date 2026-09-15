# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Idle/locked clients retain slots; real closure and revocation still clean up."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import BrokerConfig
from abralia.backend.ipc import BrokerClient, JsonLineReader
from abralia.backend.project_registry import ProjectRegistry
from abralia.backend.service import BrokerService

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
THREAD = str(UUID(int=1001))
OWNER = {'kind':'codex_gui','pid':1234,'parent':1,'started':'fixture lifetime',
         'tty':'??','executable':'/Applications/Codex.app/Contents/MacOS/Codex'}


class JsonLineReaderTests(unittest.TestCase):
    def setUp(self):
        self.server, self.client = socket.socketpair()
        self.addCleanup(self.server.close)
        self.addCleanup(self.client.close)
        self.stop = threading.Event()
        self.reader = JsonLineReader(self.server, self.stop, frame_timeout=.05)

    def test_idle_agent_waits_beyond_frame_timeout_then_reads_normally(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.reader.readline)
            self.stop.wait(.15)
            self.assertFalse(future.done())
            self.client.sendall(b'{"type":"ping"}\n')
            self.assertEqual(future.result(timeout=1), b'{"type":"ping"}\n')

    def test_handshake_idle_and_incomplete_frame_are_still_bounded(self):
        with self.assertRaises(TimeoutError):
            self.reader.readline(idle_timeout=.03)
        self.client.sendall(b'{"type":')
        with self.assertRaises(TimeoutError):
            self.reader.readline()

    def test_pipelined_frames_do_not_get_stranded_in_a_buffer(self):
        self.client.sendall(b'{"type":"hello"}\n{"type":"ping"}\n')
        self.assertEqual(self.reader.readline(idle_timeout=.1), b'{"type":"hello"}\n')
        self.assertEqual(self.reader.readline(idle_timeout=.1), b'{"type":"ping"}\n')

    def test_request_size_and_partial_eof_remain_rejected(self):
        with patch('abralia.backend.ipc.MAX_REQUEST', 64):
            self.client.sendall(b'x'*64)
            with self.assertRaisesRegex(ValueError, 'request_too_large'):
                self.reader.readline()
        self.reader.buffer.clear()
        self.client.sendall(b'{')
        self.client.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(ValueError, 'incomplete_request'):
            self.reader.readline()

    def test_shutdown_interrupts_an_idle_reader(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.reader.readline)
            self.stop.set()
            self.assertEqual(future.result(timeout=1), b'')


class ConnectionLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-lock-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.registry = ProjectRegistry(self.root/'control')
        self.row = self.registry.enroll(self.project)
        self.read_timeout = patch('abralia.backend.service.IPC_READ_TIMEOUT', .2)
        self.read_timeout.start()
        self.addCleanup(self.read_timeout.stop)
        self.service = BrokerService(self.root, PROFILE, shared=True, mode='simulated',
            config=BrokerConfig(disconnect_grace_seconds=1), endpoint=self.root/'broker.sock',
            state_dir=self.root/'control', codex_home=self.root/'codex',
            recovery_owner_check=lambda owner: owner == OWNER).start()
        self.addCleanup(self.service.close)
        self.counter = 0

    def client(self, recover=True):
        client = BrokerClient(self.project, endpoint=self.service.endpoint, recover_slots=recover,
                              recovery_owner=OWNER, project_generation=self.row['generation'])
        self.addCleanup(client.close)
        return client

    def call(self, client, operation, **args):
        self.counter += 1
        if operation != 'get_status':
            args.setdefault('idempotency_key', str(self.counter))
        return client.request({'type':'call','operation':operation,'arguments':args,
                               'metadata':{'thread_id':THREAD,'cwd':str(self.project)}})

    def acquire(self, client):
        result = self.call(client, 'acquire_slot', label='Lock fixture', harness='codex_desktop')
        self.assertEqual(result['status'], 'accepted', result)
        return result['allocation']

    def until(self, predicate):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Expected connection lifecycle event did not occur')

    def test_lock_like_silence_and_old_heartbeat_preserve_the_same_open_connection(self):
        for recover in (False, True):
            with self.subTest(recovery_proof=recover):
                client = self.client(recover)
                original = self.acquire(client)
                stream = client.socket
                # Simulate a large heartbeat-age jump plus no traffic for longer
                # than the former socket timeout and disconnect grace combined.
                for connection in list(self.service.last_seen):
                    self.service.last_seen[connection] = time.monotonic()-3600
                time.sleep(1.4)
                self.assertIn(original['slot_id'], self.service.broker.slots)
                current = self.call(client, 'get_status')['allocation']
                self.assertIs(client.socket, stream)
                self.assertEqual((current['slot_token'],current['f_key'],current['identity_color']),
                                 (original['slot_token'],original['f_key'],original['identity_color']))
                self.assertFalse(self.service.broker.disconnected)
                self.call(client, 'release_slot', slot_token=current['slot_token'])
                client.close()

    def test_actual_peer_close_releases_after_grace_and_records_reason(self):
        client = self.client()
        self.acquire(client)
        client.close()
        self.until(lambda: not self.service.broker.slots)
        events = list(self.service.broker.events)
        self.assertTrue(any(e['kind']=='agent_connection_lost' and e['reason']=='peer_closed' for e in events))
        self.assertEqual([e['reason'] for e in events if e['kind']=='slot_released'], ['connection_lost'])

    def test_stale_connection_reconnects_with_proof_without_replaying_a_muted_call(self):
        client = self.client()
        original = self.acquire(client)
        self.call(client, 'set_notification', slot_token=original['slot_token'], enabled=True)
        slot = self.service.broker.slots[original['slot_id']]
        self.service.broker.set_active(True)
        self.service.broker.act_on_call('mute', slot.slot_token, slot.notification.notification_id)
        notice_id = slot.notification.notification_id
        connection = next(iter(self.service.connections))
        self.service.submit({'connection':connection,'message':{'type':'disconnect'}})
        expired = client.request({'type':'ping'})
        self.assertEqual(expired['reason'], 'connection_expired')
        self.assertIsNone(client.socket)
        resumed = client.request({'type':'ping'})
        self.assertEqual(resumed['status'], 'accepted')
        current = self.call(client, 'get_status')['allocation']
        self.assertEqual(current['slot_token'], original['slot_token'])
        self.assertEqual(current['notification']['id'], notice_id)
        self.assertEqual(current['notification']['status'], 'muted')
        self.assertFalse(self.service.broker.disconnected)

    def test_project_disable_is_not_treated_as_a_reconnectable_expiry(self):
        client = self.client()
        self.acquire(client)
        stream = client.socket
        self.registry.remove(self.project)
        refused = client.request({'type':'ping'})
        self.assertEqual(refused['reason'], 'project_not_enabled')
        self.assertIs(client.socket, stream)
        self.assertFalse(self.service.broker.slots)
        self.assertIsNone(self.registry.resolve(self.project))
        self.assertEqual([e['reason'] for e in self.service.broker.events if e['kind']=='slot_released'],
                         ['project_disabled_or_replaced'])

    def test_release_is_not_resurrected_by_a_later_handshake(self):
        client = self.client()
        original = self.acquire(client)
        self.call(client, 'release_slot', slot_token=original['slot_token'])
        with client.lock:
            client._close()
        self.assertEqual(client.request({'type':'ping'})['status'], 'accepted')
        self.assertNotIn('allocation', self.call(client, 'get_status'))
        self.assertEqual([e['reason'] for e in self.service.broker.events if e['kind']=='slot_released'],
                         ['explicit_release'])


if __name__ == '__main__':
    unittest.main()
