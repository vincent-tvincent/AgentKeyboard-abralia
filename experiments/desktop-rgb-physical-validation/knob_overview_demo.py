#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Demo page/agent knob navigation and highlighted Enter confirmation."""

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
from abralia.backend.device import DeviceDriver, Route
from abralia.backend.render import ATTENTION, ENTER_HIGHLIGHT, Renderer, blend, hex_color
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import PhysicalSceneBuilder, Srgb8, load_profile

DEFAULT_ENTER_COLOR = f"{ENTER_HIGHLIGHT.red:02X}{ENTER_HIGHLIGHT.green:02X}{ENTER_HIGHLIGHT.blue:02X}"


class OverviewBroker(Broker):
    """Isolated overview experiment; production broker semantics are unchanged."""

    def __init__(self, config=None, *, hold_seconds=3, clock=time.monotonic):
        super().__init__(config, clock=clock)
        self.knob_mode = "pages"
        self.cursor_token = None
        self.cursor_revision = 0
        self.confirmed_at = None
        self.hold_seconds = hold_seconds

    def candidate(self):
        return next((s for s in self.visible_slots() if s.slot_token == self.cursor_token), None)

    def view(self):
        candidate = self.candidate()
        return {"active": self.active, "knob_mode": self.knob_mode, "page": self.page + 1,
                "candidate": candidate.slot_id if candidate else None, "selected": self.selected}

    def reset_overview(self):
        self.selected = self.background_slot = self.confirmed_at = None
        self.background_return_at = None
        self.cursor_token = None
        self.cursor_revision += 1
        self.focus_revision += 1
        self.knob_mode = "pages"
        self.event("demo_overview_reset", page=self.page + 1)

    def exit_focus(self, token):
        if not super().exit_focus(token):
            return False
        self.reset_overview()
        return True

    def set_active(self, active):
        changed = active != self.active
        super().set_active(active)
        if changed:
            self.reset_overview()

    def cycle(self):
        if not self.active or self.selected is not None:
            return
        self.knob_mode = "agents" if self.knob_mode == "pages" else "pages"
        slots = self.visible_slots()
        self.cursor_token = slots[0].slot_token if slots and self.knob_mode == "agents" else None
        self.cursor_revision += 1
        self.event("demo_knob_function", knob_mode=self.knob_mode)

    def rotate(self, direction):
        if not self.active or self.selected is not None:
            return
        if self.knob_mode == "pages":
            self.turn_page(direction)
            return
        slots = self.visible_slots()
        current = self.candidate()
        if not slots:
            return
        index = slots.index(current) if current else (0 if direction > 0 else len(slots) - 1)
        if current:
            index = min(len(slots) - 1, max(0, index + direction))
        token = slots[index].slot_token
        if token != self.cursor_token:
            self.cursor_token = token
            self.cursor_revision += 1
            self.event("demo_candidate", slots[index])

    def confirm(self, token, source):
        if not self.active or self.selected is not None:
            return
        super().select_slot(token)
        if self.selected is not None:
            self.cursor_token = None
            self.cursor_revision += 1
            self.confirmed_at = self.clock()
            self.event("demo_confirmed", self.selected_slot(), source=source)

    def step(self):
        super().step()
        if self.confirmed_at is not None and self.clock() >= self.confirmed_at + self.hold_seconds:
            self.reset_overview()


def prepare(config, *, clock=time.monotonic, hold_seconds=3):
    broker = OverviewBroker(config, clock=clock, hold_seconds=hold_seconds)
    states = ("progressing", "completed", "error", "action_requested", "idle")
    allocations = []
    for number in range(1, 26):
        caller = Caller(f"overview-fixture:{number}", surface="simulated")
        result = broker.call(caller, "acquire_slot", {"label": f"OVERVIEW FIXTURE {number}", "idempotency_key": "acquire"})
        token = result["allocation"]["slot_token"]
        broker.call(caller, "set_slot_state", {"slot_token": token, "state": states[(number - 1) % 5], "idempotency_key": "state"})
        allocations.append((caller, token))
    # Real releases leave holes without moving the remaining allocations.
    for number in (4, 8, 16, 20):
        caller, token = allocations[number - 1]
        broker.call(caller, "release_slot", {"slot_token": token, "idempotency_key": "release"})
    broker.events.clear()
    return broker


