# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver
from abralia.backend.service import BrokerService
from abralia.backend.terminal_focus import public_result

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
OWNER = {'pid': 123, 'parent': 12, 'started': 'Mon Sep 14 01:39:11 2026',
         'tty': 'ttys002', 'executable': '/bin/codex', 'kind': 'codex_tui'}
CONTEXT = {'owner': OWNER, 'provider': 'ghostty', 'environment': {}}


def allocate(broker, number=1):
    caller = Caller(f'codex:{UUID(int=number)}', str(UUID(int=number)), surface='codex_cli')
    broker.call(caller, 'acquire_slot', {'label': 'CLI', 'idempotency_key': 'a'})
    return broker.slots[broker.owners[caller.caller_id]]


class TerminalRoutingTests(unittest.TestCase):
    def service(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        service = BrokerService(temporary.name, PROFILE, endpoint=Path(temporary.name) / 'backend.sock')
        service.connections['one'] = set()
        service.connection_terminals['one'] = copy.deepcopy(CONTEXT)
        service.terminal_task_sources.update({str(UUID(int=i)): 'cli' for i in (1, 2)})
        return service

    def test_native_root_attaches_but_subagent_does_not_inherit_pane(self):
        service = self.service()
        root, child = allocate(service.broker), allocate(service.broker, 2)
        service.terminal_task_sources[child.caller.thread_id] = 'subagent'
        with patch('abralia.backend.codex_context.native_task_source', side_effect=['cli', 'subagent']):
            service._attach_terminal(root, 'one')
            service._attach_terminal(child, 'one')
        self.assertTrue(service.broker.navigation_target(root)['available'])
        self.assertFalse(service.broker.navigation_target(child)['available'])
        service._drop_connection('one')
        self.assertFalse(service.broker.navigation_target(root)['available'])

    def test_two_roots_in_same_cli_cannot_redirect_each_other_by_call_order(self):
        service = self.service()
        first, second = allocate(service.broker), allocate(service.broker, 2)
        with patch('abralia.backend.codex_context.native_task_source', return_value='cli'):
            service._attach_terminal(first, 'one')
            service._attach_terminal(second, 'one')
            service._attach_terminal(first, 'one')
        self.assertFalse(service.broker.navigation_target(first)['available'])
        self.assertFalse(service.broker.navigation_target(second)['available'])
        service.broker._release(first)
        service._attach_terminal(second, 'one')
        self.assertTrue(service.broker.navigation_target(second)['available'])

    def test_two_live_clients_for_one_task_stay_ambiguous_until_disconnect(self):
        service = self.service()
        slot = allocate(service.broker)
        service.connections['two'] = set()
        service.connection_terminals['two'] = copy.deepcopy(CONTEXT)
        service.connection_terminals['two']['owner']['pid'] = 456
        with patch('abralia.backend.codex_context.native_task_source', return_value='cli'):
            service._attach_terminal(slot, 'one')
            service._attach_terminal(slot, 'two')
            service._attach_terminal(slot, 'one')
            self.assertFalse(service.broker.navigation_target(slot)['available'])
            service._drop_connection('two')
            service._attach_terminal(slot, 'one')
        self.assertTrue(service.broker.navigation_target(slot)['available'])

    def test_vscode_roots_can_share_explicit_window_only_target(self):
        service = self.service()
        service.connection_terminals['one']['provider'] = 'vscode'
        first, second = allocate(service.broker), allocate(service.broker, 2)
        service._attach_terminal(first, 'one')
        service._attach_terminal(second, 'one')
        for slot in (first, second):
            result = service.broker.navigation_target(slot)
            self.assertTrue(result['available'])
            self.assertEqual(result['specificity'], 'window')

    def test_reloading_identical_bridge_keeps_surviving_attachment(self):
        service = self.service()
        slot = allocate(service.broker)
        service.connections['two'] = {slot.caller.caller_id}
        service.connection_terminals['two'] = copy.deepcopy(CONTEXT)
        service._attach_terminal(slot, 'one')
        service._attach_terminal(slot, 'two')
        service._drop_connection('one')
        self.assertTrue(service.broker.navigation_target(slot)['available'])
        self.assertEqual(service.broker.terminal_attachments[slot.caller.caller_id]['connection'], 'two')

    def test_release_destroys_attachment_and_keeps_new_occupant_unbound(self):
        service = self.service()
        slot = allocate(service.broker)
        with patch('abralia.backend.codex_context.native_task_source', return_value='cli'):
            service._attach_terminal(slot, 'one')
        service.broker._release(slot)
        replacement = allocate(service.broker, 2)
        self.assertFalse(service.broker.navigation_target(replacement)['available'])

    def test_direct_slot_and_pickup_both_enqueue_attached_cli(self):
        service = self.service()
        b = service.broker
        slot = allocate(b)
        with patch('abralia.backend.codex_context.native_task_source', return_value='cli'):
            service._attach_terminal(slot, 'one')
        b.set_active(True)
        b.select_slot(slot.slot_token)
        self.assertEqual(list(b.focus_requests), [(slot.slot_token, slot.caller.thread_id)])
        b.call(slot.caller, 'set_notification', {'slot_token': slot.slot_token, 'enabled': True,
                                                'idempotency_key': 'notify'})
        b.act_on_call('pickup', slot.slot_token, slot.notification.notification_id)
        self.assertEqual(list(b.focus_requests), [(slot.slot_token, slot.caller.thread_id)])
        driver = DeviceDriver(b, PROFILE, 'simulated')
        with patch('abralia.backend.device.subprocess.Popen') as process:
            driver.tick()
            process.assert_not_called()
        self.assertEqual(b.events[-1]['kind'], 'focus_simulated')

    def test_native_focus_runs_outside_hid_worker_and_discards_stale_result(self):
        service = self.service()
        b = service.broker
        slot = allocate(b)
        with patch('abralia.backend.codex_context.native_task_source', return_value='cli'):
            service._attach_terminal(slot, 'one')
        b.set_active(True)
        b.select_slot(slot.slot_token)
        attachment = b.terminal_attachments[slot.caller.caller_id]
        driver = DeviceDriver(b, PROFILE, 'hardware')
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 5678
        with patch('abralia.backend.device.subprocess.Popen', return_value=process) as opener:
            driver._start_terminal_focus(slot, attachment)
            self.assertIn('abralia.backend.terminal_focus', opener.call_args.args[0])
            process.wait.assert_not_called()
            process.communicate.assert_not_called()
        b.focus_revision += 1
        with patch('abralia.backend.device.os.killpg'), patch('threading.Thread'):
            driver._service_terminal_focus()
        self.assertIsNone(driver.terminal_focus_job)
        self.assertNotIn(slot.slot_token, b.navigation_results)

    def test_focus_result_is_bounded_and_does_not_expose_native_diagnostics(self):
        result = public_result({'status': 'focused', 'provider': 'ghostty',
                                'title': 'private conversation', 'socket_path': '/private/socket',
                                'environment': {'TOKEN': 'secret'}, 'reason': 'x' * 1000})
        self.assertEqual(result, {'status': 'focused', 'provider': 'ghostty'})


if __name__ == '__main__':
    unittest.main()
