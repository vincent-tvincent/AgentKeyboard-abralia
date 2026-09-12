# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read-only Codex rollout observations, with no hook or keyboard dependency."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import re
from uuid import UUID

QUESTION_TOOLS = {'request_user_input', 'request_user_input_async'}
REPLY = re.compile(r'\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*', re.S)


def json_object(value):
    if isinstance(value, str):
        try: value = json.loads(value)
        except ValueError: return {}
    return value if isinstance(value, dict) else {}


class RolloutObservation:
    """Observe records, not UI state. No prompt/answer/command text is retained."""

    def __init__(self, thread_id):
        self.thread_id = str(UUID(thread_id))
        self.turn_id = None
        self.mode = None
        self.execution = 'unknown'
        self.last_event_at = None
        self.questions = OrderedDict()
        self.records = 0

    def feed(self, record):
        if not isinstance(record, dict): return
        p = record.get('payload')
        if not isinstance(p, dict): return
        self.records += 1
        top, kind = record.get('type'), p.get('type')
        if top == 'event_msg':
            if kind == 'task_started' and isinstance(p.get('turn_id'), str):
                if self.turn_id != p['turn_id']:
                    for question in self.questions.values():
                        if question['stage'] in ('invoked', 'accepted'):
                            question['stage'] = 'turn_ended_unconfirmed'
                self.turn_id = p['turn_id']
                self.mode = p.get('collaboration_mode_kind') if p.get('collaboration_mode_kind') in ('plan', 'default') else None
                self.execution = 'running'
                self.last_event_at = record.get('timestamp')
            elif kind in ('task_complete', 'turn_aborted') and p.get('turn_id') == self.turn_id and self.turn_id:
                self.execution = 'idle' if kind == 'task_complete' else 'interrupted'
                self.last_event_at = record.get('timestamp')
                for question in self.questions.values():
                    if question['turn_id'] == self.turn_id and question['stage'] in ('invoked', 'accepted'):
                        question['stage'] = 'turn_ended_unconfirmed'
            return
        if top != 'response_item': return
        call_id = p.get('call_id')
        if kind == 'function_call' and p.get('name') in QUESTION_TOOLS and isinstance(call_id, str):
            if call_id not in self.questions:
                args = json_object(p.get('arguments'))
                questions = args.get('questions')
                count = len(questions) if isinstance(questions, list) else 0
                if count:
                    self.questions[call_id] = {'request_id':call_id, 'tool':p['name'],
                        'turn_id':self.turn_id, 'question_count':count, 'stage':'invoked',
                        'accepted_observed':False, 'reply_observed':False, '_answered':set()}
                    while len(self.questions) > 128: self.questions.popitem(last=False)
        elif kind == 'function_call_output' and isinstance(call_id, str) and call_id in self.questions:
            question = self.questions[call_id]
            if question['stage'] not in ('invoked', 'accepted'): return
            output = json_object(p.get('output'))
            if output.get('accepted') is True:
                question['stage'] = 'accepted'
                question['accepted_observed'] = True
            elif isinstance(output.get('answers'), dict):
                question['stage'] = 'response_received' if output['answers'] else 'returned_empty'
                question['reply_observed'] = bool(output['answers'])
            else:
                question['stage'] = 'tool_ended_without_acceptance'
        elif kind == 'message' and p.get('role') == 'user':
            content = p.get('content')
            if not isinstance(content, list): return
            texts = [c['text'] for c in content if isinstance(c,dict) and isinstance(c.get('text'),str)
                     and c.get('type') in ('text','input_text')]
            self._reply('\n'.join(texts))

    def _reply(self, text):
        match = REPLY.fullmatch(text)
        if not match: return
        try: replies = json.loads(match.group(1))
        except ValueError: return
        if not isinstance(replies,list): return
        for reply in replies:
            if not isinstance(reply,dict): continue
            key = reply.get('questionItemId')
            if isinstance(key,str):
                try: key=json.loads(key)
                except ValueError: continue
            if not isinstance(key,list) or len(key)!=3: continue
            tool, request_id, index = key
            if not isinstance(request_id,str) or request_id not in self.questions: continue
            question=self.questions[request_id]
            if tool!=question['tool'] or type(index) is not int or not 0<=index<question['question_count']: continue
            question['_answered'].add(index)
            if len(question['_answered'])==question['question_count']:
                question['stage']='response_received'
                question['reply_observed']=True

    def snapshot(self):
        return {'source':'codex_rollout', 'thread_id':self.thread_id,
                'execution':self.execution, 'turn_id':self.turn_id, 'last_event_at':self.last_event_at,
                'mode':self.mode,
                'records_read':self.records,
                'pending_questions':[{k:v for k,v in q.items() if not k.startswith('_')}
                                     for q in self.questions.values() if q['stage'] in ('invoked','accepted')],
                'recent_questions':[{k:v for k,v in q.items() if not k.startswith('_')}
                                    for q in list(self.questions.values())[-16:]],
                'limitations':['Version-dependent local log format',
                               'Tool invocation/acceptance does not prove visible UI',
                               'Native skip/expiry is not directly observed',
                               'Idle means turn ended, not project completed']}


def read_rollout(path, *, thread_id, project):
    path, project = Path(path), Path(project).resolve()
    state = RolloutObservation(thread_id)
    malformed = 0
    with path.open() as stream:
        header = json.loads(stream.readline())
        meta = header.get('payload',{})
        if header.get('type')!='session_meta' or meta.get('id')!=state.thread_id:
            raise ValueError('rollout session identity mismatch')
        cwd=meta.get('cwd')
        if not isinstance(cwd,str) or not Path(cwd).resolve().is_relative_to(project):
            raise ValueError('rollout is outside the selected project')
        for line in stream:
            if not line.endswith('\n'): break  # Ignore an unfinished append.
            try: record=json.loads(line)
            except ValueError:
                malformed += 1
                continue
            state.feed(record)
    return {**state.snapshot(), 'malformed_records':malformed}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollout',required=True,type=Path)
    parser.add_argument('--thread-id',required=True)
    parser.add_argument('--project',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(read_rollout(args.rollout,thread_id=args.thread_id,project=args.project),indent=2))
        return 0
    except (OSError,ValueError) as error:
        print(json.dumps({'status':'unavailable','reason':str(error)}))
        return 1


if __name__=='__main__': raise SystemExit(main())