def overview_routes(broker, profile):
    def key(name):
        return ControlId.key(*profile.element_by_id[name].matrix)

    routes = {20: Route(ControlId.encoder_counterclockwise(0), "rotate_previous"),
              21: Route(ControlId.encoder_clockwise(0), "rotate_next"),
              22: Route(key("KNOB_PRESS"), "cycle")}
    if broker.selected is not None:
        focused = broker.focused_slot()
        if broker.config.escape_exits_focus and focused:
            routes[40] = Route(key("ESC"), "exit_focus", focused.slot_token,
                               focus_revision=broker.focus_revision)
        return routes  # Brief demo confirmation display: ignore knob input.
    for slot in broker.visible_slots():
        position = (slot.slot_id - 1) % 12 + 1
        routes[position] = Route(key(f"F{position}"), "select", slot.slot_token,
                                 page_revision=broker.page_revision)
    candidate = broker.candidate()
    if broker.active and broker.knob_mode == "agents" and candidate:
        routes[32] = Route(key("ENTER"), "confirm", candidate.slot_token,
                           page_revision=broker.cursor_revision)
    return routes


def dispatch_overview(broker, event, routes, generation):
    if event.event_type is EventType.MODE_CHANGED:
        broker.set_active(event.mode_active)
        return "mode"
    if event.event_type is not EventType.CONTROL_EDGE or event.edge is not Edge.UP:
        return "ignored_edge"
    route = routes.get(event.binding_id)
    if not route or route.control != event.control_id:
        return "unknown_route"
    if route.action == "exit_focus":
        if event.binding_generation != generation or route.focus_revision != broker.focus_revision:
            return "stale_focus"
        return "exit_focus" if broker.exit_focus(route.token) else "outside_focus"
    if not broker.active or broker.selected is not None:
        return "outside_overview"
    # These controls have fixed bindings throughout overview. Ordered UP events
    # retain each detent across a page/cursor table update; retries are filtered
    # by the shared protocol client before this dispatch path.
    if route.action in ("rotate_previous", "rotate_next"):
        broker.rotate(1 if route.action == "rotate_next" else -1)
        return route.action
    if route.action == "cycle":
        broker.cycle()
        return "cycle"
    if event.binding_generation != generation:
        return "stale_generation"
    if route.action == "confirm":
        candidate = broker.candidate()
        if (broker.knob_mode != "agents" or candidate is None or
                candidate.slot_token != route.token or broker.cursor_revision != route.page_revision):
            return "stale_candidate"
        broker.confirm(route.token, "ENTER")
        return "confirm"
    if route.action == "select":
        if route.page_revision != broker.page_revision:
            return "stale_page"
        broker.confirm(route.token, f"F{event.binding_id}")
        return "select" if broker.selected is not None else "stale_allocation"
    return "unknown_action"


class OverviewRenderer(Renderer):
    def __init__(self, profile, *, tint="DDD2FF", tint_percent=16,
                 highlight_percent=85, enter_color=DEFAULT_ENTER_COLOR, scope="slots"):
        super().__init__(profile)
        self.tint = hex_color(tint)
        self.tint_percent = tint_percent
        self.highlight_percent = highlight_percent
        self.enter_color = hex_color(enter_color)
        self.scope = scope

    def state_color(self, broker, slot, now, *, animate=True):
        return super().state_color(broker, slot, now, animate=False)

    def _render_overview(self, broker, colors):
        # Compose demo controls before the shared renderer determines the
        # existing frame maximum and encodes slot values against it.
        background = self.white
        if broker.active and broker.selected is None and broker.knob_mode == "agents":
            tint = blend(Srgb8(0, 0, 0), self.tint, self.tint_percent / 100)
            if self.scope == "full":
                background = tint
            occupied = {f"F{(slot.slot_id - 1) % 12 + 1}" for slot in broker.visible_slots()}
            for key in self.f_keys:
                if key not in occupied:
                    colors[key] = self.slot_background(broker, tint)
            candidate = broker.candidate()
            if candidate:
                key = f"F{(candidate.slot_id - 1) % 12 + 1}"
                colors[key] = blend(self.state_color(broker, candidate, broker.clock()),
                                    ATTENTION, self.highlight_percent / 100)
                colors["ENTER"] = self.enter_color
        return background

    def frame(self, broker):
        scene = super().frame(broker)
        return PhysicalSceneBuilder().build("abralia-overview-demo", dict(scene.payload.colors),
                                             background=scene.payload.background, owner="abralia-overview-demo")


