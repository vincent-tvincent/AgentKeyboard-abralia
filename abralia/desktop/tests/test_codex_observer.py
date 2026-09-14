# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.codex_observer import CodexObserver, JsonlCursor, RolloutTail, configured_hook_journal
from abralia.backend.ipc import BrokerClient
from abralia.backend.service import BrokerService
from abralia.backend.device import routes_for
from abralia.rgb import load_profile

THREAD = str(UUID(int=71))
PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


def record(kind, **payload):
    return {'type': kind, 'payload': payload}


def append(path, *rows):
    with path.open('a') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')


def question_records(request='q1'):
    return [record('response_item', type='function_call', name='request_user_input_async',
                   call_id=request, arguments=json.dumps({'questions': [{'title': 'PRIVATE PROMPT'}]})),
            record('response_item', type='function_call_output', call_id=request, output='{"accepted":true}')]


def reply_record(request='q1'):
    text = '<send_user_message_question_reply>' + json.dumps([{
        'questionItemId': json.dumps(['request_user_input_async', request, 0]),
        'answer': 'PRIVATE ANSWER'}]) + '</send_user_message_question_reply>'
    return record('response_item', type='message', role='user', content=[{'type': 'input_text', 'text': text}])


class AutomaticQuestionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(BrokerConfig(question_seconds=20, notification_seconds=10), clock=lambda: self.now)
        self.owner = Caller('codex:' + THREAD, THREAD, 'codex_metadata', 'codex_desktop')
        self.serial = 0
        self.token = self.call('acquire_slot', label='fixture')['allocation']['slot_token']
        self.slot = self.b.slots[1]
        self.b.set_active(True)

    def call(self, op, **args):
        self.serial += 1
        result = self.b.call(self.owner, op, {'idempotency_key': str(self.serial), **args})
        self.assertEqual(result['status'], 'accepted', result)
        return result

    def observe(self, request='q1', stage='accepted', **extra):
        value = {'thread_id': THREAD, 'execution': 'running', 'turn_id': 't1', 'mode': 'default',
                 'questions': [{'request_id': request, 'turn_id': 't1', 'tool': 'request_user_input_async', 'stage': stage}], **extra}
        self.assertTrue(self.b.observe_codex(self.token, value))

    def physical(self, action):
        slot = self.b.pending_target()
        self.assertIsNotNone(slot)
        self.b.act_on_call(action, slot.slot_token, slot.notification.notification_id)

    def test_acceptance_not_invocation_notifies_without_changing_reported_state(self):
        self.call('set_slot_state', slot_token=self.token, state='progressing', progress=.3)
        self.observe(stage='invoked')
        self.assertIsNone(self.b.current_call)
        self.observe()
        self.assertEqual(self.b.current_call, 1)
        self.assertEqual(self.slot.notification.origin, 'codex')
        self.assertEqual((self.slot.state, self.slot.progress), ('progressing', .3))
        self.call('set_slot_state', slot_token=self.token, state='completed')
        self.assertIn('q1', self.slot.native_requests)

    def test_native_and_agent_notice_withdrawals_are_independent(self):
        self.observe()
        self.call('set_notification', slot_token=self.token, enabled=True)
        manual = self.slot.agent_notification
        self.call('set_notification', slot_token=self.token, enabled=False)
        self.assertEqual(self.slot.notification.origin, 'codex')
        self.assertEqual(self.b.current_call, 1)
        self.now = 6
        self.call('set_notification', slot_token=self.token, enabled=True)
        manual = self.slot.agent_notification
        self.observe(stage='response_received')
        self.assertEqual(manual.status, 'active')
        self.assertIs(self.slot.notification, manual)

    def test_poll_retries_do_not_restart_or_unmute(self):
        self.observe()
        notice = self.slot.notification
        self.physical('mute')
        self.now = 2
        self.observe(last_hook={'hook_event_name': 'PostToolUse'})
        self.assertIs(self.slot.notification, notice)
        self.assertEqual((notice.status, notice.started_at, notice.expires_at), ('muted', 0, 10))

    def test_picked_native_question_holds_display_against_queued_manual_calls(self):
        self.observe()
        notice = self.slot.notification
        self.physical('pickup')
        self.call('set_notification', slot_token=self.token, enabled=True)
        self.now = 2
        self.observe(last_hook={'hook_event_name': 'PreToolUse'})
        self.b.step()
        self.assertIs(self.slot.notification, notice)
        self.assertEqual(self.slot.agent_notification.status, 'queued')
        self.assertIsNone(self.b.current_call)
        self.assertFalse(self.slot.question)  # No guessed native answer shortcuts.
        self.assertFalse(any(route.action == 'confirm_candidate' for route in routes_for(self.b, load_profile(PROFILE)).values()))
        self.observe(stage='response_received')
        self.assertIsNone(self.b.selected)
        self.assertIsNone(self.b.background_slot)
        self.assertFalse(self.b.focus_requests)
        self.assertEqual(self.slot.agent_notification.status, 'active')

    def test_escape_preserves_native_question_without_rearming_call_controls(self):
        self.observe(); self.physical('pickup')
        self.assertTrue(self.b.exit_focus(self.token))
        self.assertEqual(self.slot.notification.status, 'muted')
        self.assertIsNone(self.b.pending_target())
        self.b.select_slot(self.token)
        self.assertEqual(self.b.selected, 1)
        self.assertEqual(self.slot.notification.status, 'picked_up')
        self.assertIsNone(self.b.pending_target())
        self.assertTrue(self.b.focus_requests)
        self.observe(stage='response_received')
        self.assertIsNone(self.b.selected)

    def test_mute_and_pickup_release_call_keys_until_a_new_request(self):
        for action in ('mute', 'pickup'):
            with self.subTest(action=action):
                self.setUp()
                self.observe()
                notice = self.slot.notification
                self.physical(action)
                self.assertTrue(notice.controls_dismissed)
                self.assertFalse(self.b.has_attention())
                self.assertIsNone(self.b.pending_target())
                self.assertFalse({30, 31} & routes_for(self.b, load_profile(PROFILE)).keys())
                self.b.select_slot(self.token)
                self.observe(last_hook={'hook_event_name':'PostToolUse'})
                self.now = 11; self.b.step()  # Attention timeout cannot rearm it.
                self.assertIsNone(self.b.pending_target())
                self.assertIn('q1', self.slot.native_requests)
                self.observe(stage='response_received')
                self.observe(request='q2')
                self.assertEqual(self.b.pending_target().notification.request_id, 'q2')
                self.assertTrue({30, 31} <= routes_for(self.b, load_profile(PROFILE)).keys())
                # A late old pickup cannot acknowledge the new request.
                self.b.act_on_call('pickup', self.token, notice.notification_id)
                self.assertEqual(self.slot.notification.status, 'active')

    def test_local_timeout_restores_focus_but_is_not_a_native_answer(self):
        self.observe(); self.physical('pickup')
        self.now = 21
        self.b.step()
        self.assertFalse(self.slot.native_requests)
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.has_attention())
        self.observe(last_hook={'hook_event_name': 'PreToolUse'})
        self.assertFalse(self.slot.native_requests)
        self.assertEqual(self.slot.observation['questions'][0]['stage'], 'accepted')
        self.assertIn('local_timeout', [e.get('outcome') for e in self.b.events])

    def test_notification_timeout_does_not_expire_pending_question(self):
        self.observe(); self.now = 11; self.b.step()
        self.assertEqual(self.slot.notification.status, 'expired')
        self.physical('pickup')
        self.assertIn('q1', self.slot.native_requests)
        self.observe(stage='turn_ended_unconfirmed', execution='idle')
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.slot.native_requests)

    def test_stale_allocation_and_other_thread_cannot_receive_observation(self):
        self.assertFalse(self.b.observe_codex(self.token, {'thread_id': str(UUID(int=72))}))
        self.call('release_slot', slot_token=self.token)
        fresh = self.call('acquire_slot', label='rejoin')['allocation']['slot_token']
        self.assertNotEqual(self.token, fresh)
        self.assertFalse(self.b.observe_codex(self.token, {'thread_id': THREAD}))
        self.assertIsNone(self.b.slots[1].observation)

    def test_existing_manual_question_avoids_a_second_onset(self):
        self.call('report_question', slot_token=self.token, question_id='manual', kind='free_text')
        notice = self.slot.notification
        self.observe()
        self.assertIs(self.slot.notification, notice)
        self.assertFalse(self.slot.native_notifications)
        self.call('clear_question', slot_token=self.token, question_id='manual', outcome='withdrawn')
        self.observe(last_hook={'hook_event_name': 'PostToolUse'})
        self.assertFalse(self.slot.native_requests)
        self.assertFalse(self.slot.native_notifications)
        self.assertIsNone(self.b.current_call)

    def test_plan_blocking_question_is_provisional_and_rejection_cleans_it(self):
        self.observe(stage='invoked', mode='plan', questions=[{
            'request_id':'q1', 'turn_id':'t1', 'tool':'request_user_input', 'stage':'invoked'}])
        self.assertEqual(self.b.current_call, 1)
        self.observe(stage='tool_ended_without_acceptance')
        self.assertFalse(self.slot.native_requests)
        self.assertIsNone(self.b.current_call)

    def test_withdrawing_legacy_attention_keeps_picked_question_hold(self):
        self.call('report_question', slot_token=self.token, question_id='manual', kind='free_text')
        self.physical('pickup')
        other = Caller('codex:' + str(UUID(int=72)), str(UUID(int=72)))
        acquired = self.b.call(other, 'acquire_slot', {'label':'other','idempotency_key':'acquire'})
        self.b.call(other, 'set_notification', {'slot_token':acquired['allocation']['slot_token'],
                                              'enabled':True,'idempotency_key':'notice'})
        self.call('set_notification', slot_token=self.token, enabled=False)
        self.assertEqual(self.b.selected, 1)
        self.assertIsNone(self.b.current_call)
        self.assertTrue(self.slot.question.picked_up)


class AutomaticCompletionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(BrokerConfig(notification_seconds=10), clock=lambda: self.now)
        self.owner = Caller('codex:' + THREAD, THREAD, 'codex_metadata', 'codex_desktop')
        result = self.b.call(self.owner, 'acquire_slot', {'label':'completion fixture','idempotency_key':'acquire'})
        self.token = result['allocation']['slot_token']
        self.slot = self.b.slots[1]
        self.b.set_active(True)

    def observe(self, ids=('t1',), **extra):
        value = {'source':'codex_observer','thread_id':THREAD,'execution':'idle','turn_id':'t1',
                 'questions':[],'error':None,'completed_turn_ids':list(ids),**extra}
        return self.b.observe_codex(self.token, value)

    def test_native_turn_notice_keeps_agent_semantic_state_and_identity(self):
        self.b.call(self.owner,'set_slot_state',{'slot_token':self.token,'state':'progressing',
                    'progress':.4,'idempotency_key':'working'})
        color = self.slot.identity_color
        self.assertTrue(self.observe())
        notice = self.slot.completion_notifications['t1']
        self.assertEqual((notice.origin,notice.request_id,notice.status), ('codex_turn','t1','active'))
        self.assertIs(self.b.pending_target(), self.slot)
        self.assertEqual((self.slot.state,self.slot.progress,self.slot.identity_color), ('progressing',.4,color))
        self.assertIsNone(self.slot.agent_notification)
        self.assertFalse(self.slot.native_requests)

    def test_retry_after_mute_or_pickup_never_rearms_the_same_completion(self):
        for action in ('mute','pickup'):
            with self.subTest(action=action):
                self.setUp()
                self.observe()
                notice = self.slot.completion_notifications['t1']
                identity, started, expires = notice.notification_id, notice.started_at, notice.expires_at
                self.b.act_on_call(action,self.token,identity)
                self.now = 2
                self.observe(last_hook={'hook_event_name':'PostToolUse'})
                self.observe(source='codex_hook',last_hook={'hook_event_name':'Stop'})
                self.observe()
                self.assertEqual(len(self.slot.completion_notifications),1)
                self.assertIs(self.slot.completion_notifications['t1'],notice)
                self.assertEqual((notice.notification_id,notice.started_at,notice.expires_at), (identity,started,expires))
                self.assertTrue(notice.controls_dismissed)
                self.assertIsNone(self.b.pending_target())
                self.now = 11; self.b.step(); self.observe(last_event_at='same native completion')
                self.assertIsNone(self.b.pending_target())

    def test_explicit_agent_notification_and_completion_have_independent_lifecycles(self):
        self.b.call(self.owner,'set_notification',{'slot_token':self.token,'enabled':True,'idempotency_key':'agent-call'})
        manual = self.slot.agent_notification
        self.observe()
        completion = self.slot.completion_notifications['t1']
        self.assertIsNot(manual, completion)
        self.b.call(self.owner,'set_notification',{'slot_token':self.token,'enabled':False,'idempotency_key':'withdraw'})
        self.assertEqual(manual.status, 'cancelled')
        self.assertEqual(completion.status, 'active')
        self.assertIs(self.b.pending_target().notification, completion)

    def test_hook_idle_error_and_stale_allocation_do_not_create_completion_attention(self):
        self.observe((),execution='running')
        self.observe((),execution='idle')
        self.assertIsNone(self.slot.notification)
        self.observe(source='codex_hook',last_hook={'hook_event_name':'Stop'})
        self.observe(error='rollout_unavailable_or_invalid')
        self.assertFalse(self.slot.completion_notifications)
        self.assertFalse(self.b.observe_codex(self.token,{'source':'codex_observer',
            'thread_id':str(UUID(int=72)),'completed_turn_ids':['t1']}))
        self.b.call(self.owner,'release_slot',{'slot_token':self.token,'idempotency_key':'release'})
        result = self.b.call(self.owner,'acquire_slot',{'label':'fresh','idempotency_key':'rejoin'})
        self.assertNotEqual(result['allocation']['slot_token'],self.token)
        self.assertFalse(self.observe())
        self.assertIsNone(self.b.slots[1].notification)

    def test_next_turn_completion_is_new_and_old_pickup_cannot_consume_it(self):
        self.observe()
        first = self.slot.completion_notifications['t1']
        self.b.act_on_call('mute',self.token,first.notification_id)
        self.now = 1
        self.observe(('t1','t2'),turn_id='t3',execution='running')
        second = self.slot.completion_notifications['t2']
        self.assertNotEqual(first.notification_id,second.notification_id)
        self.assertIs(self.b.pending_target().notification,second)
        self.b.act_on_call('pickup',self.token,first.notification_id)
        self.assertEqual(second.status,'active')
        self.assertFalse(second.controls_dismissed)

    def test_project_mute_preserves_policy_when_completion_arrives(self):
        self.b.set_project_muted(True)
        self.observe()
        notice = self.slot.completion_notifications['t1']
        self.assertTrue(self.b.attention_muted(self.slot))
        self.assertIsNone(self.b.pending_target())
        self.assertIsNone(self.b.notification_visuals()['presentation'])
        self.observe(last_hook={'hook_event_name':'Stop'})
        self.assertIs(self.slot.completion_notifications['t1'],notice)


class TailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root/'rollout.jsonl'
        append(self.path, record('session_meta', id=THREAD, cwd=str(self.root)),
               record('event_msg', type='task_started', turn_id='t1', collaboration_mode_kind='default'))

    def tearDown(self):
        self.temp.cleanup()

    def test_incremental_question_and_reply_keep_private_text_out(self):
        tail = RolloutTail(self.path, self.root, THREAD)
        self.assertEqual(tail.poll()['execution'], 'running')
        append(self.path, *question_records())
        self.assertEqual(tail.poll()['pending_questions'][0]['stage'], 'accepted')
        append(self.path, reply_record())
        result = tail.poll()
        self.assertFalse(result['pending_questions'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_partial_append_and_file_replacement(self):
        cursor = JsonlCursor(self.path)
        rows, reset, done = cursor.poll()
        self.assertTrue(reset and done)
        with self.path.open('a') as stream: stream.write('{"ok":')
        self.assertEqual(cursor.poll(), ([], False, False))
        with self.path.open('a') as stream: stream.write('true}\n')
        self.assertEqual(cursor.poll(), ([{'ok': True}], False, True))
        other = self.root/'other'
        other.write_text('{"replacement":true}\n'); other.replace(self.path)
        self.assertEqual(cursor.poll(), ([{'replacement': True}], True, True))

    def test_bootstrap_does_not_replay_historical_pending_question(self):
        append(self.path, *question_records(), reply_record())
        original = JsonlCursor.poll
        with patch.object(JsonlCursor, 'poll', lambda cursor: original(cursor, budget=1)):
            tail = RolloutTail(self.path, self.root, THREAD)
            result = None
            for _ in range(8):
                result = tail.poll()
                if result is not None: break
            self.assertIsNotNone(result)
            self.assertFalse(result['pending_questions'])

    def test_wrong_project_and_native_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            RolloutTail(self.path, self.root/'other', THREAD).poll()
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            RolloutTail(self.path, self.root, str(UUID(int=72))).poll()

    def test_new_turn_without_end_record_cleans_old_pending_as_unconfirmed(self):
        tail = RolloutTail(self.path, self.root, THREAD)
        append(self.path, *question_records())
        self.assertTrue(tail.poll()['pending_questions'])
        append(self.path, record('event_msg', type='task_started', turn_id='t2'))
        result = tail.poll()
        self.assertFalse(result['pending_questions'])
        self.assertEqual(result['recent_questions'][0]['stage'], 'turn_ended_unconfirmed')
        self.assertFalse(result['recent_questions'][0]['reply_observed'])

    def test_hook_discovery_reads_only_our_project_handler(self):
        (self.root/'.codex').mkdir()
        import shlex
        command = f'python probe.py record --project {shlex.quote(str(self.root))} --output {shlex.quote(str(self.root / "events.jsonl"))}'
        (self.root/'.codex/config.toml').write_text('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\n'
            'statusMessage="Abralia metadata-only hook probe"\ncommand=' + json.dumps(command) + '\n')
        self.assertEqual(configured_hook_journal(self.root), self.root/'events.jsonl')

    def test_fast_turn_completed_between_polls_is_retained_for_retry(self):
        tail = RolloutTail(self.path,self.root,THREAD)
        self.assertEqual(tail.poll()['completed_turn_ids'],[])
        append(self.path, record('event_msg',type='task_complete',turn_id='t1'),
               record('event_msg',type='task_started',turn_id='t2'),
               record('event_msg',type='task_complete',turn_id='t2'),
               record('event_msg',type='task_started',turn_id='t3'))
        value = tail.poll()
        self.assertEqual((value['execution'],value['turn_id']),('running','t3'))
        self.assertEqual(value['completed_turn_ids'],['t1','t2'])
        self.assertEqual(tail.poll()['completed_turn_ids'],['t1','t2'])
        value['completed_turn_ids'].clear()
        self.assertEqual(tail.poll()['completed_turn_ids'],['t1','t2'])

    def test_historical_completion_is_suppressed_until_bootstrap_catches_up(self):
        append(self.path,record('event_msg',type='task_complete',turn_id='t1'))
        original = JsonlCursor.poll
        with patch.object(JsonlCursor,'poll',lambda cursor: original(cursor,budget=1)):
            tail = RolloutTail(self.path,self.root,THREAD)
            value = None
            for _ in range(6):
                value = tail.poll()
                if value is not None: break
            self.assertIsNotNone(value)
            self.assertEqual(value['completed_turn_ids'],[])
        append(self.path,record('event_msg',type='task_started',turn_id='t2'),
               record('event_msg',type='task_complete',turn_id='t2'))
        self.assertEqual(tail.poll()['completed_turn_ids'],['t2'])

    def test_partial_completion_append_waits_for_newline_without_losing_live_boundary(self):
        tail = RolloutTail(self.path,self.root,THREAD); tail.poll()
        with self.path.open('a') as stream:
            stream.write(json.dumps(record('event_msg',type='task_complete',turn_id='t1')))
        self.assertIsNone(tail.poll())
        with self.path.open('a') as stream: stream.write('\n')
        self.assertEqual(tail.poll()['completed_turn_ids'],['t1'])

    def test_log_replacement_and_truncation_baseline_old_completions_again(self):
        tail = RolloutTail(self.path,self.root,THREAD); tail.poll()
        append(self.path,record('event_msg',type='task_complete',turn_id='t1'))
        self.assertEqual(tail.poll()['completed_turn_ids'],['t1'])
        replacement = self.root/'replacement.jsonl'
        append(replacement,record('session_meta',id=THREAD,cwd=str(self.root)),
               record('event_msg',type='task_started',turn_id='history'),
               record('event_msg',type='task_complete',turn_id='history'))
        replacement.replace(self.path)
        self.assertEqual(tail.poll()['completed_turn_ids'],[])
        self.path.write_text(json.dumps(record('session_meta',id=THREAD,cwd=str(self.root)))+'\n')
        self.assertEqual(tail.poll()['completed_turn_ids'],[])
        append(self.path,record('event_msg',type='task_started',turn_id='fresh'),
               record('event_msg',type='task_complete',turn_id='fresh'))
        self.assertEqual(tail.poll()['completed_turn_ids'],['fresh'])

    def test_abort_stop_and_late_old_turn_completion_are_not_success(self):
        tail = RolloutTail(self.path,self.root,THREAD); tail.poll()
        append(self.path,record('event_msg',type='turn_aborted',turn_id='t1'),
               record('event_msg',type='task_complete',turn_id='t1'),
               record('event_msg',type='task_started',turn_id='t2'),
               record('event_msg',type='hook_completed',turn_id='t2',hook_event_name='Stop'),
               record('event_msg',type='task_complete',turn_id='t1'))
        value = tail.poll()
        self.assertEqual(value['completed_turn_ids'],[])
        self.assertEqual((value['execution'],value['turn_id']),('running','t2'))
        append(self.path,record('event_msg',type='task_complete',turn_id='t2'))
        self.assertEqual(tail.poll()['completed_turn_ids'],['t2'])

    def test_recent_live_completion_ids_are_bounded_and_duplicate_completion_does_not_repeat(self):
        tail = RolloutTail(self.path,self.root,THREAD); tail.poll()
        for number in range(130):
            turn = f'fast-{number}'
            append(self.path,record('event_msg',type='task_started',turn_id=turn),
                   record('event_msg',type='task_complete',turn_id=turn),
                   record('event_msg',type='task_complete',turn_id=turn))
        value = tail.poll()
        self.assertEqual(value['completed_turn_ids'],[f'fast-{n}' for n in range(2,130)])


class ObserverServiceTests(unittest.TestCase):
    def test_registered_session_flows_from_journals_to_broker_then_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix='abralia-observe-', dir='/private/tmp') as directory:
            root = Path(directory)
            home = root/'codex'
            roll = home/f'sessions/2026/09/11/rollout-fixture-{THREAD}.jsonl'
            roll.parent.mkdir(parents=True)
            append(roll, record('session_meta', id=THREAD, cwd=str(root)),
                   record('event_msg', type='task_started', turn_id='t1', collaboration_mode_kind='default'))
            journal = root/'hooks.jsonl'
            append(journal, {'schema_version': 1, 'session_id': THREAD, 'hook_event_name': 'PreToolUse',
                             'tool_name': 'Bash', 'tool_input': 'PRIVATE COMMAND'})
            service = BrokerService(root, PROFILE, endpoint=root/'broker.sock', observe_codex=True,
                                    codex_home=home, codex_hook_journal=journal)
            service.observer.interval = .02
            with service, BrokerClient(root, endpoint=service.endpoint) as agent:
                self.assertFalse(service.broker.slots)  # Observer never allocates.
                with BrokerClient(root, endpoint=service.endpoint, role='observer') as untrusted:
                    self.assertEqual(untrusted.request({'type':'ping'})['detail'], 'invalid_connection_role')
                self.assertEqual(agent.request({'type':'codex_observation_targets'})['status'], 'rejected')
                acquired = agent.request({'type':'call', 'operation':'acquire_slot',
                    'arguments':{'label':'simulated observer test','harness':'codex_desktop','idempotency_key':'acquire'},
                    'metadata':{'thread_id':THREAD}})
                token = acquired['allocation']['slot_token']
                def status():
                    return agent.request({'type':'call', 'operation':'get_status', 'arguments':{},
                                          'metadata':{'thread_id':THREAD}})['allocation']
                def until(predicate):
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        value = status()
                        if predicate(value): return value
                        time.sleep(.01)
                    self.fail('observer did not produce expected state')
                baseline = until(lambda value: value['observed'] is not None)
                self.assertEqual(baseline['observed']['last_hook']['tool_name'], 'Bash')
                append(roll, *question_records())
                noticed = until(lambda value: value['notification'] is not None)
                self.assertEqual(noticed['notification']['origin'], 'codex')
                self.assertIsNone(noticed['question'])
                self.assertNotIn('PRIVATE', json.dumps(noticed))
                append(roll, reply_record())
                until(lambda value: value['notification']['status'] == 'cancelled')
                append(roll,record('event_msg',type='task_complete',turn_id='t1'))
                completed = until(lambda value: value['notification'] is not None
                                  and value['notification']['origin'] == 'codex_turn')
                self.assertEqual(completed['notification']['request_id'],'t1')
                self.assertEqual(completed['observed']['completed_turn_ids'],['t1'])
                self.assertEqual(completed['state'],'idle')  # Turn end is not semantic project completion.
                self.assertIsNone(completed['question'])
                agent.request({'type':'call','operation':'release_slot',
                    'arguments':{'slot_token':token,'idempotency_key':'release'},'metadata':{'thread_id':THREAD}})
            self.assertFalse(service.observer.thread.is_alive())
            self.assertFalse(service.worker.is_alive())
            self.assertFalse(service.endpoint.exists())
            self.assertIsNone(service.cleanup_error)

    def test_observer_shutdown_error_does_not_skip_device_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='abralia-close-', dir='/private/tmp') as directory:
            root = Path(directory)
            service = BrokerService(root, PROFILE, endpoint=root/'broker.sock', observe_codex=True,
                                    codex_home=root/'empty').start()
            service.observer.close()
            with patch.object(service.observer, 'close', side_effect=RuntimeError('fixture observer stop failure')):
                with self.assertRaisesRegex(RuntimeError, 'fixture'):
                    service.close()
            self.assertFalse(service.worker.is_alive())
            self.assertFalse(service.endpoint.exists())
            self.assertIsNone(service.lock_file)


if __name__ == '__main__':
    unittest.main()
