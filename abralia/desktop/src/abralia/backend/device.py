# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Single-worker keyboard I/O and immutable physical action routing."""

from __future__ import annotations

from contextlib import ExitStack
from collections.abc import Callable
from dataclasses import dataclass
import subprocess
from uuid import UUID

from abralia import SharedRawHidSession
from abralia.interaction import (BindingEntry, BindingPolicy, ConfiguredBinding, ControlId,
                                DeviceEvent, Edge, EventType, HostInteractionController, HostInteractionProtocolClient, StatusFlags)
from abralia.rgb import RgbController, load_profile
from abralia.rgb.adapters.keychron_effect25 import BrightnessPolicy, EffectSelectionPolicy, KeychronEffect25Adapter
from abralia.interaction.matrix_state import ViaMatrixReader
from .navigation import ModeKeyHold, NAVIGATION_KEYS, PICKUP_KEY, MUTE_KEY, PAGE_KEYS
from .render import Renderer
from .core import GapSelection


@dataclass(frozen=True)
class Route:
    control: ControlId
    action: str
    token: str = ""
    notification_id: str = ""
    page_revision: int = 0
    focus_revision: int = 0
    cursor_revision: int = 0
    navigation_revision: int = 0
    attention_revision: int = 0
    attention_key: str = ""
    page_target: int = -1
    page_bank: int = 0
    selection_revision: int = 0
    display_position: int = 0
    layout_revision: int = 0


@dataclass
class DeletePress:
    route: Route
    binding_id: int
    generation: int
    started_at: float
    last_seen: float
    consumed: bool = False


@dataclass
class GapPress:
    route: Route
    binding_id: int
    generation: int
    started_at: float
    last_seen: float
    gap: GapSelection | None
    consumed: bool = False


def routes_for(broker, profile, *, hold_enabled=False) -> dict[int, Route]:
    routes = {}
    for slot in broker.visible_slots():
        position = (slot.position - 1) % 12 + 1
        control = ControlId.key(*profile.element_by_id[f"F{position}"].matrix)
        routes[position] = Route(control, "select", slot.slot_token, page_revision=broker.page_revision)
    if broker.slots and broker.active:
        for key_position in range(1,13):
            position = broker.page * 12 + key_position
            if broker.slot_at_position(position) is None:
                routes[120 + key_position] = Route(
                    ControlId.key(*profile.element_by_id[f'F{key_position}'].matrix), 'close_gap',
                    display_position=position, layout_revision=broker.layout_revision)
    if broker.slots and profile.device_profile.keymap.encoder_count:
        routes[20] = Route(ControlId.encoder_counterclockwise(0), "previous_page")
        routes[21] = Route(ControlId.encoder_clockwise(0), "next_page")
        knob = profile.element_by_id.get('KNOB_PRESS')
        if broker.active and knob is not None and knob.matrix is not None:
            routes[22] = Route(ControlId.key(*profile.element_by_id["KNOB_PRESS"].matrix), "cycle_knob")
    candidate = broker.overview_candidate()
    if candidate:
        routes[32] = Route(ControlId.key(*profile.element_by_id["ENTER"].matrix), "confirm_candidate",
                           candidate.slot_token, page_revision=broker.page_revision,
                           cursor_revision=broker.cursor_revision)
    focused = broker.focused_slot()
    if broker.config.escape_exits_focus and focused:
        routes[40] = Route(ControlId.key(*profile.element_by_id["ESC"].matrix), "exit_focus",
                           focused.slot_token, focus_revision=broker.focus_revision)
    target = broker.pending_target()
    if target:
        actions = [(31, PICKUP_KEY, "pickup")]
        if broker.mute_available():
            actions.append((30, MUTE_KEY, "mute"))
        for binding_id, key, action in actions:
            routes[binding_id] = Route(ControlId.key(*profile.element_by_id[key].matrix), action,
                                      target.slot_token, target.notification.notification_id)
    if hold_enabled and broker.active:
        routes[50] = Route(ControlId.key(*profile.device_profile.require_interaction().toggle_matrix), 'mode_key')
    if broker.active and broker.navigation_active:
        for binding_id, (key, action) in enumerate(NAVIGATION_KEYS.items(), 60):
            routes[binding_id] = Route(ControlId.key(*profile.element_by_id[key].matrix), 'navigate:' + action,
                                      navigation_revision=broker.navigation_revision)
        for key, (action, token) in broker.attention_controls().items():
            routes[70 if key == 'DELETE' else 71] = Route(
                ControlId.key(*profile.element_by_id[key].matrix), 'attention:' + action, token,
                navigation_revision=broker.navigation_revision,
                attention_revision=broker.attention_policy_revision, attention_key=key)
    display = broker.page_display()
    for item in display['keys']:
        if item['captured']:
            key = item['key']
            routes[100 + PAGE_KEYS.index(key)] = Route(
                ControlId.key(*profile.element_by_id[key].matrix), 'jump_page',
                page_target=item['page'] - 1, page_bank=display['bank_start'],
                selection_revision=broker.knob_selection_revision)
    return routes


