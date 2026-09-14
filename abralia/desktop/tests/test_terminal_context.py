# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.client_lifetime import (TERMINAL_ENV_VARS, capture_client_owner,
    capture_terminal_context, normalize_terminal_context, process_identity, owner_alive)
from abralia.backend.codex_context import native_task_source
from abralia.backend.ipc import BrokerClient


OWNER = {'pid': 101, 'parent': 100, 'started': 'owner-generation', 'tty': 'ttys003', 'executable': 'codex', 'kind': 'codex_tui'}
SHELL = {'pid': 100, 'parent': 10, 'started': 'shell-generation', 'tty': 'ttys003', 'executable': '/bin/zsh'}
GHOSTTY = {'pid': 10, 'parent': 1, 'started': 'app-generation', 'tty': '??', 'executable': '/Applications/Ghostty.app/Contents/MacOS/ghostty'}


class TerminalContextTests(unittest.TestCase):
    def processes(self, *, owner=OWNER, shell=SHELL, app=GHOSTTY):
        rows = {row['pid']: row for row in (owner, shell, app)}
        return patch('abralia.backend.client_lifetime.process_identity', side_effect=rows.get)

    def test_provider_ancestry_and_only_allowlisted_environment(self):
        cases = [('ghostty', GHOSTTY['executable'], {}),
                 ('terminal', '/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal', {}),
                 ('iterm2', '/Applications/iTerm.app/Contents/MacOS/iTerm2', {'ITERM_SESSION_ID': 'w0t0p0:'+str(UUID(int=1))}),
                 ('wezterm', '/usr/bin/wezterm-gui', {'WEZTERM_PANE': '2', 'WEZTERM_UNIX_SOCKET': '/tmp/wezterm.sock'}),
                 ('kitty', '/usr/bin/kitty', {'KITTY_WINDOW_ID': '4', 'KITTY_LISTEN_ON': 'unix:/tmp/kitty.sock'}),
                 ('vscode', '/Applications/Visual Studio Code.app/Contents/MacOS/Electron', {'VSCODE_IPC_HOOK_CLI': '/tmp/vscode.sock'})]
        for provider, executable, hints in cases:
            with self.subTest(provider=provider), self.processes(app={**GHOSTTY, 'executable': executable}):
                result = capture_terminal_context(OWNER, environment={**hints, 'OPENAI_API_KEY': 'never forward', 'COMMAND': 'never forward'})
                self.assertEqual(result['provider'], provider)
                self.assertEqual(result['environment'], hints)
                self.assertEqual(result['owner'], OWNER)
                self.assertTrue(normalize_terminal_context(result, verify=True))

    def test_gui_mux_remote_and_unreadable_ancestry_are_unavailable(self):
        with self.processes():
            self.assertIsNone(capture_terminal_context({**OWNER, 'kind': 'codex_gui'}, environment={}))
        for executable in ('tmux: server', '/usr/sbin/sshd', '/usr/bin/screen', '/usr/bin/mosh-server'):
            with self.subTest(executable=executable), self.processes(shell={**SHELL, 'executable': executable}):
                self.assertIsNone(capture_terminal_context(OWNER, environment={'GHOSTTY_SURFACE_ID': '0x123'}))
        with patch('abralia.backend.client_lifetime.process_identity', side_effect=lambda pid: OWNER if pid == OWNER['pid'] else None):
            self.assertIsNone(capture_terminal_context(OWNER, environment={'TERM_PROGRAM': 'ghostty'}))

    def test_structural_validation_rejects_commands_and_stale_app_owner(self):
        with self.processes():
            captured = capture_terminal_context(OWNER, environment={})
        self.assertIsNotNone(normalize_terminal_context(captured))
        self.assertIsNone(normalize_terminal_context({**captured, 'command': 'open arbitrary'}))
        self.assertIsNone(normalize_terminal_context({**captured, 'environment': {'SECRET': 'value'}}))
        self.assertIsNone(normalize_terminal_context({**captured, 'environment': {'KITTY_LISTEN_ON': 'tcp:other-host:99'}}))
        with self.processes(app={**GHOSTTY, 'started': 'replacement'}):
            self.assertIsNone(normalize_terminal_context(captured, verify=True))
        with self.processes(owner={**OWNER, 'started': 'replacement'}):
            self.assertIsNone(normalize_terminal_context(captured, verify=True))
        with self.processes(owner={**OWNER, 'parent': 999}):
            self.assertIsNone(normalize_terminal_context(captured, verify=True))
            self.assertIsNone(capture_terminal_context(OWNER, environment={}))

    def test_vscode_helper_is_skipped_for_main_window_process(self):
        helper = {**SHELL, 'executable': '/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin).app/Contents/MacOS/Code Helper (Plugin)'}
        main = {**GHOSTTY, 'executable': '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'}
        with self.processes(shell=helper, app=main):
            result = capture_terminal_context(OWNER, environment={'VSCODE_IPC_HOOK_CLI': '/tmp/window.sock'})
            self.assertEqual(result['provider'], 'vscode')
            self.assertEqual(result['app']['pid'], main['pid'])

    def test_nontty_vscode_extension_has_distinct_editor_owner(self):
        editor = {**OWNER, 'tty': '??', 'kind': 'codex_editor'}
        helper = {**SHELL, 'tty': '??', 'executable': '/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin).app/Contents/MacOS/Code Helper (Plugin)'}
        main = {**GHOSTTY, 'executable': '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'}
        with self.processes(owner=editor, shell=helper, app=main):
            captured = capture_client_owner(editor['pid'])
            self.assertEqual(captured['kind'], 'codex_editor')
            self.assertTrue(owner_alive(captured))
            context = capture_terminal_context(captured, environment={})
            self.assertEqual(context['provider'], 'vscode')
            self.assertEqual(context['app']['pid'], main['pid'])
            self.assertIsNone(normalize_terminal_context({**context, 'provider': 'ghostty', 'app': GHOSTTY}))
        with self.processes(owner={**editor, 'parent': 999}, shell=helper, app=main):
            self.assertFalse(owner_alive(editor))
        with self.processes(owner=editor, shell=helper, app={**main, 'executable': '/Applications/Other.app/Contents/MacOS/Other'}):
            self.assertFalse(owner_alive(editor))

    def test_backend_fallback_empty_environment_does_not_read_backend_hints(self):
        with self.processes(), patch.dict(os.environ, {'VSCODE_IPC_HOOK_CLI': '/tmp/other-window.sock'}):
            self.assertEqual(capture_terminal_context(OWNER, environment={})['environment'], {})

    def test_windows_identity_dispatch_and_missing_tty_need_native_locator(self):
        owner = {**OWNER, 'tty': '?', 'executable': r'C:\Tools\codex.exe'}
        app = {**GHOSTTY, 'executable': r'C:\Apps\WindowsTerminal.exe'}
        with patch('abralia.backend.client_lifetime.sys.platform', 'win32'), patch('abralia.backend.client_lifetime._windows_process_identity', return_value=owner):
            self.assertEqual(process_identity(101), owner)
        with patch('abralia.backend.client_lifetime.sys.platform', 'win32'), self.processes(owner=owner, app=app):
            self.assertIsNone(capture_terminal_context(owner, environment={}))
            context = capture_terminal_context(owner, environment={'WT_SESSION': str(UUID(int=7))})
            self.assertEqual(context['provider'], 'windows_terminal')
            with patch.dict(os.environ, {'WT_SESSION': str(UUID(int=7))}, clear=True):
                self.assertEqual(capture_client_owner(101)['kind'], 'codex_tui')

    def test_ipc_context_is_transport_metadata_not_model_arguments(self):
        with tempfile.TemporaryDirectory() as root, self.processes():
            with patch('abralia.backend.ipc.capture_terminal_context', return_value={'provider': 'fixture'}) as capture:
                client = BrokerClient(root, recover_slots=True, recovery_owner=OWNER)
                self.assertEqual(client.terminal_context, {'provider': 'fixture'})
                capture.assert_called_once_with(OWNER)
                client.close()
                admin = BrokerClient(root, role='admin')
                self.assertIsNone(admin.terminal_context)
                self.assertEqual(capture.call_count, 1)
                admin.close()

    def test_plugin_explicitly_forwards_native_terminal_hints(self):
        payload = Path(__file__).resolve().parents[1]/'plugin-bundle/plugins/abralia/.mcp.json'
        server = json.loads(payload.read_text())['mcpServers']['abralia']
        self.assertEqual(set(server['env_vars']), set(TERMINAL_ENV_VARS))


