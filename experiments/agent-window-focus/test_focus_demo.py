# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Offline only: mock native commands, never move desktop focus."""

import argparse
import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

import focus_demo as demo


TASK = "00000000-0000-4000-8000-000000000001"
SURFACE = "00000000-0000-4000-8000-000000000002"


class InputTests(unittest.TestCase):
    def test_uuid_and_existing_task_link(self):
        self.assertEqual(demo.thread_id(TASK), TASK)
        self.assertEqual(demo.thread_id(f"codex://threads/{TASK}"), TASK)

    def test_other_routes_and_injected_data_rejected(self):
        for target in (
            "codex://threads/new", "codex://settings", f"{TASK}?prompt=hello",
            f"{TASK}/", f"{TASK}#fragment", f"{TASK};open other", "https://example.com",
        ):
            with self.subTest(target=target), self.assertRaises(argparse.ArgumentTypeError):
                demo.thread_id(target)

    def test_terminal_id_is_opaque_safe_argument(self):
        self.assertEqual(demo.terminal_id(SURFACE), SURFACE)
        for target in ("", 'x" & do shell script "x', "a\nb", "a/b", "a" * 129):
            with self.subTest(target=target), self.assertRaises(argparse.ArgumentTypeError):
                demo.terminal_id(target)

    def test_delay_is_bounded_and_finite(self):
        for value in ("0", "3.5", "30"):
            self.assertEqual(demo.delay_seconds(value), float(value))
        for value in ("-1", "31", "nan", "inf", "abc"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                demo.delay_seconds(value)


class NativeCommandTests(unittest.TestCase):
    @patch.object(demo.subprocess, "run")
    def test_command_is_argv_with_timeout_and_no_shell(self, run):
        run.return_value.stdout = "true\twin\ttab\tterm\n"
        self.assertEqual(demo.ghostty("current"), "true\twin\ttab\tterm")
        run.assert_called_once_with(
            ["/usr/bin/osascript", str(demo.GHOSTTY_SCRIPT), "current", ""],
            capture_output=True, text=True, timeout=8.0, check=True,
        )

    @patch.object(demo.subprocess, "run")
    def test_empty_window_fields_are_preserved(self, run):
        run.return_value.stdout = "false\t\t\t\n"
        self.assertEqual(demo.run_command(["native"]), "false\t\t\t")

    @patch.object(demo.subprocess, "run")
    def test_command_failure_is_not_silenced(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["native"], stderr="Denied")
        with self.assertRaisesRegex(demo.FocusError, "Denied"):
            demo.run_command(["native"])
        self.assertEqual(run.call_count, 1)

    @patch.object(demo.subprocess, "run")
    def test_timeout_does_not_retry_or_bypass_permission(self, run):
        run.side_effect = subprocess.TimeoutExpired(["native"], 8)
        with self.assertRaisesRegex(demo.FocusError, "timed out"):
            demo.run_command(["native"])
        self.assertEqual(run.call_count, 1)

    @patch.object(demo.subprocess, "run", side_effect=FileNotFoundError("missing"))
    def test_missing_command_reports_error(self, run):
        with self.assertRaisesRegex(demo.FocusError, "missing"):
            demo.run_command(["native"])


class RoutingTests(unittest.TestCase):
    @patch.object(demo, "run_command", return_value="")
    def test_gui_dispatch_is_never_called_verified(self, run):
        result = demo.focus_codex(TASK)
        run.assert_called_once_with(["/usr/bin/open", f"codex://threads/{TASK}"])
        self.assertEqual(result["requested_thread_id"], TASK)
        self.assertEqual(result["result"], "DISPATCHED_UNVERIFIED")
        self.assertFalse(result["focus_verified"])
        self.assertFalse(result["topic_verified"])

    @patch.object(demo, "ghostty", return_value="true\tw1\tt1\tp1")
    def test_current_surface(self, native):
        self.assertEqual(demo.current_ghostty(), {
            "app_frontmost": True, "window_id": "w1", "tab_id": "t1", "terminal_id": "p1",
        })

    @patch.object(demo, "ghostty", return_value="false\t\t\t")
    def test_no_windows(self, native):
        result = demo.current_ghostty()
        self.assertFalse(result["app_frontmost"])
        self.assertIsNone(result["terminal_id"])

    @patch.object(demo, "ghostty", return_value="maybe\tw\tt\tp")
    def test_malformed_readback_fails(self, native):
        with self.assertRaisesRegex(demo.FocusError, "Unexpected"):
            demo.current_ghostty()

    @patch.object(demo, "ghostty", side_effect=["w1\tt1\tp1\nw2\tt2\tp2", "true\tw2\tt2\tp2"])
    def test_list_preserves_id_relationships(self, native):
        result = demo.list_ghostty()
        self.assertEqual(result["surfaces"][1], {
            "window_id": "w2", "tab_id": "t2", "terminal_id": "p2",
        })
        self.assertEqual(result["current"]["terminal_id"], "p2")

    @patch.object(demo, "ghostty", side_effect=["", "false\t\t\t"])
    def test_empty_listing_is_normal(self, native):
        self.assertEqual(demo.list_ghostty()["surfaces"], [])

    @patch.object(demo, "ghostty", return_value="only-one-field")
    def test_malformed_listing_fails(self, native):
        with self.assertRaises(demo.FocusError):
            demo.list_ghostty()

    @patch.object(demo, "current_ghostty")
    @patch.object(demo, "ghostty", return_value="dispatched")
    def test_surface_check_does_not_claim_topic_identity(self, native, current):
        current.return_value = {
            "app_frontmost": True, "window_id": "w2", "tab_id": "t2", "terminal_id": SURFACE,
        }
        result = demo.focus_ghostty(SURFACE, TASK)
        native.assert_called_once_with("focus", SURFACE)
        self.assertEqual(result["result"], "SURFACE_FOCUS_VERIFIED")
        self.assertTrue(result["focus_verified"])
        self.assertFalse(result["topic_verified"])
        self.assertEqual(result["expected_thread_id"], TASK)

    @patch.object(demo, "current_ghostty")
    @patch.object(demo, "ghostty", side_effect=demo.FocusError("missing target"))
    def test_closed_target_has_no_fallback(self, native, current):
        with self.assertRaisesRegex(demo.FocusError, "missing target"):
            demo.focus_ghostty(SURFACE)
        native.assert_called_once_with("focus", SURFACE)
        current.assert_not_called()

    def test_wrong_pane_or_background_app_never_passes(self):
        for is_front, actual in ((True, "other"), (False, SURFACE)):
            with self.subTest(frontmost=is_front), \
                    patch.object(demo, "ghostty", return_value="dispatched") as native, \
                    patch.object(demo, "current_ghostty", return_value={
                        "app_frontmost": is_front, "terminal_id": actual,
                    }), \
                    patch.object(demo.time, "monotonic", side_effect=[0, 0, 0, 0, 4]), \
                    patch.object(demo.time, "sleep"):
                with self.assertRaisesRegex(demo.FocusError, "not foreground"):
                    demo.focus_ghostty(SURFACE)
                native.assert_called_once_with("focus", SURFACE)


class CliTests(unittest.TestCase):
    def test_dry_runs_never_call_native_commands_or_wait(self):
        for command in (("codex", TASK), ("ghostty-focus", SURFACE)):
            output = io.StringIO()
            with patch.object(demo, "run_command") as native, \
                    patch.object(demo.time, "sleep") as sleep, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(demo.main([*command, "--dry-run", "--delay", "3"]), 0)
            native.assert_not_called()
            sleep.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["result"], "DRY_RUN")

    @patch.object(demo.sys, "platform", "linux")
    def test_non_macos_stops_before_native_calls(self):
        with patch.object(demo, "run_command") as native, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(demo.main(["codex", TASK]), 2)
        native.assert_not_called()

    @patch.object(demo.sys, "platform", "darwin")
    @patch.object(demo, "focus_codex", side_effect=demo.FocusError("denied"))
    def test_main_reports_error_as_nonzero(self, focus):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(demo.main(["codex", TASK]), 1)
        self.assertEqual(json.loads(output.getvalue())["result"], "ERROR")

    @patch.object(demo.sys, "platform", "darwin")
    def test_cancel_during_delay_does_not_focus(self):
        with patch.object(demo.time, "sleep", side_effect=KeyboardInterrupt), \
                patch.object(demo, "focus_codex") as focus, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(demo.main(["codex", TASK, "--delay", "3"]), 130)
        focus.assert_not_called()


if __name__ == "__main__":
    unittest.main()
