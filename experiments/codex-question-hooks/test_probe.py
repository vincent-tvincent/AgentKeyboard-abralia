# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

from probe import configure, record, summarize


class HookProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / 'events.jsonl'

    def payload(self, **extra):
        return {'hook_event_name':'PreToolUse', 'cwd':str(self.root),
                'session_id':'session-1', 'turn_id':'turn-1', 'tool_use_id':'call-1',
                'tool_name':'request_user_input_async', **extra}

    def test_no_question_answer_prompt_command_or_extra_content_is_recorded(self):
        payload = self.payload(tool_input={'questions':[{'title':'PRIVATE_QUESTION','options':['PRIVATE_OPTION']}]},
                               tool_response={'answers':{'PRIVATE_QUESTION':'PRIVATE_ANSWER'}},
                               prompt='PRIVATE_PROMPT', command='PRIVATE_COMMAND', secret='PRIVATE_SECRET')
        row = summarize(payload, self.root)
        self.assertEqual(row['question_count'], 1)
        self.assertEqual(row['output_kind'], 'answers_object')
        self.assertNotIn('PRIVATE_', json.dumps(row))
        self.assertNotIn('cwd', row)

    def test_acceptance_is_not_classified_as_an_answer(self):
        row = summarize(self.payload(tool_response='{"accepted":true}'), self.root)
        self.assertEqual(row['output_kind'], 'accepted')

    def test_outside_project_and_unknown_event_are_ignored(self):
        self.assertIsNone(summarize(self.payload(cwd='/'), self.root))
        self.assertIsNone(summarize(self.payload(hook_event_name='Unknown'), self.root))
        self.assertIsNotNone(summarize(self.payload(cwd=str(self.root/'nested')), self.root))

    def test_bad_input_or_unwritable_output_never_raises(self):
        record(self.root, self.output, b'not JSON')
        record(self.root, self.root, json.dumps(self.payload()).encode())
        self.assertFalse(self.output.exists())

    def test_concurrent_metadata_appends_remain_valid_json_lines(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: record(self.root,self.output,json.dumps(self.payload(tool_use_id=f'call-{i}')).encode()),range(32)))
        rows=[json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(len({r['tool_use_id'] for r in rows}),32)

    def test_install_remove_preserve_unrelated_configuration_and_do_not_trust(self):
        directory=self.root/'.codex'; directory.mkdir()
        path=directory/'config.toml'
        original='# User settings\n[mcp_servers.existing]\ncommand="example"\n'
        path.write_text(original)
        one=configure(self.root,self.output,Path(sys.executable))
        self.assertFalse(one['trust_changed'])
        installed=path.read_text()
        self.assertEqual(tomllib.loads(installed)['mcp_servers']['existing']['command'],'example')
        configure(self.root,self.output,Path(sys.executable))
        self.assertEqual(path.read_text(),installed)
        configure(self.root,self.output,Path(sys.executable),remove=True)
        self.assertEqual(path.read_text(),original)

    def test_edited_probe_configuration_is_preserved(self):
        configure(self.root,self.output,Path(sys.executable))
        path=self.root/'.codex/config.toml'
        edited=path.read_text().replace('timeout = 2','timeout = 3')
        path.write_text(edited)
        with self.assertRaisesRegex(ValueError,'edited'):
            configure(self.root,self.output,Path(sys.executable),remove=True)
        self.assertEqual(path.read_text(),edited)


if __name__ == '__main__': unittest.main()