class NativeTaskSourceTests(unittest.TestCase):
    def test_index_distinguishes_cli_root_subagent_and_archived(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            ids = [str(UUID(int=n)) for n in range(1, 5)]
            with closing(sqlite3.connect(home/'state_5.sqlite')) as db, db:
                db.execute('CREATE TABLE threads(id TEXT, source TEXT, archived INTEGER)')
                db.executemany('INSERT INTO threads VALUES(?,?,?)', [(ids[0], 'cli', 0),
                    (ids[1], json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': ids[0]}}}), 0),
                    (ids[2], 'vscode', 0), (ids[3], 'cli', 1)])
            self.assertEqual([native_task_source(task, codex_home=home) for task in ids], ['cli', 'subagent', 'vscode', 'unknown'])
            self.assertEqual(native_task_source(str(UUID(int=9)), codex_home=home), 'unknown')

    def test_first_record_fallback_checks_task_id_and_never_uses_title(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root); task = str(UUID(int=1))
            folder = home/'sessions/2026/09/14'; folder.mkdir(parents=True)
            path = folder/f'rollout-date-{task}.jsonl'
            path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': task, 'source': 'cli'}})+'\nprivate conversation\n')
            self.assertEqual(native_task_source(task, codex_home=home), 'cli')
            path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': str(UUID(int=2)), 'source': 'cli'}}))
            self.assertEqual(native_task_source(task, codex_home=home), 'unknown')


if __name__ == '__main__':
    unittest.main()