def dispatch_event(broker, event, routes: dict[int, Route], generation: int):
    if event.event_type is EventType.MODE_CHANGED:
        broker.set_active(event.mode_active)
        return
    if event.event_type is not EventType.CONTROL_EDGE or event.edge is not Edge.UP:
        return
    route = routes.get(event.binding_id)
    if not route or route.control != event.control_id:
        return
    # Paging controls retain the same meaning across table revisions. Drain every
    # detent rather than dropping a burst after the first detent changes the page.
    if route.action in ("next_page", "previous_page"):
        broker.rotate_knob(1 if route.action == "next_page" else -1)
        return
    if route.action == "cycle_knob":
        broker.cycle_knob()
        return
    if event.binding_generation != generation:
        return
    if route.action == 'jump_page':
        broker.jump_to_page(route.page_target, route.page_bank, route.selection_revision)
        return
    if route.action.startswith('navigate:'):
        if route.navigation_revision == broker.navigation_revision:
            broker.navigate(route.action.removeprefix('navigate:'))
        return
    if route.action in ('mode_key', 'close_gap'):
        return
    if route.action.startswith('attention:'):
        if route.navigation_revision == broker.navigation_revision:
            broker.change_attention_policy(route.attention_key, route.action.removeprefix('attention:'),
                                           route.token, route.attention_revision)
        return
    if route.action == "confirm_candidate":
        if route.page_revision == broker.page_revision:
            broker.confirm_candidate(route.token, route.cursor_revision)
    elif route.action == "exit_focus":
        if route.focus_revision == broker.focus_revision:
            broker.exit_focus(route.token)
    elif route.action == "select":
        if route.page_revision == broker.page_revision:
            broker.select_slot(route.token)
    else:
        broker.act_on_call(route.action, route.token, route.notification_id)


