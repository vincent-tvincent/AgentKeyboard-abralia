# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Single-worker keyboard I/O and immutable physical action routing."""

from __future__ import annotations

from contextlib import ExitStack
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
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
from .gui_devices import device_fingerprint, device_id as physical_device_id, resolve_device, validate_fingerprint


from .input_routes import Route, routes_for, dispatch_event


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


class DeviceDriver:
    def __init__(self, broker, profile_id: str, mode: str, *,
                 event_observer: Callable[[DeviceEvent], None] | None = None,
                 gap_committer: Callable[[GapSelection], bool] | None = None,
                 sort_committer: Callable[[], bool] | None = None):
        self.broker = broker
        self.profile = load_profile(profile_id)
        self.profile_id = profile_id
        self.selected_device_id = None
        self.selected_device_fingerprint = None
        escape = self.profile.element_by_id.get('ESC')
        broker.keyboard_frame_keys = (frozenset(e.element_id for e in self.profile.rgb_elements)
                                      if escape and escape.rgb_capable and escape.matrix is not None else frozenset())
        broker.keyboard_frame_mode_key = self.profile.interaction_toggle_element_id()
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
        self.terminal_focus_job = None
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
        self.sort_committer = sort_committer or broker.toggle_sort

    def start(self):
        if self.mode == "simulated":
            self.broker.delivery = "simulated"
            return
        try:
            if self.selected_device_fingerprint is None:
                opened = SharedRawHidSession.open_profile(self.profile.device_profile)
            else:
                selected = resolve_device(self.selected_device_fingerprint, self.profile.device_profile)
                opened = SharedRawHidSession.open_path(selected.path, selected)
            session = self.stack.enter_context(opened)
            protocol = HostInteractionProtocolClient(session.interaction_transport(), profile=self.profile.device_profile)
            caps = protocol.get_capabilities()
            status = protocol.get_status()
            if not caps.supports_toggle_single_tap:
                raise RuntimeError("matching single-tap Host Interaction firmware is required")
            if status.session_token:
                raise RuntimeError("another host owns a firmware session; stop that host before starting the backend")
            if not status.status_flags & StatusFlags.RGB_EFFECT_25_SELECTED:
                raise RuntimeError("select enabled effect 25 before starting the backend")
            self.selected_device_fingerprint = device_fingerprint(session.device_info)
            self.selected_device_id = physical_device_id(self.selected_device_fingerprint)
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
            self.broker.device_error = None
        except Exception:
            self.stack.close()
            raise

    def select_device(self, fingerprint, device_id: str, profile_id: str):
        """Apply an explicit GUI choice on the existing serialized worker only.

        Descriptor validation precedes releasing the old device. No selection
        index survives discovery, and failure never falls back to another board.
        Geometry/profile switching requires a separately configured backend.
        """
        if not isinstance(profile_id, str) or profile_id.removeprefix('builtin:') != self.profile.device_profile.profile_id:
            raise ValueError('device_profile_mismatch')
        fingerprint = validate_fingerprint(fingerprint)
        if physical_device_id(fingerprint) != device_id:
            raise ValueError('device_identity_mismatch')
        if self.mode != 'hardware':
            return {'status': 'rejected', 'reason': 'simulated_backend_has_no_physical_device'}
        selected = resolve_device(fingerprint, self.profile.device_profile)
        fresh = device_fingerprint(selected)
        if (self.selected_device_id == device_id and self.selected_device_fingerprint == fresh
                and self.protocol is not None and self.broker.delivery not in ('suspended', 'failed')):
            return {'status': 'accepted', 'device_id': device_id, 'changed': False,
                    'delivery': self.broker.delivery}
        self.broker.set_active(False)
        self.broker.delivery = 'suspended'
        self.broker.focus_requests.clear()
        try:
            self.close()
        except Exception as error:
            self.broker.device_error = 'previous_device_cleanup_failed'
            return {'status': 'rejected', 'reason': 'previous_device_cleanup_failed',
                    'detail': str(error)}
        finally:
            self.stack = ExitStack()
            self.lease = self.protocol = self.rgb = self.matrix_reader = None
            self.routes, self.route_history = {}, {}
            self.generation = 0
            self.last_payload = None
            self.next_frame = self.refresh_at = 0.
            self.focus_processes = []
            self.delete_press = self.gap_press = None
            self.hold_tracker.reset()
            self.navigation_input_error = None
        self.selected_device_id, self.selected_device_fingerprint = device_id, fresh
        try:
            self.start()
        except Exception as error:
            self.broker.delivery = 'suspended'
            self.broker.device_error = str(error)
            return {'status': 'rejected', 'reason': 'selected_device_open_failed', 'detail': str(error)}
        self.broker.event('device_selected', device_id=device_id)
        return {'status': 'accepted', 'device_id': device_id, 'changed': True,
                'delivery': self.broker.delivery}

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
            if route and route.action == 'cycle_sort':
                if (event.control_id == route.control and event.edge is Edge.UP
                        and event.binding_generation == self.generation
                        and self.broker.active and self.broker.navigation_active
                        and route.navigation_revision == self.broker.navigation_revision
                        and route.layout_revision == self.broker.layout_revision
                        and not self.broker.visible_keyboard_frame()):
                    self.sort_committer()
                return
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
        if not self.broker.active or self.broker.visible_keyboard_frame():
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

    def _cancel_terminal_focus(self):
        job, self.terminal_focus_job = self.terminal_focus_job, None
        if job is None:
            return
        process = job['process']
        if process.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        if process.stdout:
            process.stdout.close()
        # Reap on a daemon thread so process teardown never blocks HID service.
        import threading
        threading.Thread(target=process.wait, daemon=True, name='abralia-focus-reap').start()

    def _service_terminal_focus(self):
        job = self.terminal_focus_job
        if job is None:
            return
        slot = next((s for s in self.broker.slots.values() if s.slot_token == job['token']), None)
        attachment = self.broker.terminal_attachments.get(slot.caller.caller_id) if slot else None
        valid = (slot and self.broker.active and self.broker.selected == slot.slot_id
                 and self.broker.focus_revision == job['revision'] and attachment
                 and attachment['generation'] == job['generation'])
        if not valid:
            self._cancel_terminal_focus()
            return
        process = job['process']
        if process.poll() is None:
            if time.monotonic() >= job['deadline']:
                self.broker.navigation_results[slot.slot_token] = {'status': 'failed', 'reason': 'focus_timeout'}
                self.broker.event('focus_failed', slot, reason='focus_timeout')
                self._cancel_terminal_focus()
            return
        from .terminal_focus import public_result
        try:
            result = public_result(json.loads(process.stdout.read(4097))) if process.returncode == 0 else {
                'status': 'failed', 'reason': 'focus_helper_failed'}
        except (ValueError, OSError):
            result = {'status': 'failed', 'reason': 'invalid_focus_result'}
        process.stdout.close()
        self.terminal_focus_job = None
        self.broker.navigation_results[slot.slot_token] = result
        self.broker.event('terminal_focus_result', slot, **result)

    def _start_terminal_focus(self, slot, attachment):
        self._cancel_terminal_focus()
        command = ([sys.executable, 'terminal-focus'] if getattr(sys, 'frozen', False)
                   else [sys.executable, '-m', 'abralia.backend.terminal_focus'])
        try:
            # A private anonymous file avoids blocking the HID loop on a pipe
            # write and keeps native endpoint metadata out of process arguments.
            with tempfile.TemporaryFile() as context_file:
                context_file.write(json.dumps(attachment['context']).encode())
                context_file.seek(0)
                process = subprocess.Popen(command, stdin=context_file, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, start_new_session=os.name == 'posix')
            self.terminal_focus_job = {'process': process, 'token': slot.slot_token,
                                       'revision': self.broker.focus_revision,
                                       'generation': attachment['generation'],
                                       'deadline': time.monotonic() + 12}
        except OSError:
            self.broker.event('focus_failed', slot, reason='focus_helper_unavailable')

    def tick(self):
        self._service_terminal_focus()
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
            elif slot.caller.surface != 'codex_desktop':
                attachment = self.broker.terminal_attachments.get(slot.caller.caller_id)
                if attachment and attachment['slot_token'] == token:
                    self._start_terminal_focus(slot, attachment)
            else:
                self._cancel_terminal_focus()
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
        self._cancel_terminal_focus()
        try:
            if self.lease:
                self.lease.close()
        finally:
            self.stack.close()
        for process, _ in self.focus_processes:
            if process.poll() is None:
                process.terminate()
