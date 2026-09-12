# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from abralia.backend.core import OPERATIONS
from abralia.backend.ipc import BrokerClient
from abralia.backend.project import disable_project, enable_project, inspect_project
from abralia.backend.service import BrokerService
from abralia.backend.cli import main

PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


class ProjectSetupTests(unittest.TestCase):
    def test_enable_refreshes_unchanged_managed_skill_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enable_project(root)
            config = (root/'.codex/config.toml').read_text()
            skill = root/'.agents/skills/abralia/SKILL.md'
            updated = skill.read_text() + '\nUpdated registration guidance.\n'
            with patch('abralia.backend.project.files') as resources:
                resources.return_value.joinpath.return_value.read_text.return_value = updated
                result = enable_project(root)
            self.assertEqual(result['reason'], 'skill_updated')
            self.assertEqual(skill.read_text(), updated)
            self.assertEqual((root/'.codex/config.toml').read_text(), config)
            self.assertTrue(inspect_project(root)['managed_skill_unchanged'])
            disable_project(root)
            self.assertFalse(skill.exists())

    def test_skill_refresh_does_not_overwrite_user_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enable_project(root)
            skill = root/'.agents/skills/abralia/SKILL.md'
            edited = skill.read_text() + '\nUser instruction.\n'
            skill.write_text(edited)
            with patch('abralia.backend.project.files') as resources:
                resources.return_value.joinpath.return_value.read_text.return_value = 'new template'
                with self.assertRaisesRegex(ValueError, 'preserving'):
                    enable_project(root)
            self.assertEqual(skill.read_text(), edited)

    def test_worker_failure_produces_nonzero_cli_exit_even_when_cleanup_succeeds(self):
        service=MagicMock()
        service.cleanup_error=None
        service.worker_error='fixture worker failure'
        service.project='/fixture'
        service.endpoint='/private/tmp/fixture.sock'
        service.stop_event.wait.return_value=True
        with patch('abralia.backend.cli.BrokerService',return_value=service), contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['serve','--project','/fixture','--profile',PROFILE])
        self.assertEqual(code,1)
        self.assertIn('fixture worker failure',output.getvalue())

    def test_enable_disable_preserves_unrelated_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.codex').mkdir()
            config = root / '.codex/config.toml'
            original = '# user settings\n[features]\nexample = true\n'
            config.write_text(original)
            result = enable_project(root)
            self.assertTrue(result['mcp_enabled'])
            self.assertTrue(result['skill_present'])
            self.assertEqual(enable_project(root)['reason'], 'already_enabled')
            self.assertTrue(config.read_text().startswith(original))
            disabled = disable_project(root)
            self.assertFalse(disabled['mcp_enabled'])
            self.assertEqual(config.read_text(), original)
            self.assertFalse((root/'.agents/skills/abralia/SKILL.md').exists())

    def test_disable_preserves_user_edits_and_never_changes_another_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one, two = root/'one', root/'two'
            one.mkdir(); two.mkdir()
            enable_project(one)
            self.assertFalse(inspect_project(two)['mcp_enabled'])
            skill = one/'.agents/skills/abralia/SKILL.md'
            skill.write_text(skill.read_text()+'\nUser addition.\n')
            disabled = disable_project(one)
            self.assertIn(str(skill.resolve()), disabled['preserved_user_files'])
            self.assertIn('User addition.', skill.read_text())

    def test_conflicting_config_and_symlinks_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'.codex').mkdir()
            config = root/'.codex/config.toml'
            original = '[mcp_servers.abralia_experiment]\ncommand="mine"\n'
            config.write_text(original)
            with self.assertRaises(ValueError): enable_project(root)
            self.assertEqual(config.read_text(), original)
            config.unlink()
            config.symlink_to(root/'user-config.toml')
            with self.assertRaises(ValueError): enable_project(root)


class BackendServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='abralia-test-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.socket = self.root/'broker.sock'
        self.service = BrokerService(self.root, PROFILE, endpoint=self.socket)
        self.service.start()

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def client(self, role='agent', project=None):
        return BrokerClient(project or self.root, endpoint=self.socket, role=role)

    def test_private_socket_project_isolation_and_missing_identity(self):
        self.assertEqual(self.socket.stat().st_mode & 0o777, 0o600)
        with self.client(project=self.root/'other') as wrong:
            self.assertEqual(wrong.request({'type':'ping'})['status'], 'skipped')
        with self.client() as client:
            result = client.request({'type':'call','operation':'get_status','arguments':{},'metadata':{}})
            self.assertEqual(result['reason'], 'caller_identity_unavailable')
            result = client.request({'type':'call','operation':'get_status','arguments':{},'metadata':{'thread_id':4}})
            self.assertEqual(result['status'],'rejected')
        self.assertFalse(self.service.stop_event.is_set())

    def test_admin_fixtures_and_simulated_input_cannot_be_model_calls(self):
        with self.client('admin') as admin, self.client() as agent:
            result = admin.request({'type':'admin','action':'seed_fixtures','count':25})
            self.assertEqual(result['fixtures'],25)
            admin.request({'type':'admin','action':'input','event':'toggle'})
            admin.request({'type':'admin','action':'input','event':'next_page'})
            result = admin.request({'type':'admin','action':'status'})
            self.assertEqual((result['page'], result['page_count']), (2,3))
            self.assertEqual(agent.request({'type':'admin','action':'input','event':'toggle'})['status'],'rejected')
            admin.request({'type':'admin','action':'clear_fixtures'})
            self.assertEqual(admin.request({'type':'admin','action':'status'})['slots'], [])

    def test_background_brightness_is_host_controlled(self):
        with self.client('admin') as admin, self.client() as agent:
            command={'type':'admin','action':'set_background','percent':25}
            self.assertEqual(agent.request(command)['status'],'rejected')
            self.assertEqual(admin.request(command)['background_brightness_percent'],25)
            self.assertEqual(admin.request({'type':'admin','action':'status'})['background_brightness_percent'],25)

    def test_keyboard_navigation_simulation_keeps_mode_after_confirmation(self):
        with self.client('admin') as admin:
            admin.request({'type':'admin','action':'seed_fixtures','count':25})
            for event in ('toggle', 'navigation_hold', 'last_agent', 'confirm'):
                self.assertEqual(admin.request({'type':'admin','action':'input','event':event})['status'], 'accepted')
            status = admin.request({'type':'admin','action':'status'})
            self.assertEqual((status['page'], status['selected_slot'], status['candidate_slot']), (3,25,25))
            self.assertTrue(status['navigation_active'])
            self.assertEqual(status['navigation_input']['source'], 'simulated')
            admin.request({'type':'admin','action':'input','event':'navigation_hold'})
            self.assertFalse(admin.request({'type':'admin','action':'status'})['navigation_active'])
            admin.request({'type':'admin','action':'clear_fixtures'})

    def test_duplicate_backend_does_not_preempt_existing_service(self):
        other = BrokerService(self.root,PROFILE,endpoint=self.socket)
        with self.assertRaises((OSError,RuntimeError)): other.start()
        with self.client() as client:
            self.assertEqual(client.request({'type':'ping'})['status'],'accepted')

    def test_shutdown_and_restart_changes_epoch(self):
        epoch = self.service.broker.epoch
        self.service.close()
        self.assertFalse(self.socket.exists())
        self.service = BrokerService(self.root,PROFILE,endpoint=self.socket).start()
        self.assertNotEqual(self.service.broker.epoch,epoch)

    def test_self_registration_survives_connection_replacement_but_not_backend_restart(self):
        metadata = {'thread_id':str(UUID(int=30))}
        message = {'type':'call', 'operation':'acquire_slot', 'metadata':metadata,
                   'arguments':{'label':'Self', 'harness':'codex_desktop', 'idempotency_key':'register'}}
        with self.client() as client:
            result = client.request(message)
            token = result['allocation']['slot_token']
        status = {'type':'call', 'operation':'get_status', 'metadata':metadata, 'arguments':{}}
        with self.client() as replacement:
            result = replacement.request(status)
            self.assertEqual(result['caller']['surface_source'], 'agent_reported')
            self.assertEqual(result['allocation']['slot_token'], token)
        self.service.close()
        self.service = BrokerService(self.root, PROFILE, endpoint=self.socket).start()
        with self.client() as restarted:
            result = restarted.request(status)
            self.assertEqual(result['caller']['surface'], 'unknown')
            self.assertNotIn('allocation', result)
            result = restarted.request(message)
            self.assertEqual(result['caller']['surface'], 'codex_desktop')
            self.assertNotEqual(result['allocation']['slot_token'], token)

    def test_optional_legacy_host_registration_remains_available(self):
        task_id = str(UUID(int=31))
        with self.client('admin') as admin:
            admin.request({'type':'admin','action':'register_desktop','thread_id':task_id})
        with self.client() as client:
            result = client.request({'type':'call','operation':'acquire_slot','metadata':{'thread_id':task_id},
                'arguments':{'label':'Legacy', 'harness':'codex_desktop', 'idempotency_key':'acquire'}})
        self.assertEqual(result['caller']['surface_source'], 'registered_by_host')

    def test_real_stdio_mcp_schema_identity_and_ownership(self):
        try:
            from mcp import Client, StdioServerParameters
            from mcp.types import RequestParamsMeta
        except ImportError:
            self.skipTest('install abralia-desktop[backend] to run MCP integration tests')

        async def scenario():
            parameters = StdioServerParameters(command=sys.executable,
                args=['-B','-m','abralia.backend.mcp','--project',str(self.root),'--socket',str(self.socket)],
                env={'PATH':os.environ.get('PATH',''), 'PYTHONDONTWRITEBYTECODE':'1'})
            one = {'x-codex-turn-metadata':{'thread_id':str(UUID(int=1))}}
            two = {'x-codex-turn-metadata':{'thread_id':str(UUID(int=2))}}
            async with Client(parameters) as client:
                inventory = await client.list_tools()
                self.assertEqual({t.name for t in inventory.tools},set(OPERATIONS))
                for tool in inventory.tools:
                    schema = tool.input_schema
                    self.assertNotIn('ctx',schema.get('properties',{}))
                    self.assertNotIn('caller_id',schema.get('properties',{}))
                    self.assertNotIn('thread_id',schema.get('properties',{}))
                acquire = next(t for t in inventory.tools if t.name == 'acquire_slot')
                self.assertIn('harness', acquire.input_schema['properties'])
                missing = await client.call_tool('get_status',{})
                self.assertEqual(missing.structured_content['reason'],'caller_identity_unavailable')
                result = await client.call_tool('acquire_slot',{'label':'Live MCP fixture','idempotency_key':'acquire'},meta=one)
                self.assertFalse(result.is_error, result)
                token = result.structured_content['allocation']['slot_token']
                result = await client.call_tool('get_status',{},meta=one)
                self.assertEqual(result.structured_content['caller']['identity_source'],'codex_metadata')
                self.assertEqual(result.structured_content['caller']['surface'],'unknown')
                self.assertEqual(result.structured_content['delivery'],'simulated')
                result = await client.call_tool('release_slot',{'slot_token':token,'idempotency_key':'stolen'},meta=two)
                self.assertEqual(result.structured_content['status'],'rejected')
                missing = await client.call_tool('acquire_slot', {'label':'No identity', 'harness':'codex_desktop',
                    'idempotency_key':'missing-identity'})
                self.assertEqual(missing.structured_content['reason'], 'caller_identity_unavailable')
                result = await client.call_tool('acquire_slot', {'label':'Live MCP fixture', 'harness':'codex_desktop',
                    'idempotency_key':'self-register'}, meta=one)
                self.assertEqual(result.structured_content['allocation']['slot_token'], token)
                self.assertEqual(result.structured_content['caller']['surface_source'], 'agent_reported')
                other = await client.call_tool('acquire_slot', {'label':'Other fixture', 'harness':'codex_cli',
                    'idempotency_key':'self-register'}, meta=two)
                self.assertNotEqual(other.structured_content['allocation']['slot_token'], token)
                self.assertEqual(other.structured_content['caller']['surface'], 'codex_cli')
                self.assertFalse(self.service.desktop_threads)
                result = await client.call_tool('report_question',{'slot_token':token,'question_id':'question-1',
                    'kind':'single_choice','options':[{'id':'a','label':'A'},{'id':'b','label':'B'}],
                    'allow_other':True,'idempotency_key':'question'},meta=one)
                self.assertEqual(result.structured_content['allocation']['question']['highlight_keys'][2]['action'],'other')
                with self.client('admin') as admin:
                    admin.request({'type':'admin','action':'input','event':'toggle'})
                    admin.request({'type':'admin','action':'input','event':'pickup'})
                result = await client.call_tool('get_status',{'slot_token':token},meta=one)
                self.assertTrue(result.structured_content['allocation']['question']['picked_up'])
                self.assertEqual(result.structured_content['caller']['surface_source'],'agent_reported')
                picked = [e for e in result.structured_content['events'] if e['kind']=='call_picked_up']
                self.assertEqual(picked[-1]['focus'],'requested')
                result = await client.call_tool('clear_question',{'slot_token':token,'question_id':'question-1',
                    'outcome':'answered','idempotency_key':'answered'},meta=one)
                self.assertIsNone(result.structured_content['allocation']['question'])
                result = await client.call_tool('release_slot',{'slot_token':token,'idempotency_key':'release'},meta=one)
                self.assertEqual(result.structured_content['status'],'accepted')
                await client.call_tool('release_slot',{'slot_token':other.structured_content['allocation']['slot_token'],
                    'idempotency_key':'release'},meta=two)
        asyncio.run(scenario())

    def test_missing_backend_is_bounded_skipped(self):
        with BrokerClient(self.root,endpoint=self.root/'missing.sock') as client:
            self.assertEqual(client.request({'type':'ping'})['reason'],'backend_unavailable')


if __name__ == '__main__':
    unittest.main()
