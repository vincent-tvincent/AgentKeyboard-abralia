#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Verify physical knob paging using the real Abralia backend routing."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import select
import signal
import sys
import time
from uuid import uuid4

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.render import Renderer
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8, load_profile


PAGE_NAMES = ("BLUE: slots 1–12", "GREEN: slots 13–24", "AMBER F1: slot 25")


class KnobTrace:
    """Observe delivered events and compare the backend's page transitions."""

    def __init__(self):
        self.raw = []
        self.turns = []
        self.pending = None
        self.mode_entries = 0
        self.mode_exits = 0
        self.last_mode = False

    def observe(self, event):
        if event.event_type is EventType.MODE_CHANGED:
            if event.mode_active != self.last_mode:
                if event.mode_active:
                    self.mode_entries += 1
                else:
                    self.mode_exits += 1
                self.last_mode = event.mode_active
            return
        if event.event_type is not EventType.CONTROL_EDGE or event.edge is not Edge.UP:
            return
        direction = {ControlId.encoder_clockwise(0): 1, ControlId.encoder_counterclockwise(0): -1}.get(event.control_id)
        if direction is None:
            return
        row = {"sequence": event.sequence, "control": str(event.control_id),
               "direction": "CW" if direction == 1 else "CCW", "delta": direction,
               "binding_generation": event.binding_generation, "handled": False}
        self.raw.append(row)
        self.pending = row

    def checks(self):
        active = [r for r in self.turns if r["active"]]
        return {
            "entered_active_mode": self.mode_entries > 0,
            "exited_active_mode": self.mode_exits > 0,
            "clockwise_received": any(r["direction"] == "CW" for r in active),
            "counterclockwise_received": any(r["direction"] == "CCW" for r in active),
            "all_three_pages_seen": {1, 2, 3}.issubset({r["after"] for r in active} | {1}),
            "first_page_boundary_received": any(r["direction"] == "CCW" and r["before"] == r["after"] == 1 for r in active),
            "last_page_boundary_received": any(r["direction"] == "CW" and r["before"] == r["after"] == 3 for r in active),
            "one_page_per_received_detent": bool(active) and all(r["after"] == r["expected"] for r in active),
            "paging_preserves_selection": bool(active) and all(r["selected_before"] == r["selected_after"] for r in active),
            "all_received_encoder_events_handled": bool(self.raw) and all(r["handled"] for r in self.raw),
            "no_duplicate_event_sequences": len({r["sequence"] for r in self.raw}) == len(self.raw),
        }


class ObservedBroker(Broker):
    def __init__(self, trace, config=None):
        super().__init__(config)
        self.trace = trace

    def turn_page(self, delta):
        before, selected, active = self.page + 1, self.selected, self.active
        super().turn_page(delta)
        raw = self.trace.pending
        if raw is None:
            return
        expected = min(3, max(1, before + raw["delta"])) if active else before
        raw["handled"] = True
        self.trace.turns.append({"sequence": raw["sequence"], "direction": raw["direction"],
            "active": active, "before": before, "after": self.page + 1, "expected": expected,
            "selected_before": selected, "selected_after": self.selected,
            "clamped": active and before == self.page + 1})
        self.trace.pending = None