class DeviceDriver:
    def __init__(self, broker, profile_id: str, mode: str, *,
                 event_observer: Callable[[DeviceEvent], None] | None = None,
                 gap_committer: Callable[[GapSelection], bool] | None = None):
        self.broker = broker
        self.profile = load_profile(profile_id)
        required = ('ENTER', 'INSERT', 'DELETE', *NAVIGATION_KEYS, *PAGE_KEYS) if broker.config.keyboard_navigation_enabled else ('ENTER', *PAGE_KEYS)
        for name in required:
            element = self.profile.element_by_id.get(name)
            if element is None or element.matrix is None:
                raise ValueError(f"Navigation requires physical {name} matrix metadata")
        self.mode = mode
        self.event_observer = event_observer
        self.renderer = Renderer(self.profile, fps=broker.config.fps)
        self.stack = ExitStack()
        self.routes = {}
        self.route_history = {}
        self.generation = 0
        self.lease = None
        self.last_payload = None
        self.next_frame = 0.0
        self.refresh_at = 0.0
        self.focus_processes = []
        self.protocol = None
        self.rgb = None
        self.mode_key = ControlId.key(*self.profile.device_profile.require_interaction().toggle_matrix)
        self.hold_tracker = ModeKeyHold(self.profile.device_profile.require_interaction().toggle_matrix,
                                       broker.config.navigation_hold_seconds)
        self.matrix_reader = None
        self.navigation_input_error = None
        self.navigation_hold_observed = False
        self.next_matrix_poll = 0.0
        self.delete_press = None
        self.gap_press = None
        self.gap_committer = gap_committer or broker.close_gap

    def start(self):
        if self.mode == "simulated":
            self.broker.delivery = "simulated"
            return
        try:
            session = self.stack.enter_context(SharedRawHidSession.open_profile(self.profile.device_profile))
            protocol = HostInteractionProtocolClient(session.interaction_transport(), profile=self.profile.device_profile)
            caps = protocol.get_capabilities()
            status = protocol.get_status()
            if not caps.supports_toggle_single_tap:
                raise RuntimeError("matching single-tap Host Interaction firmware is required")
            if status.session_token:
                raise RuntimeError("another host owns a firmware session; stop that host before starting the backend")
            if not status.status_flags & StatusFlags.RGB_EFFECT_25_SELECTED:
                raise RuntimeError("select enabled effect 25 before starting the backend")
            adapter = KeychronEffect25Adapter(session.rgb_transport(), session.device_info,
                profile=self.profile.device_profile, effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
                brightness_policy=BrightnessPolicy.PRESERVE_KEYBOARD)
            self.rgb = self.stack.enter_context(RgbController(adapter, self.profile))
            self.protocol = self.stack.enter_context(protocol)
            self.interaction = HostInteractionController(protocol)
            if self.broker.config.keyboard_navigation_enabled:
                matrix = self.profile.device_profile.keymap
                self.matrix_reader = ViaMatrixReader(session.interaction_transport(), matrix.matrix_rows, matrix.matrix_columns)
            self.broker.delivery = "ready"
        except Exception:
            self.stack.close()
            raise

    def suspend(self, reason: str):
        if self.lease:
            self.lease.close()
            self.lease = None
        self.broker.set_active(False)
        self.broker.delivery = "suspended"
        self.broker.device_error = reason
        self.broker.event("device_suspended", reason=reason)

    def build_routes(self):
        hold_enabled = (self.broker.config.keyboard_navigation_enabled and self.navigation_input_error is None
                        and (self.mode == 'simulated' or self.matrix_reader is not None))
        return routes_for(self.broker, self.profile, hold_enabled=hold_enabled)

    def handle_event(self, event):
        if event.event_type is EventType.CONTROL_EDGE:
            route = self.routes.get(event.binding_id)
            gap_press = self.gap_press
            if (gap_press and event.control_id == gap_press.route.control and event.edge is Edge.UP
                    and event.binding_id == gap_press.binding_id):
                if event.binding_generation != gap_press.generation:
                    return
                self._service_gap_press(self.broker.clock())
                self.gap_press = None
                self.broker.cancel_gap_hold('released')
                return  # Consume the original press, even after its key is occupied.
            if route and route.action == 'close_gap' and route.control == event.control_id:
                if event.binding_generation == self.generation and event.edge is Edge.DOWN:
                    if gap_press is None or (gap_press.consumed and gap_press.generation != event.binding_generation):
                        now = self.broker.clock()
                        gap = self.broker.begin_gap_hold(route.display_position, route.layout_revision)
                        self.gap_press = GapPress(route, event.binding_id, event.binding_generation, now, now,
                                                  gap, consumed=gap is None)
                return
            press = self.delete_press
            if (press and event.control_id == press.route.control and event.edge is Edge.UP
                    and event.binding_id == press.binding_id):
                if event.binding_generation != press.generation:
                    return
                self._service_delete_press(self.broker.clock())
                self.delete_press = None
                if not press.consumed:
                    # An unrelated call may rebuild bindings during this press.
                    # The captured target/policy/arming must still match, but its
                    # original generation remains valid for this matched release.
                    dispatch_event(self.broker, event, {press.binding_id: press.route}, press.generation)
                return
            elif route and route.attention_key == 'DELETE' and route.control == event.control_id:
                if event.binding_generation != self.generation:
                    return
                if event.edge is Edge.DOWN:
                    if press is None or press.generation != event.binding_generation:
                        now = self.broker.clock()
                        self.delete_press = DeletePress(route, event.binding_id, event.binding_generation, now, now)
                    return
                if event.edge is Edge.UP:
                    return  # Never act on an orphan release after binding changes.
        if event.event_type is EventType.MODE_CHANGED:
            self.hold_tracker.reset()
        elif (self.matrix_reader is not None and event.event_type is EventType.CONTROL_EDGE
              and event.control_id == self.mode_key and event.edge is Edge.UP):
            fresh, consumed = self.hold_tracker.release_event(self.broker.clock())
            if fresh:
                self.broker.toggle_navigation()
            if consumed:
                return
        if event.event_type is EventType.CONTROL_EDGE:
            previous = self.route_history.get(event.binding_generation, {})
            route = previous.get(event.binding_id)
            if route and (route.action.startswith('navigate:') or route.action == 'jump_page'):
                # Navigation commands retain their meaning while this one
                # arming is active, even when a page/cursor changes the table.
                # The saved route's navigation_revision rejects old armings.
                dispatch_event(self.broker, event, previous, event.binding_generation)
                return
        dispatch_event(self.broker, event, self.routes, self.generation)

    def _service_gap_press(self, now):
        press = self.gap_press
        if press is None or press.consumed:
            return
        if now - press.last_seen > .25 or not self.broker.gap_valid(press.gap):
            press.consumed = True
            self.broker.cancel_gap_hold('input_or_layout_changed')
            return
        press.last_seen = now
        self.broker._navigation_activity()
        if now - press.started_at >= self.broker.config.gap_close_hold_seconds:
            press.consumed = True
            if not self.gap_committer(press.gap):
                self.broker.cancel_gap_hold('commit_failed')

    def _service_delete_press(self, now):
        press = self.delete_press
        if press is None or press.consumed:
            return
        route = press.route
        current = self.broker.attention_controls().get('DELETE')
        if (now - press.last_seen > .25 or route.navigation_revision != self.broker.navigation_revision
                or route.attention_revision != self.broker.attention_policy_revision
                or current != (route.action.removeprefix('attention:'), route.token)):
            press.consumed = True
            return
        press.last_seen = now
        if now - press.started_at >= self.broker.config.restore_all_hold_seconds:
            press.consumed = True
            self.broker.restore_individual_mutes(route.attention_revision)
        self.broker._navigation_activity()

    def poll_mode_key(self, now):
        if not self.broker.active:
            self.hold_tracker.reset()
            return
        if self.matrix_reader is None or self.navigation_input_error or now < self.next_matrix_poll:
            return
        self.next_matrix_poll = now + .04
        try:
            matrix = self.matrix_reader.read()
            row, column = self.profile.device_profile.require_interaction().toggle_matrix
            self.navigation_hold_observed |= bool(matrix[row] & (1 << column))
            if self.broker.navigation_active:
                positions = [self.profile.element_by_id[key].matrix
                             for key in (*NAVIGATION_KEYS, 'ENTER', *self.broker.attention_controls())]
                positions.append((row, column))
                if any(matrix[r] & (1 << c) for r, c in positions):
                    self.broker._navigation_activity()
            if self.hold_tracker.sample(matrix, now):
                self.broker.toggle_navigation()
        except Exception:
            self.navigation_input_error = 'matrix_readback_unavailable'
            self.broker.disarm_navigation('input_unavailable')
            self.hold_tracker.reset()

    def navigation_input_status(self):
        return {'enabled':self.broker.config.keyboard_navigation_enabled,
                'source':'simulated' if self.mode == 'simulated' else 'via_switch_matrix',
                'mode_key_observed':self.navigation_hold_observed,
                'error':self.navigation_input_error}

    def tick(self):
        now = self.broker.clock()
        if self.mode == "hardware" and self.broker.delivery not in ("suspended", "failed"):
            try:
                self.poll_mode_key(now)
                desired = self.build_routes()
                if desired != self.routes:
                    update = self.interaction.replace_bindings(
                        ConfiguredBinding(BindingEntry(route.control, binding_id),
                                          BindingPolicy(emit_down=route.attention_key == 'DELETE' or route.action == 'close_gap', emit_up=True))
                        for binding_id, route in desired.items())
                    if self.generation:
                        self.route_history[self.generation] = self.routes
                        while len(self.route_history) > 32:
                            self.route_history.pop(next(iter(self.route_history)))
                    self.routes, self.generation = desired, update.binding_generation
                for event in self.protocol.service(timeout_ms=2):
                    if self.event_observer is not None:
                        self.event_observer(event)
                    if event.event_type is EventType.QUEUE_OVERFLOW:
                        self.suspend("input_queue_overflow")
                        break
                    if event.event_type is EventType.RGB_EFFECT_CHANGED and not event.rgb_effect25_selected:
                        self.rgb.suspend_output()
                        self.suspend("RGB effect changed; restart the backend after selecting effect 25")
                        break
                    self.handle_event(event)
                self._service_delete_press(self.broker.clock())
                self._service_gap_press(self.broker.clock())
                if self.broker.delivery == "suspended":
                    return
            except Exception as error:
                self.suspend(str(error))
                return
        if self.broker.delivery == "suspended":
            return
        if now >= self.next_frame:
            frame = self.renderer.frame(self.broker)
            if self.mode == "hardware":
                try:
                    if self.lease is None or frame.payload != self.last_payload:
                        if self.lease:
                            self.lease.close()
                        self.lease = self.rgb.display([frame], brightness_ceiling=255)
                        self.refresh_at = now + 1
                    elif now >= self.refresh_at:
                        self.lease.refresh()
                        self.refresh_at = now + 1
                    self.broker.delivery = "written"
                except Exception as error:
                    self.suspend(str(error))
                    return
            self.last_payload = frame.payload
            self.next_frame = now + 1 / self.broker.config.fps
        while self.broker.focus_requests:
            token, thread_id = self.broker.focus_requests.popleft()
            slot = next((s for s in self.broker.slots.values() if s.slot_token == token), None)
            if slot is None or self.broker.selected != slot.slot_id:
                continue
            if self.mode == "simulated":
                self.broker.event("focus_simulated", slot)
            else:
                try:
                    process = subprocess.Popen(["/usr/bin/open", f"codex://threads/{UUID(thread_id)}"],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.focus_processes.append((process, token))
                except OSError as error:
                    self.broker.event("focus_failed", slot, reason=str(error))
        for process, token in list(self.focus_processes):
            if process.poll() is not None:
                slot = next((s for s in self.broker.slots.values() if s.slot_token == token), None)
                self.broker.event("focus_dispatched_unverified" if process.returncode == 0 else "focus_failed", slot)
                self.focus_processes.remove((process, token))

    def close(self):
        try:
            if self.lease:
                self.lease.close()
        finally:
            self.stack.close()
        for process, _ in self.focus_processes:
            if process.poll() is None:
                process.terminate()
