# Copyright 2026 blue_lobster
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the actual interaction C sources against fake QMK input/time/HID."""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[1]
USERSPACE = FIRMWARE / "qmk-userspace"
REFERENCE = (
    USERSPACE / "keyboards/keychron/v3_8k/ansi_encoder/keymaps/abralia_host_interaction"
)


class FirmwarePortTests(unittest.TestCase):
    def test_interaction_on_each_board(self):
        cases = (
            ("v3/ansi", 16, 0),
            ("v3/ansi_encoder", 16, 1),
            ("v3_8k/ansi_encoder", 17, 1),
        )
        with tempfile.TemporaryDirectory(prefix="abralia-firmware-tests-") as temporary:
            for board, columns, encoders in cases:
                with self.subTest(board=board):
                    keymap = (
                        USERSPACE
                        / "keyboards/keychron"
                        / board
                        / "keymaps/abralia_host_interaction"
                    )
                    executable = Path(temporary) / board.replace("/", "_")
                    command = shlex.split(os.environ.get("CC", "cc")) + [
                        "-std=c11",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-DMATRIX_ROWS=6",
                        f"-DMATRIX_COLS={columns}",
                        f"-DEXPECTED_ENCODERS={encoders}",
                        '-DQMK_KEYBOARD_H="qmk_test.h"',
                        "-I", str(FIRMWARE / "tests/stubs"),
                        "-I", str(REFERENCE),
                        "-include", str(keymap / "config.h"),
                    ]
                    if encoders:
                        command += ["-DENCODER_ENABLE", f"-DNUM_ENCODERS={encoders}"]
                    command += [
                        str(keymap / "host_interaction.c"),
                        str(keymap / "host_interaction_protocol.c"),
                        str(FIRMWARE / "tests/interaction_port_harness.c"),
                        "-o", str(executable),
                    ]
                    subprocess.run(command, check=True)
                    subprocess.run([str(executable)], check=True)

    def test_16_column_target_requires_valid_toggle_configuration(self):
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-fsyntax-only", "-DMATRIX_ROWS=6", "-DMATRIX_COLS=16",
            '-DQMK_KEYBOARD_H="qmk_test.h"',
            "-I", str(FIRMWARE / "tests/stubs"),
            str(REFERENCE / "host_interaction_protocol.c"),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("toggle must be in the matrix", result.stderr)

    def test_siblings_keep_upstream_keymaps_and_reference_implementations(self):
        for board in ("ansi", "ansi_encoder"):
            for variant in ("abralia", "abralia_host_interaction"):
                with self.subTest(board=board, variant=variant):
                    keymap = USERSPACE / "keyboards/keychron/v3" / board / "keymaps" / variant
                    self.assertIn(
                        f'#include "keyboards/keychron/v3/{board}/keymaps/keychron/keymap.c"',
                        (keymap / "keymap.c").read_text(),
                    )
                    rules = (keymap / "rules.mk").read_text()
                    self.assertEqual(
                        "ENCODER_MAP_ENABLE = yes" in rules, board.endswith("encoder")
                    )
                    for name in ("per_key_rgb_independent_v.c", "rgb_matrix_user.inc"):
                        expected = f"../../../../v3_8k/ansi_encoder/keymaps/{variant}/{name}"
                        self.assertIn(f'#include "{expected}"', (keymap / name).read_text())
                        self.assertTrue((keymap / expected).is_file())


if __name__ == "__main__":
    unittest.main()