class OverviewTrace:
    def __init__(self):
        self.rows = []

    def record(self, event, action, before, after):
        if event.event_type in (EventType.CONTROL_EDGE, EventType.MODE_CHANGED):
            self.rows.append({"sequence": event.sequence, "control": str(event.control_id),
                              "generation": event.binding_generation, "action": action,
                              "before": before, "after": after})

    def checks(self):
        rotations = [r for r in self.rows if r["action"].startswith("rotate_")]
        agents = [r for r in rotations if r["before"]["knob_mode"] == "agents"]
        pages = [r for r in rotations if r["before"]["knob_mode"] == "pages"]
        return {
            "entered_active": any(not r["before"]["active"] and r["after"]["active"] for r in self.rows),
            "exited_active": any(r["before"]["active"] and not r["after"]["active"] for r in self.rows),
            "paged_both_directions": all(any(r["action"] == action and r["before"]["page"] != r["after"]["page"] for r in pages)
                                         for action in ("rotate_next", "rotate_previous")),
            "cycled_both_functions": all(any(r["action"] == "cycle" and r["after"]["knob_mode"] == mode for r in self.rows)
                                         for mode in ("pages", "agents")),
            "moved_candidate_both_directions": all(any(r["action"] == action and r["before"]["candidate"] != r["after"]["candidate"] for r in agents)
                                                   for action in ("rotate_next", "rotate_previous")),
            "candidate_stays_on_page": bool(agents) and all(r["before"]["page"] == r["after"]["page"] for r in agents),
            "rotation_never_commits": bool(rotations) and all(r["after"]["selected"] is None for r in rotations),
            "enter_confirmed": any(r["action"] == "confirm" and r["after"]["selected"] == r["before"]["candidate"] for r in self.rows),
            "f_key_confirmed": any(r["action"] == "select" and r["after"]["selected"] is not None for r in self.rows),
            "delivered_sequences_unique": len({r["sequence"] for r in self.rows}) == len(self.rows),
        }


class OverviewDriver(DeviceDriver):
    def __init__(self, broker, profile_id, trace, **appearance):
        super().__init__(broker, profile_id, "hardware")
        for name in ("KNOB_PRESS", "ENTER"):
            element = self.profile.element_by_id.get(name)
            if element is None or element.matrix is None:
                raise ValueError(f"Profile requires physical {name} matrix metadata")
        self.trace = trace
        self.renderer = OverviewRenderer(self.profile, **appearance)

    def build_routes(self):
        return overview_routes(self.broker, self.profile)

    def handle_event(self, event):
        before = self.broker.view()
        action = dispatch_overview(self.broker, event, self.routes, self.generation)
        self.trace.record(event, action, before, self.broker.view())


def dry_run(args, config):
    now = [100.0]
    broker = prepare(config, clock=lambda: now[0])
    driver = OverviewDriver(broker, args.profile, OverviewTrace(), **appearance(args))
    sequence = 0

    def send(binding=None, active=None):
        nonlocal sequence
        sequence += 1
        driver.routes, driver.generation = driver.build_routes(), sequence
        event = (DeviceEvent(EventType.MODE_CHANGED, 1, sequence, sequence, 0, ControlId(0), int(active), EventFlags(0), 0)
                 if active is not None else DeviceEvent(EventType.CONTROL_EDGE, 1, sequence, sequence, binding,
                                                       driver.routes[binding].control, int(Edge.UP), EventFlags(0), 0))
        driver.handle_event(event)
        driver.renderer.frame(broker)

    send(active=True)
    for binding in (21, 20, 22, 21, 21, 21, 20, 22, 22, 32):
        send(binding)
    now[0] += 4
    broker.step()
    send(2)
    send(active=False)
    checks = driver.trace.checks()
    print(json.dumps({"evidence": "SIMULATED ONLY; no HID opened", "checks": checks}, indent=2))
    return 0 if all(checks.values()) else 1


