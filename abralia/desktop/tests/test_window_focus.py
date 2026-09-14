# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Native APIs are injected; these tests never focus an actual window."""

import unittest
from unittest.mock import Mock, patch

from abralia.backend.window_focus import focus_window


class WindowFocusTests(unittest.TestCase):
    def setUp(self):
        self.app = {'pid': 222, 'started': 'captured-start', 'executable': '/usr/share/code/code'}
        self.context = {'provider': 'vscode', 'app': self.app,
                        'owner': {'pid': 333, 'kind': 'codex_tui'}, 'environment': {}}
        self.identity = patch('abralia.backend.window_focus.process_identity', side_effect=lambda _pid: dict(self.app))
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_environment_pid_alone_does_not_identify_a_window(self):
        self.context.pop('app')
        self.context['environment'] = {'VSCODE_PID': '222', 'VSCODE_IPC_HOOK_CLI': '/tmp/possible.sock'}
        result = focus_window(self.context)
        self.assertEqual(result['reason'], 'captured_app_identity_required')
        self.assertEqual(result['verification'], 'not_dispatched')

    def test_reused_pid_is_rejected_before_native_window_calls(self):
        with patch('abralia.backend.window_focus.process_identity', return_value={**self.app, 'started': 'new-process'}), \
             patch('abralia.backend.window_focus._focus_windows') as focus:
            result = focus_window(self.context)
        self.assertEqual(result['reason'], 'app_process_identity_changed')
        focus.assert_not_called()

    def test_wrong_app_executable_is_rejected(self):
        self.app['executable'] = '/usr/bin/python'
        self.assertEqual(focus_window(self.context)['reason'], 'captured_app_identity_required')

    def mac(self, native):
        self.app['executable'] = '/Applications/Visual Studio Code.app/Contents/MacOS/Electron'
        with patch('abralia.backend.window_focus.sys.platform', 'darwin'), \
             patch('abralia.backend.window_focus._Accessibility', return_value=native):
            return focus_window(self.context)

    def test_macos_missing_existing_permission_does_not_request_or_raise(self):
        native = Mock()
        native.trusted.return_value = False
        result = self.mac(native)
        self.assertEqual(result['reason'], 'accessibility_permission_required')
        native.windows.assert_not_called()
        native.raise_window.assert_not_called()
        native.close.assert_called_once()

    def test_macos_multiple_windows_are_not_guessed(self):
        native = Mock()
        native.trusted.return_value = True
        native.windows.return_value = 2
        result = self.mac(native)
        self.assertEqual(result['reason'], 'multiple_app_windows')
        native.raise_window.assert_not_called()

    def test_macos_unique_window_requires_foreground_focused_window_readback(self):
        native = Mock()
        native.trusted.return_value = True
        native.windows.return_value = 1
        native.raise_window.return_value = True
        native.confirmed.return_value = True
        result = self.mac(native)
        self.assertEqual(result['status'], 'focused')
        self.assertEqual(result['specificity'], 'window')
        self.assertEqual(result['verification'], 'foreground_window_verified')
        native.windows.assert_called_once_with(222)
        native.raise_window.assert_called_once()

    def test_macos_failed_readback_retains_dispatched_boundary(self):
        native = Mock()
        native.trusted.return_value = True
        native.windows.return_value = 1
        native.raise_window.return_value = True
        native.confirmed.side_effect = OSError('native application closed')
        result = self.mac(native)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['verification'], 'dispatched_unverified')

    def windows(self, native, provider='windows_terminal'):
        self.context['provider'] = provider
        self.app['executable'] = r'C:\Apps\WindowsTerminal.exe' if provider == 'windows_terminal' else r'C:\Apps\Code.exe'
        self.context['environment'] = {'WT_SESSION': 'not-a-window-id'}
        with patch('abralia.backend.window_focus.sys.platform', 'win32'), \
             patch('abralia.backend.window_focus._Windows', return_value=native):
            return focus_window(self.context)

    def test_windows_terminal_raises_unique_app_hwnd_but_never_claims_tab(self):
        native = Mock()
        native.windows.return_value = [100]
        native.belongs.return_value = True
        native.raise_window.return_value = True
        native.foreground.return_value = 100
        result = self.windows(native)
        self.assertEqual(result['reason'], 'window_only_tab_not_selected')
        self.assertEqual(result['status'], 'focused')
        native.raise_window.assert_called_once_with(100)

    def test_windows_multiple_windows_do_not_choose_mru_or_title(self):
        native = Mock()
        native.windows.return_value = [100, 200]
        result = self.windows(native)
        self.assertEqual(result['reason'], 'multiple_app_windows')
        native.raise_window.assert_not_called()

    def test_windows_foreground_restrictions_are_not_bypassed(self):
        native = Mock()
        native.windows.return_value = [100]
        native.belongs.return_value = True
        native.raise_window.return_value = False
        native.foreground.return_value = 200
        result = self.windows(native)
        self.assertEqual(result['reason'], 'foreground_request_denied')
        self.assertEqual(result['status'], 'failed')

    def test_windows_already_foreground_is_verified_even_if_raise_returns_false(self):
        native = Mock()
        native.windows.return_value = [100]
        native.belongs.return_value = True
        native.raise_window.return_value = False
        native.foreground.return_value = 100
        result = self.windows(native, provider='vscode')
        self.assertEqual(result['status'], 'focused')
        self.assertEqual(result['reason'], 'window_only')

    def linux(self, run, *, environment=None):
        with patch('abralia.backend.window_focus.sys.platform', 'linux'), \
             patch.dict('os.environ', environment or {'DISPLAY': ':1', 'XDG_SESSION_TYPE': 'x11'}, clear=True), \
             patch('abralia.backend.window_focus.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
             patch('abralia.backend.window_focus._run', side_effect=run):
            return focus_window(self.context)

    def test_linux_exact_pid_and_window_id_ignore_all_window_titles(self):
        calls = []
        def run(argv):
            calls.append(argv)
            if argv[-1] == '-lp':
                return '0x00100001 0 111 host Secret workspace title\n0x00200002 0 222 host Another private title\n'
            if argv[-1] == '_NET_WM_PID':
                return '_NET_WM_PID(CARDINAL) = 222\n'
            if argv[-1] == '_NET_ACTIVE_WINDOW':
                return '_NET_ACTIVE_WINDOW(WINDOW): window id # 0x200002\n'
            return ''
        result = self.linux(run)
        self.assertEqual(result['status'], 'focused')
        self.assertIn(['/usr/bin/wmctrl', '-ia', '0x00200002'], calls)
        self.assertNotIn('private', str(calls))
        self.assertNotIn('Secret', str(result))

    def test_linux_ambiguous_or_changed_pid_never_activates(self):
        for inventory, observed_pid in (
            ('0x1 0 222 host A\n0x2 0 222 host B\n', 222),
            ('0x1 0 222 host A\n', 999),
        ):
            calls = []
            def run(argv):
                calls.append(argv)
                return inventory if argv[-1] == '-lp' else f'_NET_WM_PID(CARDINAL) = {observed_pid}\n'
            result = self.linux(run)
            self.assertEqual(result['status'], 'unavailable')
            self.assertFalse(any('-ia' in argv for argv in calls))

    def test_linux_missing_readback_does_not_claim_focus(self):
        def run(argv):
            if argv[-1] == '-lp':
                return '0x1 0 222 host ignored\n'
            if argv[-1] == '_NET_WM_PID':
                return '_NET_WM_PID(CARDINAL) = 222\n'
            if argv[-1] == '_NET_ACTIVE_WINDOW':
                raise OSError('property unavailable')
            return ''
        result = self.linux(run)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['verification'], 'dispatched_unverified')

    def test_wayland_without_exact_native_api_is_unavailable(self):
        native = Mock(side_effect=AssertionError('No X11 fallback on Wayland'))
        result = self.linux(native, environment={'DISPLAY': ':1', 'WAYLAND_DISPLAY': 'wayland-0'})
        self.assertEqual(result['reason'], 'wayland_window_focus_unavailable')
        native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
