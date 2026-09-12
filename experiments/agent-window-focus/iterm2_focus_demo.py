#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0
"""Create and refocus one retained iTerm2 session through its official API."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import secrets
import signal
import sys
import time
from typing import Any, Callable

from focus_demo import FocusError, delay_seconds, run_command

try:
    import iterm2
except ModuleNotFoundError:
    iterm2 = None


FINDER_SCRIPT = Path(__file__).with_name("activate_finder.applescript")
OUTER_TIMEOUT_SECONDS = 20.0


def connection_timeout(_signum: int, _frame: Any) -> None:
    raise FocusError(
        "iTerm2 API authentication/experiment exceeded 20s. Check iTerm2's "
        "Python API authorization prompt and API-server setting; no fallback "
        "focus action was attempted."
    )


def selected_identity(app: Any) -> dict:
    window = app.current_terminal_window
    if window is None or window.current_tab is None:
        return {"window_id": None, "tab_id": None, "session_id": None}
    session = window.current_tab.current_session
    return {
        "window_id": window.window_id,
        "tab_id": window.current_tab.tab_id,
        "session_id": session.session_id if session is not None else None,
    }


def event_record(update: Any) -> dict:
    if update.application_active is not None:
        return {
            "kind": "application_active",
            "active": update.application_active.application_active,
        }
    if update.window_changed is not None:
        return {
            "kind": "window_changed",
            "window_id": update.window_changed.window_id,
            "reason": update.window_changed.event.name,
        }
    if update.selected_tab_changed is not None:
        return {
            "kind": "selected_tab_changed",
            "tab_id": update.selected_tab_changed.tab_id,
        }
    if update.active_session_changed is not None:
        return {
            "kind": "active_session_changed",
            "session_id": update.active_session_changed.session_id,
        }
    return {"kind": "unknown"}


async def collect_until(
    monitor: Any,
    predicate: Callable[[list[dict]], bool],
    timeout: float,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            update = await asyncio.wait_for(
                monitor.async_get_next_update(), timeout=remaining
            )
        except TimeoutError as error:
            raise FocusError(f"Timed out waiting for iTerm2 focus events: {events}") from error
        events.append(event_record(update))
        if predicate(events):
            return events
    raise FocusError(f"Timed out waiting for iTerm2 focus events: {events}")


def saw_application_state(events: list[dict], active: bool) -> bool:
    return any(
        event.get("kind") == "application_active"
        and event.get("active") is active
        for event in events
    )


def saw_window_reason(events: list[dict], window_id: str, reason: str) -> bool:
    return any(
        event.get("kind") == "window_changed"
        and event.get("window_id") == window_id
        and event.get("reason") == reason
        for event in events
    )


async def api_experiment(connection: Any, delay: float) -> dict:
    app = await iterm2.async_get_app(connection, create_if_needed=False)
    if app is None:
        raise FocusError("iTerm2 is not running or its Python API server is unavailable.")

    token = secrets.token_hex(8)
    started = time.monotonic()
    async with iterm2.FocusMonitor(connection) as monitor:
        window = await iterm2.Window.async_create(connection)
        if window is None or window.current_tab is None:
            raise FocusError("iTerm2 did not create a persistent terminal window.")
        session = window.current_tab.current_session
        if session is None:
            raise FocusError("The new iTerm2 window has no current session.")

        await session.async_set_name(f"Abralia Focus Demo {token}")
        await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        await session.async_activate(select_tab=True, order_window_front=True)
        await asyncio.sleep(0.25)
        opened = selected_identity(app)
        target = {
            "window_id": window.window_id,
            "tab_id": window.current_tab.tab_id,
            "session_id": session.session_id,
        }
        if opened != target:
            raise FocusError(f"New iTerm2 session was not selected: {opened} != {target}")

        if delay:
            await asyncio.sleep(delay)
        run_command(["/usr/bin/osascript", str(FINDER_SCRIPT)], timeout=5.0)
        background_events = await collect_until(
            monitor,
            lambda events: saw_application_state(events, False)
            and saw_window_reason(
                events, window.window_id, "TERMINAL_WINDOW_RESIGNED_KEY"
            ),
            timeout=5.0,
        )

        await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        await session.async_activate(select_tab=True, order_window_front=True)
        focused_events = await collect_until(
            monitor,
            lambda events: saw_application_state(events, True)
            and saw_window_reason(
                events, window.window_id, "TERMINAL_WINDOW_BECAME_KEY"
            ),
            timeout=5.0,
        )
        await asyncio.sleep(0.25)
        focused = selected_identity(app)
        if focused != target:
            raise FocusError(f"Focused iTerm2 identity did not match: {focused} != {target}")

    return {
        "result": "ITERM2_SESSION_FOCUS_VERIFIED",
        "token": token,
        "target": target,
        "opened": opened,
        "background_events": background_events,
        "focused_events": focused_events,
        "focused": focused,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "session_verified": True,
        "agent_topic_verified": False,
        "verification": (
            "Native iTerm2 events and selected IDs verify the retained session. "
            "No Codex CLI conversation was started or verified."
        ),
    }


def run_experiment(delay: float) -> dict:
    if iterm2 is None:
        raise FocusError(
            "Missing Python client. Install requirements-iterm2.txt in an "
            "isolated environment."
        )
    result: dict = {}

    async def main(connection: Any) -> None:
        result.update(await api_experiment(connection, delay))

    previous_handler = signal.signal(signal.SIGALRM, connection_timeout)
    signal.setitimer(signal.ITIMER_REAL, OUTER_TIMEOUT_SECONDS)
    try:
        iterm2.run_until_complete(main, retry=False)
    except FocusError:
        raise
    except SystemExit as error:
        raise FocusError(
            "The iTerm2 Python client could not connect. Confirm the Python "
            "API server—not the separate iTerm AI feature—is enabled, then "
            "restart iTerm2 if its private API socket is still absent."
        ) from error
    except Exception as error:
        raise FocusError(f"iTerm2 API experiment failed: {error}") from error
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    if not result:
        raise FocusError("iTerm2 API returned without an experiment result.")
    return result


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--delay",
        type=delay_seconds,
        default=1.0,
        help="0–30 seconds to leave the new session visible before displacement.",
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Describe the operation without API calls, windows, or focus changes.",
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
                        "connect to the enabled iTerm2 Python API server",
                        "create a new window and retain its window/tab/session IDs",
                        "activate Finder and observe native focus-loss events",
                        "activate the retained session through iTerm2's API",
                        "verify native focus events and selected IDs",
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
