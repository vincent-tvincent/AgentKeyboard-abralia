#!/usr/bin/env python3
"""F1-F12 multi-agent status display demo for custom RGB effect 25.

Each function key represents one agent. The demo uses only volatile lighting
commands, rotates every agent through the complete demo status vocabulary, and
restores the original keyboard lighting state on completion or interruption.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from keychron_rgb_demo import (
    DemoError,
    FrameFlags,
    FrameOperation,
    FrameState,
    FrameStatus,
    HSV,
    KeychronProtocol,
    PER_KEY_RGB_INDEPENDENT_V_EFFECT,
    RawHIDConnection,
    V3_8K_ANSI,
    enumerate_keychron_candidates,
    restore,
    snapshot,
    write_frame,
)


AGENT_KEYS: tuple[tuple[str, int], ...] = tuple(
    (f"F{number}", number) for number in range(1, 13)
)


class AgentStatus(str, Enum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_CHOICE = "waiting_choice"
    COMPLETED_UNREAD = "completed_unread"
    COMPLETED_SEEN = "completed_seen"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    STALE = "stale"


@dataclass(frozen=True)
class StatusVisual:
    label: str
    hue: int
    saturation: int
    floor_value: int
    peak_value: int
    breathe_seconds: float | None
    meaning: str


# The vocabulary combines the canonical Abralia session states with the two
# structured waiting states commonly exposed separately by agent harnesses.
# QUEUED covers accepted work that has not started. RUNNING intentionally
# includes reasoning, tool use, command execution, and file edits; those are
# item-level details rather than different session lifecycle states.
STATUS_ORDER: tuple[AgentStatus, ...] = (
    AgentStatus.UNKNOWN,
    AgentStatus.IDLE,
    AgentStatus.QUEUED,
    AgentStatus.RUNNING,
    AgentStatus.WAITING_USER,
    AgentStatus.WAITING_APPROVAL,
    AgentStatus.WAITING_CHOICE,
    AgentStatus.COMPLETED_UNREAD,
    AgentStatus.COMPLETED_SEEN,
    AgentStatus.INTERRUPTED,
    AgentStatus.ERROR,
    AgentStatus.STALE,
)

STATUS_VISUALS: dict[AgentStatus, StatusVisual] = {
    AgentStatus.UNKNOWN: StatusVisual(
        "Unknown", 0, 0, 18, 54, 5.0, "No authoritative state yet"
    ),
    AgentStatus.IDLE: StatusVisual(
        "Idle", 160, 155, 56, 56, None, "Ready with no active turn"
    ),
    AgentStatus.QUEUED: StatusVisual(
        "Queued", 200, 150, 72, 72, None, "Accepted and waiting to start"
    ),
    AgentStatus.RUNNING: StatusVisual(
        "Running", 145, 220, 48, 168, 2.4, "Reasoning, tools, commands, or edits"
    ),
    AgentStatus.WAITING_USER: StatusVisual(
        "Waiting for user", 35, 230, 64, 208, 1.6, "General user input required"
    ),
    AgentStatus.WAITING_APPROVAL: StatusVisual(
        "Waiting for approval", 24, 255, 80, 255, 0.9, "Live approval request"
    ),
    AgentStatus.WAITING_CHOICE: StatusVisual(
        "Waiting for choice", 205, 190, 60, 196, 1.4, "Structured choice or elicitation"
    ),
    AgentStatus.COMPLETED_UNREAD: StatusVisual(
        "Completed, unread", 92, 205, 84, 235, 1.1, "Finished and needs attention"
    ),
    AgentStatus.COMPLETED_SEEN: StatusVisual(
        "Completed, seen", 92, 175, 62, 62, None, "Finished and acknowledged"
    ),
    AgentStatus.INTERRUPTED: StatusVisual(
        "Interrupted", 14, 235, 112, 112, None, "Cancelled or interrupted"
    ),
    AgentStatus.ERROR: StatusVisual(
        "Error", 0, 255, 76, 255, 0.75, "Turn or harness failure"
    ),
    AgentStatus.STALE: StatusVisual(
        "Stale", 180, 45, 12, 44, 4.5, "Disconnected or heartbeat lost"
    ),
}


def breathing_envelope(elapsed_seconds: float, period_seconds: float) -> float:
    """Return a smooth repeating 0 -> 1 -> 0 breathing envelope."""
    if period_seconds <= 0:
        raise DemoError("Breathing period must be positive.")
    progress = (elapsed_seconds % period_seconds) / period_seconds
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * progress)


def status_value(status: AgentStatus, elapsed_seconds: float) -> int:
    visual = STATUS_VISUALS[status]
    if visual.breathe_seconds is None:
        return visual.peak_value
    envelope = breathing_envelope(elapsed_seconds, visual.breathe_seconds)
    return round(
        visual.floor_value
        + (visual.peak_value - visual.floor_value) * envelope
    )


def statuses_for_step(step: int) -> tuple[AgentStatus, ...]:
    """Rotate all twelve states so every agent demonstrates every state."""
    return tuple(
        STATUS_ORDER[(agent_index + step) % len(STATUS_ORDER)]
        for agent_index in range(len(AGENT_KEYS))
    )


def desired_status_frame(
    statuses: Sequence[AgentStatus],
    elapsed_seconds: float,
    led_count: int = V3_8K_ANSI.expected_led_count,
) -> list[HSV]:
    if len(statuses) != len(AGENT_KEYS):
        raise DemoError(
            f"Expected {len(AGENT_KEYS)} agent states, got {len(statuses)}."
        )
    if led_count <= max(index for _key, index in AGENT_KEYS):
        raise DemoError("LED count does not contain the complete F1-F12 region.")

    frame = [HSV(0, 0, 0) for _ in range(led_count)]
    for (_key, led_index), status in zip(AGENT_KEYS, statuses, strict=True):
        visual = STATUS_VISUALS[status]
        frame[led_index] = HSV(
            visual.hue,
            visual.saturation,
            status_value(status, elapsed_seconds),
        )
    return frame


def normalize_for_effect_25(
    desired_frame: Sequence[HSV], brightness_ceiling: int
) -> tuple[list[HSV], int]:
    """Preserve desired per-key brightness through effect 25 normalization.

    Effect 25 scales the brightest input V to the global RGB brightness. The
    host therefore normalizes the desired frame to a 255 maximum and moves the
    desired scene maximum into the volatile global-brightness value.
    """
    if not 0 <= brightness_ceiling <= 255:
        raise DemoError("Brightness ceiling must be in 0...255.")

    frame_max = max((color.value for color in desired_frame), default=0)
    if frame_max == 0:
        return list(desired_frame), 0

    global_brightness = (frame_max * brightness_ceiling + 127) // 255
    if brightness_ceiling > 0:
        global_brightness = max(1, global_brightness)

    normalized = [
        HSV(
            color.hue,
            color.saturation,
            (color.value * 255 + frame_max // 2) // frame_max,
        )
        for color in desired_frame
    ]
    return normalized, global_brightness


def wait_for_status(
    protocol: KeychronProtocol,
    predicate: Callable[[FrameStatus], bool],
    description: str,
    timeout_seconds: float = 1.0,
) -> FrameStatus:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        last_status = protocol.frame_status()
        if predicate(last_status):
            return last_status
        time.sleep(0.005)
    raise DemoError(
        f"Timed out waiting for {description}; last status={last_status}."
    )


def commit_staging(protocol: KeychronProtocol, sequence: int) -> None:
    protocol.frame_control(FrameOperation.COMMIT, sequence)
    wait_for_status(
        protocol,
        lambda status: bool(status.flags & FrameFlags.ACTIVE_VALID)
        and status.active_sequence == sequence
        and status.back_buffer_free,
        f"active frame sequence {sequence}",
    )


def write_agent_region(protocol: KeychronProtocol, frame: Sequence[HSV]) -> None:
    """Update F1-F12 in two packets while leaving every other key black."""
    protocol.set_colors(1, frame[1:10])
    protocol.set_colors(10, frame[10:13])


def print_status_legend() -> None:
    print("\nAgent-status vocabulary:")
    print("state                 color/brightness       animation   meaning")
    for status in STATUS_ORDER:
        visual = STATUS_VISUALS[status]
        animation = (
            "steady"
            if visual.breathe_seconds is None
            else f"{visual.breathe_seconds:g}s breath"
        )
        value = (
            f"V={visual.peak_value}"
            if visual.floor_value == visual.peak_value
            else f"V={visual.floor_value}..{visual.peak_value}"
        )
        print(
            f"{status.value:21} H={visual.hue:3} S={visual.saturation:3} "
            f"{value:11} {animation:10} {visual.meaning}"
        )


def print_assignments(step: int, statuses: Sequence[AgentStatus]) -> None:
    assignments = ", ".join(
        f"{key}={status.value}"
        for (key, _led_index), status in zip(AGENT_KEYS, statuses, strict=True)
    )
    print(f"Step {step + 1}: {assignments}", flush=True)


def run_agent_status_demo(args: argparse.Namespace) -> None:
    candidates = enumerate_keychron_candidates()
    usable = [candidate for candidate in candidates if candidate.path is not None]
    if args.device is not None:
        usable = [
            candidate
            for candidate in usable
            if candidate.scan_index == args.device
        ]
    if len(usable) != 1:
        raise DemoError(
            f"Expected one selected Keychron Raw HID device, found {len(usable)}."
        )

    with RawHIDConnection(usable[0].path) as connection:
        protocol = KeychronProtocol(connection)
        led_count = protocol.led_count()
        if led_count != V3_8K_ANSI.expected_led_count:
            raise DemoError(
                f"Expected {V3_8K_ANSI.expected_led_count} LEDs, device reports "
                f"{led_count}."
            )

        original = snapshot(protocol, led_count)
        restore_required = False

        try:
            protocol.set_brightness(0)
            restore_required = True
            protocol.set_effect(PER_KEY_RGB_INDEPENDENT_V_EFFECT)
            if protocol.effect() != PER_KEY_RGB_INDEPENDENT_V_EFFECT:
                raise DemoError(
                    "Effect 25 is unavailable; flash the Abralia custom firmware first."
                )

            status = protocol.frame_status()
            if status.state is not FrameState.AWAITING:
                protocol.frame_control(FrameOperation.AWAIT)
                wait_for_status(
                    protocol,
                    lambda current: current.state is FrameState.AWAITING,
                    "AWAITING state",
                )

            protocol.frame_control(FrameOperation.BEGIN)
            wait_for_status(
                protocol,
                lambda current: current.state is FrameState.GUARDED
                and current.back_buffer_free,
                "GUARDED state",
            )

            # Clear the full staging buffer once. Subsequent animation frames
            # update only F1-F12, so Esc and every non-agent key remain black.
            write_frame(protocol, [HSV(0, 0, 0) for _ in range(led_count)])
            sequence = 0
            commit_staging(protocol, sequence)
            sequence = (sequence + 1) & 0xFF

            step = 0
            total_steps = args.cycles * len(STATUS_ORDER)
            frame_interval = 1.0 / args.fps

            while args.cycles == 0 or step < total_steps:
                statuses = statuses_for_step(step)
                print_assignments(step, statuses)
                step_start = time.monotonic()
                next_tick = step_start

                while time.monotonic() - step_start < args.step_seconds:
                    elapsed = time.monotonic() - step_start
                    desired = desired_status_frame(statuses, elapsed, led_count)
                    normalized, global_brightness = normalize_for_effect_25(
                        desired, args.brightness
                    )

                    protocol.set_brightness(global_brightness)
                    write_agent_region(protocol, normalized)
                    commit_staging(protocol, sequence)
                    sequence = (sequence + 1) & 0xFF

                    next_tick += frame_interval
                    delay = next_tick - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_tick = time.monotonic()

                step += 1
        finally:
            if restore_required:
                protocol.set_brightness(0)
                try:
                    protocol.frame_control(FrameOperation.AWAIT)
                    wait_for_status(
                        protocol,
                        lambda current: current.state is FrameState.AWAITING,
                        "cleanup AWAITING state",
                    )
                finally:
                    restore(protocol, original)

                restored = snapshot(protocol, led_count)
                if restored != original:
                    raise DemoError("Agent-status demo restoration readback mismatch.")
                print(
                    f"Restored effect={restored.effect}, "
                    f"brightness={restored.brightness}.",
                    flush=True,
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use F1-F12 as twelve independent agent-status indicators and "
            "rotate every agent through the complete demo state vocabulary."
        )
    )
    parser.add_argument("--device", type=int, help="scanned Raw HID device index")
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=2.5,
        help="seconds to show each status assignment (default: 2.5)",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=255,
        help="maximum temporary brightness in 0...255 (default: 255)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=20.0,
        help="smooth breathing updates per second (default: 20)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help=(
            "complete 12-step rotations; 0 runs until interrupted "
            "(default: 1)"
        ),
    )
    parser.add_argument(
        "--list-statuses",
        action="store_true",
        help="print the status legend without accessing the keyboard",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive lighting confirmation",
    )
    args = parser.parse_args()

    if args.step_seconds <= 0:
        parser.error("--step-seconds must be positive")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be in 0...255")
    if args.fps <= 0 or args.fps > 30:
        parser.error("--fps must be in (0, 30]")
    if args.cycles < 0:
        parser.error("--cycles must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    print_status_legend()
    if args.list_statuses:
        return 0

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    print("\nWARNING — animated F-row agent-status demonstration")
    print(
        "F1-F12 will temporarily represent twelve agents. The demo uses "
        "smooth breathing only, keeps every other key black, and restores "
        "the complete original RGB state afterward."
    )
    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        confirmation = input("Type STATUS to continue, or anything else to cancel: ")
        if confirmation != "STATUS":
            print("Agent-status demo cancelled. No lighting state was changed.")
            return 0

    run_agent_status_demo(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; restoration was requested.", file=sys.stderr)
        raise SystemExit(130)
    except DemoError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
