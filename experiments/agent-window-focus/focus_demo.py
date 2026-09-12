#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Bounded macOS focus experiment; no keyboard input or Codex session ownership."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


GHOSTTY_SCRIPT = Path(__file__).with_name("ghostty.applescript")
UUID_PATTERN = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


class FocusError(RuntimeError):
    """A bounded operation failed; do not guess another target."""


def thread_id(value: str) -> str:
    """Allow only existing-task links, never arbitrary URLs or new-task routes."""
    value = value.strip()
    if value.startswith("codex://threads/"):
        value = value.removeprefix("codex://threads/")
    if not re.fullmatch(UUID_PATTERN, value):
        raise argparse.ArgumentTypeError("Use a task UUID or codex://threads/<UUID>.")
    return value.lower()


def terminal_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise argparse.ArgumentTypeError("Copy a terminal_id from ghostty-list/current.")
    return value


def delay_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Delay must be a number from 0 to 30.") from error
    if not math.isfinite(seconds) or not 0 <= seconds <= 30:
        raise argparse.ArgumentTypeError("Delay must be a number from 0 to 30.")
    return seconds


def run_command(command: list[str], timeout: float = 8.0) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=True
        )
    except subprocess.TimeoutExpired as error:
        raise FocusError(
            "Command timed out. Check any macOS Automation prompt, then rerun manually."
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "native command failed").strip()
        raise FocusError(detail[:1200]) from error
    except OSError as error:
        raise FocusError(str(error)) from error
    return result.stdout.rstrip("\r\n")


def ghostty(operation: str, target: str = "", timeout: float = 8.0) -> str:
    # All input is argv data to a fixed script, never interpolated AppleScript.
    return run_command(
        ["/usr/bin/osascript", str(GHOSTTY_SCRIPT), operation, target], timeout
    )


def current_ghostty(timeout: float = 8.0) -> dict:
    fields = ghostty("current", timeout=timeout).split("\t")
    if len(fields) != 4 or fields[0] not in ("true", "false"):
        raise FocusError("Unexpected Ghostty focus readback; result is unverified.")
    frontmost, window, tab, terminal = fields
    return {
        "app_frontmost": frontmost == "true",
        "window_id": window or None,
        "tab_id": tab or None,
        "terminal_id": terminal or None,
    }


def list_ghostty() -> dict:
    rows = ghostty("list")
    surfaces = []
    for row in rows.splitlines():
        fields = row.split("\t")
        if len(fields) != 3:
            raise FocusError("Unexpected Ghostty terminal listing.")
        surfaces.append(dict(zip(("window_id", "tab_id", "terminal_id"), fields)))
    return {"surfaces": surfaces, "current": current_ghostty()}


def focus_codex(target: str) -> dict:
    target = thread_id(target)
    url = f"codex://threads/{target}"
    start = time.monotonic()
    run_command(["/usr/bin/open", url])
    return {
        "result": "DISPATCHED_UNVERIFIED",
        "requested_thread_id": target,
        "url": url,
        "dispatch_ms": round((time.monotonic() - start) * 1000, 1),
        "focus_verified": False,
        "topic_verified": False,
        "verification": "Manually check foreground app and exact conversation; open is not proof.",
    }


def focus_ghostty(target: str, expected_thread: str | None = None) -> dict:
    target = terminal_id(target)
    start = time.monotonic()
    ghostty("focus", target)
    # Read-only bounded polling. Never refocus if the user switches elsewhere.
    deadline = time.monotonic() + 3.0
    observed = None
    while time.monotonic() < deadline:
        observed = current_ghostty(timeout=max(0.05, deadline - time.monotonic()))
        if observed["app_frontmost"] and observed["terminal_id"] == target:
            return {
                "result": "SURFACE_FOCUS_VERIFIED",
                "requested_terminal_id": target,
                "expected_thread_id": expected_thread,
                "observed": observed,
                "focus_ms": round((time.monotonic() - start) * 1000, 1),
                "focus_verified": True,
                "topic_verified": False,
                "verification": "Surface matched. Manually verify its current Codex conversation.",
            }
        time.sleep(0.1)
    raise FocusError(f"Requested Ghostty surface was not foreground within 3s; observed={observed}")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("ghostty-list", help="Read window/tab/terminal IDs; no titles or contents.")
    current = commands.add_parser("ghostty-current", help="Read the selected terminal surface.")
    codex = commands.add_parser("codex", help="Open an exact existing GUI task link.")
    codex.add_argument("thread", type=thread_id, help="Task UUID or codex://threads/<UUID>.")
    focus = commands.add_parser("ghostty-focus", help="Focus an exact Ghostty terminal ID.")
    focus.add_argument("terminal", type=terminal_id)
    focus.add_argument(
        "--expect-thread", type=thread_id,
        help="Optional human-check label only; NOT an automatic task-to-terminal binding.",
    )
    for command in (codex, focus, current):
        command.add_argument("--delay", type=delay_seconds, default=0.0, help="0–30 seconds.")
    for command in (codex, focus):
        command.add_argument("--dry-run", action="store_true", help="No native calls or delay.")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "dry_run", False):
        print(json.dumps({"result": "DRY_RUN", "request": vars(args)}, indent=2))
        return 0
    if sys.platform != "darwin":
        print("This experiment requires macOS (dry-run and offline tests are portable).", file=sys.stderr)
        return 2
    try:
        delay = getattr(args, "delay", 0.0)
        if delay:
            print(f"Waiting {delay:g}s; switch to another window now. Ctrl-C cancels.", file=sys.stderr)
            time.sleep(delay)
        if args.command == "codex":
            result = focus_codex(args.thread)
        elif args.command == "ghostty-focus":
            result = focus_ghostty(args.terminal, args.expect_thread)
        elif args.command == "ghostty-list":
            result = list_ghostty()
        else:
            result = current_ghostty()
        print(json.dumps(result, indent=2))
        return 0
    except FocusError as error:
        print(json.dumps({"result": "ERROR", "error": str(error)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled; no further focus requests will be sent.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