def appearance(args):
    return dict(tint=args.selection_tint, tint_percent=args.selection_background_percent,
                highlight_percent=args.highlight_percent, enter_color=args.enter_color,
                scope=args.selection_background_scope)


def run(args):
    config = BrokerConfig(background_brightness_percent=args.background_percent)
    if args.dry_run:
        return dry_run(args, config)
    broker = prepare(config)
    trace = OverviewTrace()
    driver = OverviewDriver(broker, args.profile, trace, **appearance(args))
    log = args.log.open("a", encoding="utf-8") if args.log else None
    run_id = str(uuid4())

    def emit(kind, **values):
        row = {"run_id": run_id, "event": kind, **values}
        line = json.dumps(row, ensure_ascii=False)
        print(line, flush=True)
        if log:
            log.write(line + "\n")
            log.flush()

    fault = cleanup_error = None
    interrupted = False
    old_term = signal.getsignal(signal.SIGTERM)
    def terminate(*_):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, terminate)
        driver.start()
        emit("READY", profile=args.profile, appearance=appearance(args),
             background_percent=args.background_percent, keyboard_upper_bound="preserved")
        print("\nOVERVIEW DEMO — all agents are simulated; no conversations open.\n"
              "1. Inactive: check ordinary knob press/rotation. Double-tap physical Pause to activate.\n"
              "2. PAGES: turn right then left to change pages; empty slots match dim white background.\n"
              "3. Click knob: AGENTS. Background changes tint; candidate and Enter light up.\n"
              "4. Turn right/left: move candidate, skip empty slots, stay on this page.\n"
              "5. Click knob to PAGES: Enter cue clears. Click again to AGENTS.\n"
              "6. Press Enter: confirm candidate. Escape returns immediately; otherwise reset after 3 seconds.\n"
              "7. Press an occupied F-key: direct confirmation; again resets after 3 seconds.\n"
              "8. Double-tap Pause to exit; check ordinary knob/Enter behavior. Ctrl-C stops.\n"
              "You can also type q then Enter after exiting active mode. Timeout restores state.\n"
              "Demo defaults: first occupied candidate, no wrap, skip holes, reset to PAGES.\n", flush=True)
        started, row_index, last_view, stdin_open = time.monotonic(), 0, None, True
        while time.monotonic() - started < args.timeout:
            broker.step()
            driver.tick()
            if broker.delivery in ("suspended", "failed"):
                fault = broker.device_error or "device suspended"
                break
            for row in trace.rows[row_index:]:
                emit("INPUT_RESULT", **row)
            row_index = len(trace.rows)
            view = broker.view()
            if view != last_view:
                start = broker.page * 12 + 1
                emit("VIEW", **view, slot_range=[start, start + 11],
                     occupied=[s.slot_id for s in broker.visible_slots()])
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
        emit("SUMMARY", checks=checks, fault=fault, cleanup_error=cleanup_error,
             duplicate_retries_filtered=driver.protocol.duplicate_event_count if driver.protocol else 0,
             physical_confirmation="Required: colors, detents and no ordinary input leakage during capture.")
        if log:
            log.close()
    if fault or cleanup_error:
        return 1
    if all(checks.values()):
        return 0
    print("INCOMPLETE: some guided steps were not recorded; visual confirmation is separate.")
    return 130 if interrupted else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--background-percent", type=float, default=25)
    parser.add_argument("--selection-tint", default="DDD2FF", help="trial background RGB hex")
    parser.add_argument("--selection-background-percent", type=float, default=16)
    parser.add_argument("--selection-background-scope", choices=("full", "slots"), default="slots")
    parser.add_argument("--highlight-percent", type=float, default=85, help="yellow-green share of candidate blend")
    parser.add_argument("--enter-color", default=DEFAULT_ENTER_COLOR,
                        help="Enter RGB hex override (default: yellow-green A0FF00)")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 10 <= args.timeout <= 900:
        parser.error("--timeout must be in 10...900 seconds")
    for name in ("background_percent", "selection_background_percent", "highlight_percent"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 <= value <= 100:
            parser.error(f"--{name.replace('_', '-')} must be in 0...100")
    for name in ("selection_tint", "enter_color"):
        value = getattr(args, name)
        if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
            parser.error(f"--{name.replace('_', '-')} must contain six RGB hex digits")
    try:
        return run(args)
    except (ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
