# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.codex_context import native_task_cwd
from abralia.backend.codex_hook import forward, summarize
from abralia.backend.ipc import shared_socket_path
from abralia.backend.mcp import Bridge
from abralia.backend.project_registry import ProjectRegistry

DESKTOP = Path(__file__).resolve().parents[1]
PLUGIN = DESKTOP / 'plugin-bundle/plugins/abralia'


class PluginRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abp-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'codex'
        self.home.mkdir()
        self.registry = ProjectRegistry(self.root / 'control')
        self.one, self.two, self.disabled = (self.root / n for n in ('one', 'two', 'disabled'))
        for path in (self.one, self.two, self.disabled):
            path.mkdir()
        self.registry.enroll(self.one)
        self.registry.enroll(self.two)
        self.ids = [str(UUID(int=i)) for i in range(1, 4)]
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT, archived INTEGER)')
            db.executemany('INSERT INTO threads VALUES(?,?,0)', zip(self.ids, map(str, (self.one, self.two, self.disabled))))

    def meta(self, index=0):
        return {'x-codex-turn-metadata': {'thread_id': self.ids[index], 'cwd': str(self.disabled)}}

    def test_native_index_context_ignores_plugin_cwd_and_model_paths(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        with patch('abralia.backend.mcp.BrokerClient') as client:
            client.return_value.request.return_value = {'status': 'accepted'}
            self.assertEqual(bridge.call(self.meta(), 'get_status', {})['status'], 'accepted')
            self.assertEqual(client.call_args.args[0], str(self.one))
            message = client.return_value.request.call_args.args[0]
            self.assertEqual(message['metadata']['cwd'], str(self.one))
            self.assertNotEqual(message['metadata']['cwd'], self.meta()['x-codex-turn-metadata']['cwd'])
            bridge.call(self.meta(1), 'get_status', {})
            self.assertEqual(client.call_count, 2)
            self.assertEqual(bridge.call(self.meta(2), 'get_status', {})['reason'], 'project_not_enabled')
            self.assertEqual(client.call_count, 2)

    def test_registry_is_checked_again_after_removal(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        with patch('abralia.backend.mcp.BrokerClient') as client:
            bridge.call(self.meta(), 'get_status', {})
            self.registry.remove(self.one)
            self.assertEqual(bridge.call(self.meta(), 'get_status', {})['reason'], 'project_not_enabled')
            self.assertEqual(client.return_value.request.call_count, 1)
            self.registry.enroll(self.one)
            bridge.call(self.meta(), 'get_status', {})
            self.assertEqual(client.call_count, 2)
            client.return_value.close.assert_called_once()

    def test_missing_and_archived_native_context_fail_closed(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        self.assertEqual(bridge.call({}, 'get_status', {})['reason'], 'caller_identity_unavailable')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET archived=1 WHERE id=?', (self.ids[0],))
        self.assertEqual(bridge.call(self.meta(), 'get_status', {})['reason'], 'native_project_unavailable')

    def test_disabled_historical_projects_do_not_exhaust_connection_capacity(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        with patch('abralia.backend.mcp.BrokerClient') as client, patch('abralia.backend.mcp.native_task_cwd') as native:
            client.return_value.request.return_value = {'status': 'accepted'}
            for index in range(70):
                project = self.root / f'cycle-{index}'
                project.mkdir()
                self.registry.enroll(project)
                native.return_value = project
                self.assertEqual(bridge.call(self.meta(), 'get_status', {})['status'], 'accepted')
                self.assertEqual(len(bridge.clients), 1)
                self.registry.remove(project)
            self.assertEqual(client.call_count, 70)
            self.assertEqual(client.return_value.close.call_count, 69)
            self.assertEqual(len(bridge.client_generations), 1)

    def test_enable_self_validates_arguments_and_native_identity_before_writes(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home, endpoint=self.root/'missing.sock')
        self.addCleanup(bridge.close)
        valid = {'label': 'Enable here', 'idempotency_key': 'enable'}
        for invalid in ({**valid, 'project': str(self.disabled)}, {**valid, 'label': ' '},
                        {**valid, 'label': 'x'*81}, {**valid, 'idempotency_key': ''},
                        {**valid, 'harness': 'made-up'}):
            self.assertEqual(bridge.call(self.meta(2), 'enable_self', invalid)['status'], 'rejected')
            self.assertIsNone(self.registry.resolve(self.disabled))
        self.assertEqual(bridge.call({}, 'enable_self', valid)['reason'], 'caller_identity_unavailable')
        unknown = {'x-codex-turn-metadata': {'thread_id': str(UUID(int=9999))}}
        self.assertEqual(bridge.call(unknown, 'enable_self', valid)['reason'], 'native_project_unavailable')
        self.assertIsNone(self.registry.resolve(self.disabled))

    def test_enable_self_reuses_enabled_parent_and_legacy_does_not_enroll(self):
        child = self.one/'component'
        child.mkdir()
        args = {'label': 'Enable here', 'idempotency_key': 'enable', 'harness': 'codex_desktop'}
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        with patch('abralia.backend.mcp.native_task_cwd', return_value=child), patch('abralia.backend.mcp.BrokerClient') as client:
            client.return_value.request.return_value = {'status': 'accepted', 'allocation': {'slot_id': 1}}
            result = bridge.call(self.meta(), 'enable_self', args)
            self.assertEqual(result['enabled_project']['path'], str(self.one))
            self.assertTrue(result['slot_acquired'])
            self.assertFalse(result['project_created'])
            self.assertEqual(len(self.registry.list()), 2)
        legacy = Bridge(str(self.disabled), endpoint=self.root/'missing.sock')
        self.addCleanup(legacy.close)
        result = legacy.call(self.meta(2), 'enable_self', args)
        self.assertEqual(result['enabled_project']['scope'], 'legacy_project_local')
        self.assertFalse(result['slot_acquired'])
        self.assertIsNone(self.registry.resolve(self.disabled))

    def test_enable_self_offline_retry_and_disable_respect_original_intent(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home, endpoint=self.root/'missing.sock')
        self.addCleanup(bridge.close)
        args = {'label': 'Enable current native project', 'idempotency_key': 'enable'}
        first = bridge.call(self.meta(2), 'enable_self', args)
        self.assertEqual(first['reason'], 'backend_unavailable')
        self.assertTrue(first['project_enabled'])
        self.assertTrue(first['project_created'])
        self.assertFalse(first['slot_acquired'])
        initial = self.registry.resolve(self.disabled)
        retry = bridge.call(self.meta(2), 'enable_self', args)
        self.assertTrue(retry['project_enabled'])
        self.assertFalse(retry['slot_acquired'])
        self.assertEqual(self.registry.resolve(self.disabled)['generation'], initial['generation'])
        self.assertEqual(bridge.call(self.meta(2), 'enable_self', {**args, 'label': 'Changed'})['reason'], 'idempotency_conflict')
        self.registry.remove(self.disabled)
        self.assertEqual(bridge.call(self.meta(2), 'enable_self', args)['reason'], 'stale_enable_request')
        self.assertIsNone(self.registry.resolve(self.disabled))
        fresh = bridge.call(self.meta(2), 'enable_self', {**args, 'idempotency_key': 'new-explicit-request'})
        self.assertTrue(fresh['project_created'])
        self.assertNotEqual(self.registry.resolve(self.disabled)['generation'], initial['generation'])

    def test_concurrent_first_enable_retry_does_not_look_stale(self):
        bridge = Bridge(registry=self.registry, codex_home=self.home)
        self.addCleanup(bridge.close)
        barrier, seen, lock = threading.Barrier(2), set(), threading.Lock()
        resolve = self.registry.resolve
        def synchronized_first_read(cwd):
            result = resolve(cwd)
            with lock:
                first = threading.get_ident() not in seen
                seen.add(threading.get_ident())
            if first:
                barrier.wait(timeout=5)
            return result
        args = {'label': 'Explicit concurrent request', 'idempotency_key': 'enable'}
        with patch.object(self.registry, 'resolve', side_effect=synchronized_first_read), patch('abralia.backend.mcp.BrokerClient') as client:
            client.return_value.request.return_value = {'status': 'accepted', 'allocation': {'slot_id': 1}}
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(bridge.call, self.meta(2), 'enable_self', args) for _ in range(2)]
                self.assertEqual([future.result()['status'] for future in futures], ['accepted', 'accepted'])
            self.assertEqual(client.call_count, 1)
        self.assertEqual(len(self.registry.list()), 3)

    def test_native_session_meta_fallback_checks_exact_id(self):
        (self.home / 'state_5.sqlite').unlink()
        folder = self.home / 'sessions/2026/09/13'
        folder.mkdir(parents=True)
        path = folder / f'rollout-date-{self.ids[0]}.jsonl'
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': self.ids[0], 'cwd': str(self.one)}})
                        + '\nprivate later conversation is never parsed\n')
        self.assertEqual(native_task_cwd(self.ids[0], codex_home=self.home), self.one)
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': self.ids[1], 'cwd': str(self.one)}}))
        with self.assertRaisesRegex(ValueError, 'native_project_unavailable'):
            native_task_cwd(self.ids[0], codex_home=self.home)

    def test_hooks_filter_private_contents_and_normalize_question_tool(self):
        payload = {'hook_event_name': 'PostToolUse', 'session_id': self.ids[0], 'cwd': str(self.one),
                   'turn_id': 'turn-1', 'tool_use_id': 'question-1', 'tool_name': 'functions.request_user_input_async',
                   'tool_input': {'questions': [{'question': 'SECRET PROMPT'}]},
                   'tool_response': {'accepted': True, 'secret': 'SECRET RESPONSE'}, 'command': 'SECRET COMMAND'}
        project, event = summarize(payload, self.registry)
        self.assertEqual(project['path'], str(self.one))
        self.assertEqual(event['tool_name'], 'request_user_input_async')
        self.assertEqual(event['output_kind'], 'accepted')
        self.assertEqual(event['question_count'], 1)
        self.assertNotIn('SECRET', json.dumps(event))
        payload['tool_response'] = {'answers': {'x': 'PRIVATE ANSWER'}}
        self.assertEqual(summarize(payload, self.registry)[1]['output_kind'], 'answers')
        payload['subagent_id'] = 'child-not-a-native-thread'
        self.assertIsNone(summarize(payload, self.registry))
        payload.pop('subagent_id')
        payload['cwd'] = str(self.disabled)
        with patch('abralia.backend.codex_hook.BrokerClient') as client:
            self.assertEqual(forward(json.dumps(payload).encode(), registry=self.registry)['reason'], 'event_not_applicable')
            client.assert_not_called()

    def test_hook_offline_is_bounded_silent_success(self):
        payload = {'hook_event_name': 'SessionStart', 'session_id': self.ids[0], 'cwd': str(self.one)}
        result = subprocess.run([sys.executable, '-m', 'abralia.backend.codex_hook'], input=json.dumps(payload),
            text=True, capture_output=True, timeout=3,
            env={**os.environ, 'ABRALIA_STATE_DIR': str(self.root / 'control'), 'ABRALIA_RUNTIME_DIR': str(self.root)})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(forward(b'[' * 10000, registry=self.registry)['status'], 'skipped')

    def test_launcher_preserves_arguments_with_spaces_and_hook_missing_is_quiet(self):
        executable = self.root / 'runtime with spaces'
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        executable.chmod(0o700)
        params = ['/bin/sh', str(PLUGIN / 'scripts/abralia-launch'), 'mcp', '--socket', '/some path/shared.sock']
        result = subprocess.run(params, env={**os.environ, 'ABRALIA_RUNTIME_LAUNCHER': str(executable)},
                                text=True, capture_output=True)
        self.assertEqual(result.stdout.splitlines(), ['mcp', '--socket', '/some path/shared.sock'])
        result = subprocess.run(params[:2] + ['codex-hook'], env={**os.environ, 'ABRALIA_RUNTIME_LAUNCHER': str(self.root/'missing')},
                                text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_payload_names_and_paths_are_portable(self):
        manifest = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())
        marketplace = json.loads((DESKTOP / 'plugin-bundle/.agents/plugins/marketplace.json').read_text())
        self.assertEqual((manifest['name'], marketplace['name']), ('abralia', 'abralia'))
        self.assertNotIn('hooks', manifest)
        handlers = json.loads((PLUGIN / 'hooks/hooks.json').read_text())['hooks']
        for event in handlers.values():
            handler = event[0]['hooks'][0]
            self.assertTrue(handler['statusMessage'].startswith('Abralia: '))
            self.assertNotIn('probe', handler['statusMessage'])
            self.assertIn('${PLUGIN_ROOT}', handler['command'])
            self.assertEqual(handler['timeout'], 2)
        mcp = json.loads((PLUGIN / '.mcp.json').read_text())['mcpServers']['abralia']
        self.assertEqual(mcp['args'], ['./scripts/abralia-launch', 'mcp'])
        self.assertEqual(mcp['cwd'], '.')

    def test_frozen_dispatch_modes_are_distinct(self):
        for mode in ('mcp', 'codex-hook'):
            result = subprocess.run([sys.executable, str(DESKTOP/'gui/scripts/frozen_host.py'), mode, '--help'],
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('managed-mode', result.stdout)

    def test_real_stdio_inventory_and_unavailable_project_are_distinct(self):
        from mcp import Client, StdioServerParameters

        async def scenario():
            parameters = StdioServerParameters(command=sys.executable,
                args=['-B', '-m', 'abralia.backend.mcp'],
                env={**os.environ, 'CODEX_HOME': str(self.home), 'ABRALIA_STATE_DIR': str(self.root/'control'),
                     'ABRALIA_RUNTIME_DIR': str(self.root)})
            async with Client(parameters) as client:
                tools = (await client.list_tools()).tools
                self.assertIn('acquire_slot', {tool.name for tool in tools})
                for tool in tools:
                    self.assertTrue({'project', 'thread_id', 'cwd'}.isdisjoint(tool.input_schema.get('properties', {})))
                disabled = await client.call_tool('get_status', {}, meta=self.meta(2))
                self.assertEqual(disabled.structured_content['reason'], 'project_not_enabled')
                offline = await client.call_tool('get_status', {}, meta=self.meta())
                self.assertEqual(offline.structured_content['reason'], 'backend_unavailable')
        asyncio.run(scenario())

    def test_real_stdio_two_projects_share_service_and_hooks_do_not_allocate(self):
        from mcp import Client, StdioServerParameters
        from abralia.backend.service import BrokerService
        from abralia.backend.ipc import BrokerClient

        endpoint = self.root/'shared.sock'
        service = BrokerService(self.root, 'builtin:keychron-v3-8k-ansi-encoder-effect25', shared=True, mode='simulated',
                               state_dir=self.root/'control', endpoint=endpoint)
        service.start()
        self.addCleanup(service.close)
        payload = {'hook_event_name': 'SessionStart', 'session_id': self.ids[0], 'cwd': str(self.one)}
        report = forward(json.dumps(payload).encode(), registry=self.registry, endpoint=endpoint)
        self.assertEqual(report['status'], 'accepted', report)
        self.assertFalse(report['allocation_present'])
        self.assertFalse(service.broker.slots)

        async def scenario():
            parameters = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'abralia.backend.mcp'],
                env={**os.environ, 'CODEX_HOME': str(self.home), 'ABRALIA_STATE_DIR': str(self.root/'control'),
                     'ABRALIA_RUNTIME_DIR': str(self.root)})
            async with Client(parameters) as client:
                tokens = []
                for index in (0, 1):
                    result = await client.call_tool('acquire_slot', {'label': 'Native fixture',
                        'harness': 'codex_desktop', 'idempotency_key': 'acquire'}, meta=self.meta(index))
                    self.assertEqual(result.structured_content['status'], 'accepted', result)
                    tokens.append(result.structured_content['allocation']['slot_token'])
                self.assertEqual(len(service.broker.slots), 2)
                stolen = await client.call_tool('release_slot', {'slot_token': tokens[0],
                    'idempotency_key': 'stolen'}, meta=self.meta(1))
                self.assertEqual(stolen.structured_content['status'], 'rejected')
                disabled = await client.call_tool('acquire_slot', {'label': 'Disabled',
                    'idempotency_key': 'disabled'}, meta=self.meta(2))
                self.assertEqual(disabled.structured_content['reason'], 'project_not_enabled')
                payload.update(hook_event_name='PostToolUse', tool_name='functions.request_user_input_async',
                               tool_use_id='native-question-1', turn_id='native-turn-1',
                               tool_input={'questions': [{'question': 'PRIVATE'}]}, tool_response={'accepted': True})
                report = forward(json.dumps(payload).encode(), registry=self.registry, endpoint=endpoint)
                self.assertEqual(report['status'], 'accepted', report)
                one = (await client.call_tool('get_status', {}, meta=self.meta())).structured_content
                two = (await client.call_tool('get_status', {}, meta=self.meta(1))).structured_content
                self.assertIsNotNone(one['allocation']['notification'])
                self.assertIsNone(two['allocation']['notification'])
                notice = one['allocation']['notification']['id']
                forward(json.dumps(payload).encode(), registry=self.registry, endpoint=endpoint)
                retried = (await client.call_tool('get_status', {}, meta=self.meta())).structured_content
                self.assertEqual(retried['allocation']['notification']['id'], notice)
                payload['tool_response'] = {'answers': {'answer': 'PRIVATE'}}
                forward(json.dumps(payload).encode(), registry=self.registry, endpoint=endpoint)
                answered = (await client.call_tool('get_status', {}, meta=self.meta())).structured_content
                self.assertEqual(answered['allocation']['notification']['status'], 'cancelled')
                self.assertIsNone(service.broker.pending_target())
                with BrokerClient(self.root, endpoint=endpoint, role='admin') as admin:
                    status = admin.request({'type': 'admin', 'action': 'status'})
                    self.assertEqual({row['path'] for row in status['projects']}, {str(self.one), str(self.two)})
                    self.assertEqual([row['task_count'] for row in status['projects']], [1, 1])
                for index, token in enumerate(tokens):
                    result = await client.call_tool('release_slot', {'slot_token': token,
                        'idempotency_key': 'release'}, meta=self.meta(index))
                    self.assertEqual(result.structured_content['status'], 'accepted')
        asyncio.run(scenario())
        self.assertFalse(service.broker.slots)

    def test_real_stdio_enable_self_only_on_explicit_tool_and_fresh_generation(self):
        from mcp import Client, StdioServerParameters
        from abralia.backend.service import BrokerService
        endpoint = self.root/'shared.sock'
        service = BrokerService(self.root, 'builtin:keychron-v3-8k-ansi-encoder-effect25', shared=True, mode='simulated',
                               state_dir=self.root/'control', endpoint=endpoint)
        service.start()
        self.addCleanup(service.close)

        async def scenario():
            params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'abralia.backend.mcp'],
                env={**os.environ, 'CODEX_HOME': str(self.home), 'ABRALIA_STATE_DIR': str(self.root/'control'),
                     'ABRALIA_RUNTIME_DIR': str(self.root)})
            async with Client(params) as client:
                tools = (await client.list_tools()).tools
                tool = next(tool for tool in tools if tool.name == 'enable_self')
                self.assertTrue({'project', 'project_id', 'cwd', 'thread_id', 'user_approved'}.isdisjoint(tool.input_schema['properties']))
                skipped = await client.call_tool('get_status', {}, meta=self.meta(2))
                self.assertEqual(skipped.structured_content['reason'], 'project_not_enabled')
                skipped = await client.call_tool('acquire_slot', {'label': 'Passive disabled registration',
                    'idempotency_key': 'ordinary'}, meta=self.meta(2))
                self.assertEqual(skipped.structured_content['reason'], 'project_not_enabled')
                payload = {'hook_event_name': 'SessionStart', 'session_id': self.ids[2], 'cwd': str(self.disabled)}
                self.assertEqual(forward(json.dumps(payload).encode(), registry=self.registry, endpoint=endpoint)['status'], 'skipped')
                self.assertIsNone(self.registry.resolve(self.disabled))
                self.assertFalse(service.broker.slots)
                args = {'label': 'User requested Abralia', 'harness': 'codex_desktop', 'idempotency_key': 'explicit-enable'}
                first = (await client.call_tool('enable_self', args, meta=self.meta(2))).structured_content
                self.assertTrue(first['project_enabled'])
                self.assertTrue(first['project_created'])
                self.assertTrue(first['slot_acquired'])
                self.assertEqual(first['enabled_project']['path'], str(self.disabled))
                token = first['allocation']['slot_token']
                generation = self.registry.resolve(self.disabled)['generation']
                retry = (await client.call_tool('enable_self', args, meta=self.meta(2))).structured_content
                self.assertEqual(retry['allocation']['slot_token'], token)
                self.assertEqual(len(service.broker.slots), 1)
                self.registry.remove(self.disabled)
                stale = (await client.call_tool('enable_self', args, meta=self.meta(2))).structured_content
                self.assertEqual(stale['reason'], 'stale_enable_request')
                self.assertIsNone(self.registry.resolve(self.disabled))
                fresh = (await client.call_tool('enable_self', {**args, 'idempotency_key': 'new-explicit-enable'}, meta=self.meta(2))).structured_content
                self.assertTrue(fresh['slot_acquired'])
                self.assertNotEqual(self.registry.resolve(self.disabled)['generation'], generation)
                self.assertNotEqual(fresh['allocation']['slot_token'], token)
                self.assertEqual(len(service.broker.slots), 1)
                result = await client.call_tool('release_slot', {'slot_token': fresh['allocation']['slot_token'],
                    'idempotency_key': 'release'}, meta=self.meta(2))
                self.assertEqual(result.structured_content['status'], 'accepted')
        asyncio.run(scenario())
        self.assertFalse(service.broker.slots)


if __name__ == '__main__':
    unittest.main()
