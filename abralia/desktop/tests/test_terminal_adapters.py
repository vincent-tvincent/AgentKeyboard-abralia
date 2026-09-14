# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from abralia.backend import terminal_adapters as adapters


OWNER = {'pid': 404, 'started': 'start', 'tty': 'ttys007',
         'executable': '/fixture/codex', 'kind': 'codex_tui'}


class TerminalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.alive = patch.object(adapters, 'owner_alive', return_value=True).start()
        self.process = patch.object(adapters, 'process_identity').start()
        self.run = patch.object(adapters, '_run').start()
        self.cli = patch.object(adapters, '_cli', side_effect=lambda provider, context: '/native/' + provider).start()
        patch.object(adapters.sys, 'platform', 'darwin').start()

    def context(self, provider, **extra):
        names = {'terminal': 'Terminal', 'iterm2': 'iTerm2', 'ghostty': 'ghostty',
                 'wezterm': 'wezterm-gui', 'kitty': 'kitty'}
        app = {'pid': 100, 'started': 'app-start', 'executable': '/Applications/App.app/Contents/MacOS/' + names[provider]}
        self.process.return_value = dict(app)
        return {'provider': provider, 'owner': deepcopy(OWNER), 'app': app, **extra}

    def test_terminal_selects_exact_tty_and_keeps_title_cwd_out_of_routing(self):
        context = self.context('terminal', title='same as another tab', cwd='/shared/project')
        self.run.side_effect = ['found:17', 'verified']
        result = adapters.focus_terminal(context)
        self.assertEqual(result['status'], 'focused')
        self.assertEqual(result['verification'], 'surface_verified')
        scan, focus = self.run.call_args_list
        self.assertEqual(scan.args[0][-3:], ['/dev/ttys007', '', 'scan'])
        self.assertEqual(focus.args[0][-3:], ['/dev/ttys007', '17', 'focus'])
        self.assertNotIn('/shared/project', str(self.run.call_args_list))
        self.assertNotIn('same as another tab', str(self.run.call_args_list))
        self.assertIn('set selected of targetTab to true', focus.kwargs['script'])
        self.assertIn('runningApplicationWithProcessIdentifier', focus.kwargs['script'])

    def test_iterm_selects_session_tab_and_window_and_checks_native_session_identity(self):
        self.run.side_effect = ['found:session-id', 'verified']
        result = adapters.focus_terminal(self.context('iterm2', environment={'ITERM_SESSION_ID': 'w0t0p0:session-id'}))
        self.assertEqual(result['status'], 'focused')
        script = self.run.call_args.kwargs['script']
        for command in ('tell targetSession to select', 'tell targetTab to select', 'tell targetWindow to select'):
            self.assertIn(command, script)
        self.run.reset_mock(); self.run.side_effect = ['found:different-session']
        result = adapters.focus_terminal(self.context('iterm2', environment={'ITERM_SESSION_ID': 'w0t0p0:session-id'}))
        self.assertEqual(result['reason'], 'terminal_native_identity_mismatch')
        self.assertEqual(self.run.call_count, 1)

    def test_owner_is_rechecked_after_enumeration_before_dispatch(self):
        context = self.context('terminal')
        self.alive.side_effect = [True, True, False]
        self.run.return_value = 'found:17'
        result = adapters.focus_terminal(context)
        self.assertEqual(result['reason'], 'terminal_owner_stale')
        self.assertEqual(self.run.call_count, 1)

    def test_reused_app_pid_or_missing_app_does_not_launch_a_terminal(self):
        context = self.context('terminal')
        self.process.return_value = {**context['app'], 'started': 'replacement'}
        self.assertEqual(adapters.focus_terminal(context)['reason'], 'terminal_app_stale')
        self.run.assert_not_called()
        context.pop('app')
        self.assertEqual(adapters.focus_terminal(context)['reason'], 'terminal_app_identity_required')
        self.run.assert_not_called()

    def test_ambiguous_native_tabs_never_trigger_dispatch(self):
        self.run.return_value = 'unavailable:terminal_surface_not_unique'
        result = adapters.focus_terminal(self.context('terminal'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(self.run.call_count, 1)

    def test_ghostty_new_tty_api_uses_native_surface_and_old_single_surface_is_explicit(self):
        self.run.side_effect = ['found:ghostty-uuid', 'verified']
        result = adapters.focus_terminal(self.context('ghostty'))
        self.assertEqual(result['verification'], 'surface_verified')
        self.assertIn('«class Gtty»', self.run.call_args.kwargs['script'])
        self.run.reset_mock(); self.run.side_effect = ['single:ghostty-uuid', 'single_dispatched']
        with patch.object(adapters, '_ancestor', return_value=True):
            result = adapters.focus_terminal(self.context('ghostty'))
        self.assertEqual((result['status'], result['specificity'], result['verification']),
                         ('focused', 'window', 'dispatched_unverified'))

    def test_ghostty_old_multiple_surfaces_or_unverified_ancestry_are_unavailable(self):
        self.run.return_value = 'unavailable:ghostty_tty_api_or_unique_surface_required'
        result = adapters.focus_terminal(self.context('ghostty'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(self.run.call_count, 1)
        self.run.reset_mock(); self.run.return_value = 'single:only-terminal'
        with patch.object(adapters, '_ancestor', return_value=False):
            result = adapters.focus_terminal(self.context('ghostty'))
        self.assertEqual(result['reason'], 'ghostty_single_surface_owner_unverified')
        self.assertEqual(self.run.call_count, 1)

    def test_unsupported_native_platform_does_not_invoke_applescript(self):
        for provider in ('ghostty', 'terminal', 'iterm2'):
            with self.subTest(provider=provider), patch.object(adapters.sys, 'platform', 'linux'):
                self.assertEqual(adapters.focus_terminal(self.context(provider))['reason'], 'terminal_platform_unsupported')
        self.run.assert_not_called()

    def test_wezterm_matches_tty_even_with_identical_titles_and_cwds(self):
        context = self.context('wezterm', environment={'WEZTERM_UNIX_SOCKET': '/native/socket'})
        rows = [{'pane_id': 2, 'tty_name': '/dev/ttys008', 'title': 'same', 'cwd': '/same'},
                {'pane_id': 9, 'tty_name': '/dev/ttys007', 'title': 'same', 'cwd': '/same'}]
        after = [{**row, 'is_active': row['pane_id'] == 9} for row in rows]
        self.run.side_effect = [json.dumps(rows), '', json.dumps(after)]
        with patch.object(adapters, '_activate_app', return_value=True):
            result = adapters.focus_terminal(context)
        self.assertEqual(result['verification'], 'dispatched_unverified')
        self.assertEqual(result['reason'], 'pane_selected_os_window_unverified')
        self.assertEqual(self.run.call_args_list[1].args[0],
                         ['/native/wezterm', 'cli', '--no-auto-start', 'activate-pane', '--pane-id', '9'])
        self.assertEqual(self.run.call_args_list[0].kwargs['env'], {'WEZTERM_UNIX_SOCKET': '/native/socket'})

    def test_wezterm_rejects_stale_native_pane_hint_instead_of_falling_back_to_title(self):
        rows = [{'pane_id': 9, 'tty_name': '/dev/ttys007', 'title': 'same'}]
        self.run.return_value = json.dumps(rows)
        result = adapters.focus_terminal(self.context('wezterm', environment={'WEZTERM_PANE': '3'}))
        self.assertEqual(result['reason'], 'terminal_surface_not_unique')
        self.assertEqual(self.run.call_count, 1)

    def test_wezterm_windows_native_pane_dispatch_is_not_claimed_as_foreground_proof(self):
        context = self.context('wezterm', environment={'WEZTERM_PANE': '9'})
        context['owner']['tty'] = None
        self.run.side_effect = [json.dumps([{'pane_id': 9, 'tty_name': None}]), '',
                                json.dumps([{'pane_id': 9, 'tty_name': None, 'is_active': True}])]
        with patch.object(adapters.sys, 'platform', 'win32'):
            result = adapters.focus_terminal(context)
        self.assertEqual(result['status'], 'focused')
        self.assertEqual(result['verification'], 'dispatched_unverified')

    def test_kitty_requires_existing_local_control_socket_and_matching_foreground_pid(self):
        context = self.context('kitty', environment={'KITTY_LISTEN_ON': 'unix:/native/kitty', 'KITTY_WINDOW_ID': '5'})
        row = {'id': 5, 'pid': 300, 'foreground_processes': [{'pid': 404}]}
        rows = [{'tabs': [{'windows': [row, {'id': 8, 'pid': 999}]}]}]
        after = [{'tabs': [{'windows': [{**row, 'is_focused': True}]}]}]
        self.run.side_effect = [json.dumps(rows), '', json.dumps(after)]
        with patch.object(adapters, '_activate_app', return_value=True):
            result = adapters.focus_terminal(context)
        self.assertEqual(result['verification'], 'surface_verified')
        self.assertEqual(self.run.call_args_list[1].args[0],
                         ['/native/kitty', '@', '--to', 'unix:/native/kitty', 'focus-window', '--match', 'id:5'])

    def test_kitty_does_not_enable_remote_control_or_use_a_remote_server(self):
        for environment in ({}, {'KITTY_LISTEN_ON': 'tcp:example.org:8888'}):
            result = adapters.focus_terminal(self.context('kitty', environment=environment))
            self.assertEqual(result['reason'], 'kitty_local_remote_control_socket_required')
        self.run.assert_not_called()

    def test_kitty_same_native_id_with_wrong_owner_is_not_selected(self):
        self.run.return_value = json.dumps([{'tabs': [{'windows': [{'id': 5, 'pid': 999}]}]}])
        result = adapters.focus_terminal(self.context('kitty', environment={
            'KITTY_LISTEN_ON': 'unix:/native/kitty', 'KITTY_WINDOW_ID': '5'}))
        self.assertEqual(result['reason'], 'terminal_owner_surface_not_unique')
        self.assertEqual(self.run.call_count, 1)

    def test_malformed_context_and_environment_fail_without_commands(self):
        self.assertEqual(adapters.focus_terminal({'provider': []})['status'], 'unavailable')
        result = adapters.focus_terminal(self.context('wezterm', environment={'WEZTERM_PANE': '4\n5'}))
        self.assertEqual(result['reason'], 'invalid_terminal_environment')
        self.run.assert_not_called()


class NativeCommandBoundaryTests(unittest.TestCase):
    def test_native_command_is_bounded_and_permission_failure_is_explicit(self):
        with patch.object(adapters.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='Not authorized (-1743)')) as run:
            with self.assertRaisesRegex(adapters.Unavailable, 'permission_required'):
                adapters._run(['/usr/bin/osascript', '-'], script='native selection')
        self.assertEqual(run.call_args.kwargs['timeout'], 2)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_timeout_and_oversize_response_are_not_focus_success(self):
        with patch.object(adapters.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 2)):
            with self.assertRaisesRegex(adapters.Unavailable, 'timeout'):
                adapters._run(['/native/wezterm', 'cli', '--no-auto-start', 'list'])
        with patch.object(adapters.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='x' * (adapters.MAX_OUTPUT + 1), stderr='')):
            with self.assertRaisesRegex(adapters.Unavailable, 'too_large'):
                adapters._run(['/native/wezterm', 'cli', '--no-auto-start', 'list'])

    def test_owner_environment_overrides_only_allowed_routing_keys(self):
        with patch.dict(adapters.os.environ, {'WEZTERM_PANE': 'wrong', 'KITTY_LISTEN_ON': 'wrong'}), \
             patch.object(adapters.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]', stderr='')) as run:
            adapters._run(['/native/wezterm', 'cli', '--no-auto-start', 'list'], env={'WEZTERM_PANE': '9'})
        self.assertEqual(run.call_args.kwargs['env']['WEZTERM_PANE'], '9')
        self.assertNotIn('KITTY_LISTEN_ON', run.call_args.kwargs['env'])


if __name__ == '__main__':
    unittest.main()