class FixtureRenderer(Renderer):
    def state_color(self, broker, slot, now, *, animate=True):
        # This isolated demo deliberately colors whole pages alike as a page
        # marker. Production slots use each caller's assigned identity color.
        value = broker.config.colors[slot.state]
        return Srgb8(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


def prepare(config=None):
    trace = KnobTrace()
    broker = ObservedBroker(trace, config)
    for number in range(1, 26):
        caller = Caller(f"knob-fixture:{number}", surface="simulated")
        result = broker.call(caller, "acquire_slot", {"label": f"KNOB FIXTURE {number}", "idempotency_key": "acquire"})
        if result["status"] != "accepted":
            raise RuntimeError(f"Cannot prepare fixture {number}: {result['status']}")
        token = result["allocation"]["slot_token"]
        state = ("progressing", "completed", "error")[(number - 1) // 12]
        broker.call(caller, "set_slot_state", {"slot_token": token, "state": state, "idempotency_key": "state"})
    # Setup events are irrelevant to the physical trace and contain no user data.
    broker.events.clear()
    return broker, trace


def page_line(broker):
    return (f"PAGE {broker.page + 1}/3 | {PAGE_NAMES[broker.page]} | "
            f"{'ACTIVE' if broker.active else 'INACTIVE'} | selected={broker.selected or 'none'}")


def dry_run(profile_id, config):
    broker, trace = prepare(config)
    profile = load_profile(profile_id)
    renderer = FixtureRenderer(profile)
    generation, sequence = 1, 1

    def mode(active):
        nonlocal sequence
        event = DeviceEvent(EventType.MODE_CHANGED, 1, sequence, generation, 0,
                            ControlId(0), int(active), EventFlags(0), 0)
        sequence += 1
        trace.observe(event)
        dispatch_event(broker, event, routes_for(broker, profile), generation)

    def detent(direction):
        nonlocal generation, sequence
        routes = routes_for(broker, profile)
        binding_id = 21 if direction == 1 else 20
        event = DeviceEvent(EventType.CONTROL_EDGE, 1, sequence, generation, binding_id,
                            routes[binding_id].control, int(Edge.UP), EventFlags(0), 0)
        sequence += 1
        trace.observe(event)
        dispatch_event(broker, event, routes, generation)
        generation += 1
        renderer.frame(broker)

    detent(1)
    assert broker.page == 0
    mode(True)
    broker.select_slot(broker.slots[1].slot_token)
    for direction in (-1, 1, 1, 1, -1, -1):
        detent(direction)
    mode(False)
    detent(1)
    assert broker.page == 0 and broker.selected == 1
    checks = trace.checks()
    print(json.dumps({"evidence": "SIMULATED ONLY; no HID opened", "checks": checks, "detents": trace.turns}, indent=2))
    return 0 if all(checks.values()) else 1


def run(args):
    colors = {"idle": "FFFFFF", "progressing": "2080FF", "completed": "20FF60",
              "error": "FFB020", "action_requested": "FFB020"}
    config = BrokerConfig(background_brightness_percent=args.background_percent, colors=colors)
    if args.dry_run:
        return dry_run(args.profile, config)
    broker, trace = prepare(config)
    driver = DeviceDriver(broker, args.profile, "hardware", event_observer=trace.observe)
    driver.renderer = FixtureRenderer(driver.profile, fps=config.fps)
    run_id = str(uuid4())
    log = args.log.open("a", encoding="utf-8") if args.log else None

    def emit(event, **values):
        row = {"run_id": run_id, "event": event, **values}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()

    interrupted, fault, cleanup_error = False, None, None
    started = time.monotonic()
    old_term = signal.getsignal(signal.SIGTERM)

    def terminate(*_):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, terminate)
        driver.start()
        emit("READY", profile=args.profile, slots=25, page_size=12,
             background_percent=args.background_percent, keyboard_brightness="preserved")
        print("\n1. INACTIVE: rotate the knob; it should behave normally and not change pages.\n"
              f"2. Double-tap the physical {driver.renderer.toggle} position to activate.\n"
              "3. One CCW detent at page 1: stay BLUE (lower boundary).\n"
              "4. One CW detent: GREEN. One more: AMBER F1 only.\n"
              "5. One more CW: stay on page 3 (upper boundary).\n"
              "6. One CCW: GREEN. One more: BLUE.\n"
              "7. Double-tap the mode position to exit; rotate and check normal behavior.\n"
              "Optional: select F1 on page 1 before paging; selected slot must not change with the knob.\n"
              "Type q then Enter to stop, or press Ctrl-C. The timeout also restores state.\n", flush=True)
        last_view, raw_index, turn_index, broker_sequence = None, 0, 0, 0
        stdin_open = True
        while time.monotonic() - started < args.timeout:
            broker.step()
            driver.tick()
            if broker.delivery in ("suspended", "failed"):
                fault = broker.device_error or "device suspended"
                break
            for raw in trace.raw[raw_index:]:
                emit("ENCODER_UP", **raw)
            raw_index = len(trace.raw)
            for row in trace.turns[turn_index:]:
                emit("DETENT_RESULT", **row)
            turn_index = len(trace.turns)
            for row in broker.events:
                if row["sequence"] > broker_sequence and row["kind"] in ("mode_changed", "slot_selected"):
                    emit("BACKEND_EVENT", **row)
                broker_sequence = max(broker_sequence, row["sequence"])
            view = (broker.page, broker.active, broker.selected)
            if view != last_view:
                print(page_line(broker), flush=True)
                last_view = view
            if stdin_open and select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if not line:
                    stdin_open = False
                elif line.strip().lower() == "q":
                    break
            time.sleep(.003)
    except KeyboardInterrupt:
        interrupted = True
    except Exception as error:
        fault = str(error)
    finally:
        try:
            driver.close()
        except Exception as error:
            cleanup_error = str(error)
        signal.signal(signal.SIGTERM, old_term)
        checks = trace.checks()
        emit("SUMMARY", checks=checks, received_encoder_events=len(trace.raw),
             duplicate_retries_filtered=(driver.protocol.duplicate_event_count if driver.protocol else 0),
             fault=fault, cleanup_error=cleanup_error,
             physical_confirmation="Required: actual knob detents match the log; normal inactive action and no active action leakage.")
        if log:
            log.close()
    if fault or cleanup_error:
        return 1
    if all(checks.values()):
        print("ROUTING_CHECKS_PASS: confirm the visual pages and normal inactive knob behavior.")
        return 0
    print("INCOMPLETE: one or more required physical steps were not observed; this is not a passing hardware test.")
    return 130 if interrupted else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--background-percent", type=float, default=50)
    parser.add_argument("--log", type=Path, help="optional append-only JSONL trace; parent directory must exist")
    parser.add_argument("--dry-run", action="store_true", help="exercise real dispatch with synthetic events; no hardware")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 900:
        parser.error("--timeout must be in 10...900 seconds")
    try:
        return run(args)
    except (ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
