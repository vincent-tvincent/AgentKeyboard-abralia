#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""User-assisted pickup -> Tab -> arrows -> Enter experiment on a real question."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import time
from uuid import UUID, uuid4

from abralia.backend.service import BrokerService


class FocusEvidence:
    """Observe host events; never infer native focus or an answer from a URL open."""

    def __init__(self, thread_id):
        self.caller_id = "codex:" + str(UUID(thread_id))
        self.last_sequence = 0
        self.last_view = None
        self.picked_up = False
        self.open_dispatched = False
        self.released = False

    def observe(self, snapshot):
        rows = []
        for event in snapshot.get("events", []):
            sequence = event["sequence"]
            if sequence <= self.last_sequence:
                continue
            self.last_sequence = sequence
            if event.get("caller_id") != self.caller_id and event["kind"] != "mode_changed":
                continue
            kind = event["kind"]
            self.picked_up |= kind == "call_picked_up"
            self.open_dispatched |= kind == "focus_dispatched_unverified"
            self.released |= kind == "slot_released"
            # Explicitly allowlist evidence; no summaries, tokens or question text.
            rows.append({"event": "HOST_EVENT", **{k: event[k] for k in
                         ("sequence", "at", "kind", "slot_id", "active", "page", "focus") if k in event}})
        slots = [{k: s[k] for k in ("slot_id", "page", "f_key", "visible", "state")}
                 for s in snapshot.get("slots", []) if s.get("caller_id") == self.caller_id]
        view = {"event": "VIEW", "active": snapshot.get("active"),
                "delivery": snapshot.get("delivery"), "page": snapshot.get("page"), "slots": slots}
        if view != self.last_view:
            rows.append(view)
            self.last_view = view
        return rows

    def summary(self):
        return {"physical_pickup_received": self.picked_up,
                "task_open_dispatched_unverified": self.open_dispatched,
                "helper_released_slot": self.released,
                "native_focus": "requires user observation",
                "native_answer": "requires actual native question reply in the helper task"}


def run(args):
    evidence = FocusEvidence(args.thread_id)
    case_id = str(uuid4())
    output = args.output.open("a", encoding="utf-8")
    service = BrokerService(args.project, args.profile, mode=args.mode)

    def emit(row):
        line = json.dumps({"case_id": case_id, **row}, ensure_ascii=False)
        print(line, flush=True)
        output.write(line + "\n")
        output.flush()

    old_term = signal.getsignal(signal.SIGTERM)
    def stop(*_):
        raise KeyboardInterrupt

    fault = None
    try:
        signal.signal(signal.SIGTERM, stop)
        service.start()
        emit({"event": "READY", "mode": args.mode, "thread_id": args.thread_id,
              "project": str(Path(args.project).resolve()), "profile": args.profile})
        print("\nPICKUP / TAB / ENTER EXPERIMENT\n"
              "Wait for the helper's fresh native question and keyboard notification.\n"
              "Start in another task with its normal composer focused.\n"
              "1. Double-tap physical Pause if inactive, then press Scroll Lock to pick up.\n"
              "2. Without clicking the question, press Tab once. Note which control gets focus.\n"
              "3. Use arrows until BRAVO is selected. Note whether arrow selection works.\n"
              "4. Press Enter once. Does BRAVO reach the agent, or does the panel close?\n"
              "If the panel expires, report EXPIRED; do not call that an Enter success.\n"
              "Do not return to this terminal between steps; that would change app focus.\n"
              "Afterward tell the parent task the observed focus and submission result.\n"
              "Ctrl-C ends the experiment and restores keyboard state.\n", flush=True)
        started = time.monotonic()
        while time.monotonic() - started < args.timeout and not service.stop_event.is_set():
            snapshot = service.submit({"connection": "focus-experiment-monitor", "role": "admin",
                "message": {"type": "admin", "action": "status"}})
            if snapshot.get("status") != "accepted":
                fault = snapshot.get("reason", "status_unavailable")
                break
            for row in evidence.observe(snapshot):
                emit(row)
            if snapshot.get("device_error"):
                fault = snapshot["device_error"]
                break
            time.sleep(.1)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        fault = str(error)
    finally:
        try:
            service.close()
        except Exception as error:
            fault = fault or str(error)
        signal.signal(signal.SIGTERM, old_term)
        emit({"event": "SUMMARY", **evidence.summary(), "fault": fault,
              "cleanup_error": service.cleanup_error, "worker_error": service.worker_error,
              "interpretation": "Host observations only; this summary cannot certify native keyboard submission."})
        output.close()
    if fault or service.cleanup_error or service.worker_error:
        return 1
    return 0 if evidence.picked_up else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--thread-id", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--mode", choices=("hardware", "simulated"), default="hardware")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", type=Path, required=True, help="append-only JSONL; parent directory must exist")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 900:
        parser.error("--timeout must be in 10...900 seconds")
    try:
        return run(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
