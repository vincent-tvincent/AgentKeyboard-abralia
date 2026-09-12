# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import tempfile
import unittest

from abralia.backend.codex_rollout import RolloutObservation, read_rollout

THREAD='11111111-2222-3333-4444-555555555555'


class CodexRolloutTests(unittest.TestCase):
    def setUp(self): self.s=RolloutObservation(THREAD)
    def event(self,kind,turn='turn-1'):
        self.s.feed({'type':'event_msg','payload':{'type':kind,'turn_id':turn}})
    def item(self,kind,**fields):
        self.s.feed({'type':'response_item','payload':{'type':kind,**fields}})
    def question(self,count=1):
        self.event('task_started')
        self.item('function_call',name='request_user_input_async',call_id='call-1',
                  arguments=json.dumps({'questions':[{'title':'PRIVATE QUESTION'}]*count}))
    def reply(self,index=0,role='user',call_id='call-1'):
        text='<send_user_message_question_reply>'+json.dumps([{'questionItemId':json.dumps(['request_user_input_async',call_id,index]),'answer':'PRIVATE ANSWER'}])+'</send_user_message_question_reply>'
        self.item('message',role=role,content=[{'type':'input_text','text':text}])

    def test_turn_events_and_late_completion_do_not_overwrite_current_turn(self):
        self.event('task_started');self.event('task_started','turn-2');self.event('task_complete')
        self.assertEqual(self.s.execution,'running')
        self.event('task_complete','turn-2');self.assertEqual(self.s.execution,'idle')
        self.event('turn_aborted','turn-2');self.assertEqual(self.s.execution,'interrupted')

    def test_async_acceptance_and_exact_reply_are_distinct_and_private(self):
        self.question()
        self.item('function_call_output',call_id='call-1',output='{"accepted":true}')
        self.assertEqual(self.s.questions['call-1']['stage'],'accepted')
        self.assertFalse(self.s.questions['call-1']['reply_observed'])
        self.reply();self.assertEqual(self.s.questions['call-1']['stage'],'response_received')
        self.assertNotIn('PRIVATE',json.dumps(self.s.snapshot()))

    def test_wrong_role_or_request_does_not_resolve(self):
        self.question();self.reply(role='assistant');self.reply(call_id='other')
        self.assertEqual(self.s.questions['call-1']['stage'],'invoked')

    def test_multiple_question_reply_waits_for_all_indices(self):
        self.question(2);self.reply();self.assertFalse(self.s.questions['call-1']['reply_observed'])
        self.reply(1);self.assertTrue(self.s.questions['call-1']['reply_observed'])

    def test_rejected_call_is_not_a_pending_question(self):
        self.question();self.item('function_call_output',call_id='call-1',output='Unavailable in Default mode')
        self.assertEqual(self.s.questions['call-1']['stage'],'tool_ended_without_acceptance')

    def test_turn_end_is_not_an_answer_and_late_reply_does_not_change_execution(self):
        self.question();self.item('function_call_output',call_id='call-1',output={'accepted':True})
        self.event('task_complete')
        self.assertEqual(self.s.questions['call-1']['stage'],'turn_ended_unconfirmed')
        self.assertFalse(self.s.questions['call-1']['reply_observed'])
        self.event('task_started','turn-2');self.reply()
        self.assertEqual(self.s.execution,'running')
        self.assertTrue(self.s.questions['call-1']['reply_observed'])

    def test_duplicate_call_does_not_reopen_a_resolved_question(self):
        self.question();self.reply();self.question()
        self.assertEqual(self.s.questions['call-1']['stage'],'response_received')

    def test_reader_rejects_wrong_identity_and_ignores_partial_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();path=root/'rollout.jsonl'
            header={'type':'session_meta','payload':{'id':THREAD,'cwd':str(root)}}
            start={'type':'event_msg','payload':{'type':'task_started','turn_id':'live'}}
            path.write_text(json.dumps(header)+'\n'+json.dumps(start)+'\n'+json.dumps({'type':'event_msg','payload':{'type':'task_complete','turn_id':'live'}}))
            self.assertEqual(read_rollout(path,thread_id=THREAD,project=root)['execution'],'running')
            with self.assertRaisesRegex(ValueError,'identity'):
                read_rollout(path,thread_id='22222222-2222-3333-4444-555555555555',project=root)
            with self.assertRaisesRegex(ValueError,'outside'):
                read_rollout(path,thread_id=THREAD,project=root/'other')


if __name__=='__main__':unittest.main()
