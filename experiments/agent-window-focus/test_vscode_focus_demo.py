# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Offline VS Code focus tests; native processes and UI are mocked."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import vscode_focus_demo as demo


TOKEN = "0123456789abcdef"


class ReadbackTests(unittest.TestCase):
    @patch.object(
        demo,
        "vscode",
        return_value=f"true\ttrue\tABRALIA_FOCUS_TARGET_{TOKEN}.md — demo",
    )
    def test_exact_marked_main_window(self, native):
        result = demo.observed_window(TOKEN)
        self.assertTrue(result["app_frontmost"])
        self.assertTrue(result["window_main"])
        self.assertTrue(result["marker_present"])

    @patch.object(demo, "vscode", return_value="false\tfalse\tanother window")
    def test_unrelated_window_does_not_match(self, native):
        result = demo.observed_window(TOKEN)
        self.assertFalse(result["app_frontmost"])
        self.assertFalse(result["marker_present"])

    @patch.object(demo, "vscode", return_value="invalid")
    def test_malformed_readback_fails(self, native):
        with self.assertRaisesRegex(demo.FocusError, "Unexpected"):
            demo.observed_window(TOKEN)


class WorkspaceTests(unittest.TestCase):
    def test_workspace_marker_is_unique_and_contains_no_prompt(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            with patch.object(demo.tempfile, "mkdtemp", return_value=temporary), \
                    patch.object(demo.secrets, "token_hex", return_value=TOKEN):
                workspace, marker, token = demo.create_workspace()
            self.assertEqual(workspace, Path(temporary))
            self.assertEqual(token, TOKEN)
            self.assertIn(TOKEN, marker.name)
            contents = marker.read_text(encoding="utf-8")
            self.assertIn(TOKEN, contents)
            self.assertIn("contains no agent prompt", contents)


class CliTests(unittest.TestCase):
    @patch.object(demo, "run_experiment")
    def test_dry_run_has_no_files_or_native_calls(self, experiment):
        output = io.StringIO()
        with patch.object(demo, "create_workspace") as workspace, \
                contextlib.redirect_stdout(output):
            self.assertEqual(demo.main(["--dry-run"]), 0)
        experiment.assert_not_called()
        workspace.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["result"], "DRY_RUN")

    @patch.object(demo.sys, "platform", "linux")
    @patch.object(demo, "run_experiment")
    def test_non_macos_stops_before_experiment(self, experiment):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(demo.main([]), 2)
        experiment.assert_not_called()

    @patch.object(demo.sys, "platform", "darwin")
    @patch.object(demo, "run_experiment", side_effect=demo.FocusError("denied"))
    def test_error_is_nonzero(self, experiment):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(demo.main([]), 1)
        self.assertEqual(json.loads(output.getvalue())["result"], "ERROR")


if __name__ == "__main__":
    unittest.main()
