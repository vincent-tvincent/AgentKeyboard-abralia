#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Open, retain, refocus, and verify one uniquely marked VS Code window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from focus_demo import FocusError, delay_seconds, run_command


HERE = Path(__file__).resolve().parent
VSCODE_SCRIPT = HERE / "vscode.applescript"
FINDER_SCRIPT = HERE / "activate_finder.applescript"
VSCODE_CLI = Path(
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
)


def vscode(operation: str, token: str, timeout: float = 8.0) -> str:
    """Pass the opaque token as argv to fixed AppleScript; never interpolate it."""
    return run_command(
        ["/usr/bin/osascript", str(VSCODE_SCRIPT), operation, token], timeout
    )


def observed_window(token: str, timeout: float = 8.0) -> dict:
    fields = vscode("identify", token, timeout).split("\t")
    if len(fields) != 3 or fields[0] not in ("true", "false"):
        raise FocusError("Unexpected VS Code window readback; result is unverified.")
    frontmost, main, title = fields
    if main not in ("true", "false"):
        raise FocusError("Unexpected VS Code main-window readback; result is unverified.")
    return {
        "app_frontmost": frontmost == "true",
        "window_main": main == "true",
        "window_title": title,
        "marker_present": token in title,
    }


def wait_for_window(token: str, deadline: float) -> dict:
    last_error: FocusError | None = None
    while time.monotonic() < deadline:
        try:
            return observed_window(token, timeout=max(0.05, deadline - time.monotonic()))
        except FocusError as error:
            last_error = error
            time.sleep(0.1)
    raise FocusError(f"VS Code target window did not appear: {last_error}")


def wait_for_focus(token: str, deadline: float) -> dict:
    observed = None
    while time.monotonic() < deadline:
        observed = observed_window(
            token, timeout=max(0.05, deadline - time.monotonic())
        )
        if (
            observed["app_frontmost"]
            and observed["window_main"]
            and observed["marker_present"]
        ):
            return observed
        time.sleep(0.1)
    raise FocusError(f"VS Code target was not the focused main window: {observed}")


def create_workspace() -> tuple[Path, Path, str]:
    token = secrets.token_hex(8)
    workspace = Path(
        tempfile.mkdtemp(prefix=f"abralia-vscode-focus-{token}-", dir="/private/tmp")
    )
    marker = workspace / f"ABRALIA_FOCUS_TARGET_{token}.md"
    marker.write_text(
        "# Abralia VS Code focus target\n\n"
        f"Session marker: `{token}`\n\n"
        "This file identifies a temporary focus-test window. It contains no agent prompt.\n",
        encoding="utf-8",
    )
    return workspace, marker, token


def run_experiment(delay: float) -> dict:
    if not VSCODE_CLI.is_file():
        raise FocusError(f"VS Code CLI not found at {VSCODE_CLI}")
    workspace, marker, token = create_workspace()
    start = time.monotonic()
    subprocess.Popen(
        [
            str(VSCODE_CLI),
            "--new-window",
            str(workspace),
            "--goto",
            f"{marker}:1:1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    opened = wait_for_window(token, time.monotonic() + 15.0)

    if delay:
        time.sleep(delay)
    run_command(["/usr/bin/osascript", str(FINDER_SCRIPT)], timeout=5.0)
    time.sleep(0.4)
    background = observed_window(token)
    if background["app_frontmost"]:
        raise FocusError(
            "Could not create a background-focus challenge; target was never displaced."
        )

    vscode("focus", token)
    focused = wait_for_focus(token, time.monotonic() + 3.0)
    return {
        "result": "VSCODE_WINDOW_FOCUS_VERIFIED",
        "token": token,
        "workspace": str(workspace),
        "marker_file": str(marker),
        "opened": opened,
        "background_challenge": background,
        "focused": focused,
        "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
        "window_verified": True,
        "agent_topic_verified": False,
        "verification": (
            "Unique VS Code workspace window refocused. No Codex-extension "
            "conversation was opened or verified."
        ),
    }


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--delay",
        type=delay_seconds,
        default=1.0,
        help="0–30 seconds to leave the new window visible before the challenge.",
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Describe the operation without files, applications, or focus changes.",
    )
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "result": "DRY_RUN",
                    "steps": [
                        "create unique temporary workspace and marker",
                        "open a new VS Code window",
                        "identify the unique window without user input",
                        "activate Finder to displace it",
                        "raise and verify that same VS Code window",
                    ],
                },
                indent=2,
            )
        )
        return 0
    if sys.platform != "darwin":
        print("This experiment requires macOS.", file=sys.stderr)
        return 2
    try:
        print(json.dumps(run_experiment(args.delay), indent=2))
        return 0
    except FocusError as error:
        print(json.dumps({"result": "ERROR", "error": str(error)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled; no further focus requests will be sent.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
