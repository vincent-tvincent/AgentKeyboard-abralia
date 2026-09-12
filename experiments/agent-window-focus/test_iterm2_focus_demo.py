# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Offline iTerm2 focus tests; API connections and native UI are not used."""

import contextlib
import io
import json
import types
import unittest
from unittest.mock import Mock, patch

import iterm2_focus_demo as demo


class IdentityTests(unittest.TestCase):
    def test_selected_identity(self):
        session = types.SimpleNamespace(session_id="session-1")
        tab = types.SimpleNamespace(tab_id="tab-1", current_session=session)
        window = types.SimpleNamespace(window_id="window-1", current_tab=tab)
        app = types.SimpleNamespace(current_terminal_window=window)
        self.assertEqual(
            demo.selected_identity(app),
            {
                "window_id": "window-1",
                "tab_id": "tab-1",
                "session_id": "session-1",
            },
        )

    def test_missing_window_is_explicit(self):
        app = types.SimpleNamespace(current_terminal_window=None)
        self.assertEqual(
            demo.selected_identity(app),
            {"window_id": None, "tab_id": None, "session_id": None},
        )


class EventTests(unittest.TestCase):
    def test_application_event(self):
        update = types.SimpleNamespace(
            application_active=types.SimpleNamespace(application_active=False),
            window_changed=None,
            selected_tab_changed=None,
            active_session_changed=None,
        )
        event = demo.event_record(update)
        self.assertEqual(event, {"kind": "application_active", "active": False})
        self.assertTrue(demo.saw_application_state([event], False))

    def test_window_event_requires_exact_id_and_reason(self):
        update = types.SimpleNamespace(
            application_active=None,
            window_changed=types.SimpleNamespace(
                window_id="window-1",
                event=types.SimpleNamespace(name="TERMINAL_WINDOW_BECAME_KEY"),
            ),
            selected_tab_changed=None,
            active_session_changed=None,
        )
        event = demo.event_record(update)
        self.assertTrue(
            demo.saw_window_reason(
                [event], "window-1", "TERMINAL_WINDOW_BECAME_KEY"
            )
        )
        self.assertFalse(
            demo.saw_window_reason(
                [event], "window-2", "TERMINAL_WINDOW_BECAME_KEY"
            )
        )


class CliTests(unittest.TestCase):
    def test_outer_timeout_is_fail_closed(self):
        with self.assertRaisesRegex(demo.FocusError, "exceeded 20s"):
            demo.connection_timeout(0, None)

    def test_client_system_exit_becomes_structured_connection_error(self):
        fake_client = types.SimpleNamespace(
            run_until_complete=Mock(side_effect=SystemExit(1))
        )
        with patch.object(demo, "iterm2", fake_client), self.assertRaisesRegex(
            demo.FocusError, "could not connect"
        ):
            demo.run_experiment(0)

    @patch.object(demo, "run_experiment")
    def test_dry_run_does_not_connect(self, experiment):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(demo.main(["--dry-run"]), 0)
        experiment.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["result"], "DRY_RUN")

    @patch.object(demo.sys, "platform", "darwin")
    @patch.object(demo, "run_experiment", side_effect=demo.FocusError("failed"))
    def test_error_is_nonzero(self, experiment):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(demo.main([]), 1)
        self.assertEqual(json.loads(output.getvalue())["result"], "ERROR")


if __name__ == "__main__":
    unittest.main()
