# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Pure broker state. The service serializes every access on its device worker."""

from __future__ import annotations

from collections import OrderedDict, deque
import colorsys
from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field, replace
import json
import math
import secrets
import time
from typing import Callable
from uuid import UUID
from .navigation import PAGE_KEYS
from .notification_visuals import NotificationVisualState


STATES = ("idle", "progressing", "error", "action_requested", "completed")
HARNESSES = ("codex_desktop", "codex_cli", "unknown")
OPERATIONS = ("acquire_slot", "release_slot", "set_slot_state", "set_notification",
              "report_question", "clear_question", "get_status")
PENDING = ("queued", "active", "muted")


@dataclass(frozen=True)
class BrokerConfig:
    notification_seconds: float = 300
    notification_breath_seconds: float = 8
    orb_formation_seconds: float = 2
    orb_hold_seconds: float = 120
    orb_fade_seconds: float = 60
    orb_dismiss_seconds: float = .5
    question_seconds: float = 600
    disconnect_grace_seconds: float = 30
    background_seconds: float = 15
    fade_seconds: float = 5
    inactive_saturation_percent: float = 75
    background_brightness_percent: float = 50
    escape_exits_focus: bool = True
    keyboard_navigation_enabled: bool = True
    navigation_hold_seconds: float = .8
    navigation_timeout_seconds: float = 15
    agent_mute_seconds: float = 600
    restore_all_hold_seconds: float = 1.2
    muted_brightness_percent: float = 50
    muted_saturation_percent: float = 50
    recovery_grace_seconds: float = 30
    overview_tint: str = "DDD2FF"
    overview_tint_percent: float = 16
    overview_highlight_percent: float = 85
    selection_brightness_percent: float = 130
    selection_saturation_percent: float = 85
    selection_breath_min_percent: float = 85
    selection_breath_seconds: float = 2
    page_display_seconds: float = 5
    knob_selection_idle_seconds: float = 60
    page_occupied_color: str = '00BFFF'
    page_current_color: str = 'FF9000'
    page_occupied_brightness_percent: float = 35
    gap_close_hold_seconds: float = 1.2
    gap_close_flash_seconds: float = .12
    navigation_colors: dict[str, str] = field(default_factory=lambda: {
        'page': '00BFFF', 'slot': 'FF9000', 'boundary': 'A060FF',
    })
    fps: float = 30
    max_pending_calls: int = 256
    idempotency_capacity: int = 4096
    notification_cooldown_seconds: float = 5
    colors: dict[str, str] = field(default_factory=lambda: {
        "idle": "FFFFFF", "progressing": "2080FF", "error": "FF3030",
        "action_requested": "FFB020", "completed": "20D060",
    })

    def __post_init__(self):
        for name in ('escape_exits_focus', 'keyboard_navigation_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        limits = {"notification_seconds": (1, 3600), "question_seconds": (1, 86400),
                  "notification_breath_seconds": (4, 120), "orb_formation_seconds": (.1, 30),
                  "orb_hold_seconds": (0, 3600), "orb_fade_seconds": (.1, 600),
                  "orb_dismiss_seconds": (.05, 5),
                  "disconnect_grace_seconds": (1, 600), "background_seconds": (1, 600),
                  "fade_seconds": (.05, 5), "inactive_saturation_percent": (0, 100),
                  "navigation_hold_seconds": (.4, 3), "navigation_timeout_seconds": (1, 120),
                  "agent_mute_seconds": (1, 3600), "restore_all_hold_seconds": (.4, 5),
                  "muted_brightness_percent": (0, 100), "muted_saturation_percent": (0, 100),
                  "recovery_grace_seconds": (5, 120),
                  "fps": (10, 30), "notification_cooldown_seconds": (0, 600),
                  "background_brightness_percent": (0, 100),
                  "overview_tint_percent": (0, 100), "overview_highlight_percent": (0, 100)}
        limits.update(selection_brightness_percent=(100, 200), selection_saturation_percent=(0, 100),
                      selection_breath_min_percent=(50, 100), selection_breath_seconds=(.5, 10),
                      page_display_seconds=(1, 30), knob_selection_idle_seconds=(1, 600),
                      page_occupied_brightness_percent=(0, 100),
                      gap_close_hold_seconds=(.4, 5), gap_close_flash_seconds=(.05, .5))
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and in {low}...{high}")
        for name in ("max_pending_calls", "idempotency_capacity"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if set(self.colors) != set(STATES):
            raise ValueError("colors must define exactly the supported slot states")
        if not isinstance(self.navigation_colors, dict) or set(self.navigation_colors) != {'page', 'slot', 'boundary'}:
            raise ValueError('navigation_colors must define page, slot, and boundary')
        for color in (*self.colors.values(), *self.navigation_colors.values(), self.overview_tint,
                      self.page_occupied_color, self.page_current_color):
            if not isinstance(color, str) or len(color) != 6 or any(c not in "0123456789abcdefABCDEF" for c in color):
                raise ValueError("colors must be six-digit RGB values")


@dataclass(frozen=True)
class Caller:
    caller_id: str
    thread_id: str | None = None
    identity_source: str = "registered_test_connection"
    surface: str = "unknown"
    surface_source: str = "unknown"

    def __post_init__(self):
        if not self.caller_id or len(self.caller_id) > 256:
            raise ValueError("invalid caller identity")
        if self.thread_id is not None:
            UUID(self.thread_id)


@dataclass
class Notification:
    notification_id: str
    created_at: float
    expires_at: float
    status: str = "queued"
    started_at: float | None = None
    origin: str = "agent"
    request_id: str | None = None
    controls_dismissed: bool = False


@dataclass
class Question:
    question_id: str
    kind: str
    options: list[dict[str, str]]
    allow_other: bool
    expires_at: float
    picked_up: bool = False
    previous_state: str = "idle"
    previous_progress: float | None = None


@dataclass
class Allocation:
    slot_id: int
    slot_token: str
    caller: Caller
    label: str
    state: str = "idle"
    summary: str | None = None
    progress: float | None = None
    revision: int = 1
    notification: Notification | None = None
    question: Question | None = None
    last_notification_at: float = -math.inf
    identity_color: str = "FFFFFF"
    agent_notification: Notification | None = None
    native_notifications: dict[str, Notification] = field(default_factory=dict)
    native_requests: dict[str, dict] = field(default_factory=dict)
    native_deadlines: dict[str, float] = field(default_factory=dict)
    native_covered_by_question: dict[str, str] = field(default_factory=dict)
    observation: dict | None = None
    host_registered: bool = False
    agent_attached: bool = True
    display_position: int | None = None

    @property
    def position(self) -> int:
        return self.slot_id if self.display_position is None else self.display_position

    @property
    def f_key(self) -> str:
        return f'F{(self.position - 1) % 12 + 1}'


@dataclass(frozen=True)
class GapSelection:
    start: int
    end: int
    page: int
    layout_revision: int


class Rejected(ValueError):
    pass


def text_argument(value, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Rejected(f"{name} must be nonempty text of at most {maximum} characters")
    return value


class Broker:
    page_size = 12

    def __init__(self, config: BrokerConfig | None = None, *, clock: Callable[[], float] = time.monotonic):
        self.config = config or BrokerConfig()
        self.clock = clock
        self.epoch = secrets.token_hex(12)
        self.slots: dict[int, Allocation] = {}
        self.reserved_slots: set[int] = set()
        self.reserved_slot_owners: dict[int, str] = {}
        self.reserved_positions: dict[int, str] = {}
        self.owners: dict[str, int] = {}
        # Retain a task's color through release/rejoin for this backend session.
        self.identity_colors: dict[str, str] = {}
        self.page = 0
        self.page_revision = 0
        self.layout_revision = 0
        self.gap_feedback = None
        self.knob_mode = "pages"
        self.knob_selection_deadline = None
        self.knob_selection_revision = 0
        self.page_display_until = 0.0
        self.navigation_active = False
        self.navigation_deadline = None
        self.navigation_revision = 0
        self.agent_mutes: dict[str, float] = {}
        self.only_agent_token: str | None = None
        self.only_agent_until: float | None = None
        self.attention_policy_revision = 0
        self.cursor_token: str | None = None
        self.cursor_revision = 0
        self.active = False
        self.selected: int | None = None
        self.selected_notice_id: str | None = None
        self.focus_revision = 0
        self.current_call: int | None = None
        self.attention_target: int | None = None
        self.background_slot: int | None = None
        self.background_return_at: float | None = None
        self.hold_until = 0.0
        self.delivery = "starting"
        self.device_error: str | None = None
        self.idempotency: OrderedDict[tuple[str, str], tuple[str, dict]] = OrderedDict()
        self.released: OrderedDict[str, str] = OrderedDict()
        self.disconnected: dict[str, float] = {}
        self.events: deque[dict] = deque(maxlen=512)
        self.sequence = 0
        self.focus_requests: deque[tuple[str, str]] = deque()
        self._notification_visual_state = NotificationVisualState()

    def notification_visuals(self) -> dict:
        """Read the worker-owned visual lifecycle without advancing it."""
        return self._notification_visual_state.snapshot(self)

    def _sync_notification_visuals(self):
        self._notification_visual_state = copy(self._notification_visual_state)
        self._notification_visual_state.sync(self)

    def acknowledge_notification_visuals(self, token: str, notice_ids=None) -> bool:
        """Acknowledge visual attention at a verified user-action boundary.

        An asynchronous view adapter must pass the notice IDs captured with its
        view evidence, so a delayed acknowledgement cannot clear later arrivals.
        This does not answer, cancel, or otherwise complete native questions.
        """
        slot = next((s for s in self.slots.values() if s.slot_token == token), None)
        if slot is None:
            return False
        current = {notice.notification_id for notice in self._notices(slot)}
        covered = current if notice_ids is None else current.intersection(notice_ids)
        self._notification_visual_state = copy(self._notification_visual_state)
        self._notification_visual_state.acknowledge(token, covered)
        self._sync_notification_visuals()
        return bool(covered)

    @property
    def page_count(self) -> int:
        return max(1, (max((s.position for s in self.slots.values()), default=1) - 1) // self.page_size + 1)

    def slot_at_position(self, position: int) -> Allocation | None:
        return next((slot for slot in self.slots.values() if slot.position == position), None)

    def location(self, slot: Allocation) -> dict:
        page = (slot.position - 1) // self.page_size
        return {'display_position':slot.position, 'page':page + 1, 'f_key':slot.f_key,
                'visible':page == self.page, 'layout_revision':self.layout_revision}

    def _allocation_addresses(self, caller_id: str) -> tuple[int, int]:
        number = next((n for n, owner in self.reserved_slot_owners.items()
                       if owner == caller_id and n not in self.slots), None)
        if number is None:
            number = 1
            while number in self.slots or number in self.reserved_slots:
                number += 1
        occupied = {s.position for s in self.slots.values()}
        position = next((p for p, owner in self.reserved_positions.items()
                         if owner == caller_id and p not in occupied), None)
        if position is None:
            position = 1
            while position in occupied or position in self.reserved_positions:
                position += 1
        return number, position

    def _layout_changed(self):
        self.layout_revision += 1
        self.cancel_gap_hold('layout_changed')
        if self.gap_feedback and self.gap_feedback['phase'] == 'flash':
            self.gap_feedback = None

    def gap_at(self, position: int) -> GapSelection | None:
        # Recovery reservations are not visible agents yet. Wait for that
        # bounded startup reconciliation before rearranging the display.
        if self.reserved_positions or not self.page * 12 < position <= (self.page + 1) * 12:
            return None
        occupied = {s.position for s in self.slots.values()}
        if position in occupied:
            return None
        start = end = position
        while start > self.page * 12 + 1 and start - 1 not in occupied:
            start -= 1
        while end < (self.page + 1) * 12 and end + 1 not in occupied:
            end += 1
        if not any(p > end for p in occupied):
            return None
        return GapSelection(start, end, self.page, self.layout_revision)

    def gap_valid(self, gap: GapSelection) -> bool:
        return self.active and self.gap_at(gap.start) == gap

    def begin_gap_hold(self, position: int, revision: int) -> GapSelection | None:
        gap = self.gap_at(position)
        if self.gap_feedback or gap is None or gap.layout_revision != revision or not self.gap_valid(gap):
            return None
        self.gap_feedback = {'gap':gap, 'phase':'holding', 'started_at':self.clock()}
        self._navigation_activity()
        self.event('gap_hold_started', start=gap.start, end=gap.end)
        return gap

    def cancel_gap_hold(self, reason: str):
        if self.gap_feedback and self.gap_feedback['phase'] == 'holding':
            self.gap_feedback = None
            self.event('gap_hold_cancelled', reason=reason)

    def gap_snapshot(self) -> dict | None:
        feedback = self.gap_feedback
        if not feedback:
            return None
        gap = feedback['gap']
        progress = min(1, max(0, (self.clock() - feedback['started_at']) / self.config.gap_close_hold_seconds))
        return {'phase':feedback['phase'], 'start_position':gap.start, 'end_position':gap.end,
                'page':gap.page + 1, 'progress':1 if feedback['phase'] == 'flash' else progress,
                'keys':[f'F{(p - 1) % 12 + 1}' for p in range(gap.start, gap.end + 1)]}

    def close_gap(self, gap: GapSelection) -> bool:
        if not self.gap_valid(gap):
            return False
        later = sorted((s for s in self.slots.values() if s.position > gap.end), key=lambda s:s.position)
        self.gap_feedback = None
        for position, slot in enumerate(later, gap.start):
            previous = slot.position
            slot.display_position = position
            slot.revision += 1
            self.event('slot_moved', slot, previous_position=previous, display_position=position)
        self._layout_changed()
        self.page_revision += 1  # Reject old F-key/Enter releases after relocation.
        self.knob_selection_revision += 1
        if self.selection_preview_active() and self.candidate() is None and self.visible_slots():
            self._set_cursor(self.visible_slots()[0].slot_token)
        self._navigation_activity()
        self._show_page_display()
        now = self.clock()
        self.gap_feedback = {'gap':gap, 'phase':'flash', 'started_at':now,
                             'until':now + self.config.gap_close_flash_seconds}
        self.event('gap_closed', start=gap.start, end=gap.end, moved_slots=len(later),
                   layout_revision=self.layout_revision)
        return True

    def event(self, kind: str, slot: Allocation | None = None, **extra):
        self.sequence += 1
        entry = {"sequence": self.sequence, "at": self.clock(), "kind": kind, **extra}
        if slot:
            entry.update(slot_id=slot.slot_id, caller_id=slot.caller.caller_id)
        self.events.append(entry)

    def connected(self, caller_id: str):
        self.disconnected.pop(caller_id, None)

    def set_background_brightness(self, percent: float):
        self.config = replace(self.config, background_brightness_percent=percent)
        self.event("background_brightness_changed", percent=percent)

    def disconnected_at(self, caller_id: str):
        self.disconnected.setdefault(caller_id, self.clock())

    def _identity_color(self, caller_id: str) -> str:
        if caller_id not in self.identity_colors:
            # Golden-angle spacing separates successive registrations. Colors
            # belong to caller identity, independently of slot position/status.
            base_hue, saturation, _ = colorsys.rgb_to_hsv(32 / 255, 128 / 255, 1)
            ordinal = len(self.identity_colors)
            used = set(self.identity_colors.values())
            for offset in range(1024):
                candidate = ordinal + offset
                hue = (base_hue + candidate * .618033988749895) % 1
                # Add saturation variation beyond the initial spread so a long
                # session does not exhaust the quantized single-saturation ring.
                sat = saturation if candidate < 128 else .65 + .3 * ((candidate * .754877666) % 1)
                color = "".join(f"{round(c * 255):02X}" for c in colorsys.hsv_to_rgb(hue, sat, 1))
                if color not in used:
                    break
            else:
                raise Rejected("identity_color_capacity_reached")
            self.identity_colors[caller_id] = color
        return self.identity_colors[caller_id]

    def _owned(self, caller: Caller, token) -> Allocation:
        slot = self.slots.get(self.owners.get(caller.caller_id, -1))
        if slot is None or not isinstance(token, str) or not secrets.compare_digest(slot.slot_token, token):
            raise Rejected("invalid_or_stale_slot_token")
        if not slot.agent_attached:
            raise Rejected('acquire_slot_required')
        return slot

    def register_host_task(self, thread_id: str, label: str) -> tuple[Allocation, bool]:
        caller = Caller('codex:' + thread_id, thread_id, 'registered_by_host',
                        'codex_desktop', 'registered_by_host')
        existing = self.slots.get(self.owners.get(caller.caller_id))
        if existing:
            return existing, False
        number, position = self._allocation_addresses(caller.caller_id)
        slot = Allocation(number, secrets.token_hex(24), caller, label,
                          identity_color=self._identity_color(caller.caller_id),
                          host_registered=True, agent_attached=False, display_position=position)
        self.slots[number] = slot
        self.owners[caller.caller_id] = number
        self._layout_changed()
        self.disconnected.pop(caller.caller_id, None)
        if self.navigation_active and self.candidate() is None and slot in self.visible_slots():
            self._set_cursor(slot.slot_token)
        self.event('slot_registered_by_host', slot)
        return slot, True

    def snapshot(self, slot: Allocation) -> dict:
        n, q = slot.notification, slot.question
        return {"slot_id": slot.slot_id, **({'slot_token': slot.slot_token} if slot.agent_attached else {}),
                "registration_source": 'host' if slot.host_registered else 'agent',
                "agent_attached": slot.agent_attached,
                **self.location(slot), "state": slot.state, "revision": slot.revision,
                "identity_color": slot.identity_color,
                "attention_policy": self.attention_snapshot(slot),
                "progress": slot.progress, "summary": slot.summary,
                "observed": deepcopy(slot.observation),
                "notification": ({"id": n.notification_id, "status": n.status,
                    "origin": n.origin, "request_id": n.request_id,
                    "controls_dismissed": n.controls_dismissed,
                    "remaining_seconds": max(0, n.expires_at - self.clock())} if n else None),
                "question": ({"id": q.question_id, "kind": q.kind, "picked_up": q.picked_up,
                    "highlight_keys": self.question_keys(slot),
                    "remaining_seconds": max(0, q.expires_at - self.clock())} if q else None)}

    def status(self, caller: Caller, token=None) -> dict:
        result = {"status": "accepted", "backend_epoch": self.epoch,
                  "delivery": self.delivery, "device_error": self.device_error,
                  "active": self.active, "page": self.page + 1, "page_count": self.page_count,
                  "focus_active": self.focused_slot() is not None,
                  **self.overview_snapshot(),
                  "attention_slot": self.attention_target,
                  "allocated_slots": len(self.slots), "page_size": self.page_size,
                  "supported_states": STATES, "supported_question_kinds": ["single_choice", "free_text"],
                  "caller": asdict(caller), "limits": asdict(self.config)}
        slot = self.slots.get(self.owners.get(caller.caller_id, -1)) if token is None else self._owned(caller, token)
        if slot:
            result["caller"] = asdict(slot.caller)
            result["allocation"] = self.snapshot(slot)
        result["events"] = [e for e in self.events if e.get("caller_id") == caller.caller_id][-16:]
        return result

    def call(self, caller: Caller, operation: str, arguments: dict) -> dict:
        self.step()
        try:
            if operation not in OPERATIONS or not isinstance(arguments, dict):
                raise Rejected("unknown_operation_or_invalid_arguments")
            if operation == "get_status":
                if set(arguments) - {"slot_token"}:
                    raise Rejected("unexpected_argument")
                return self.status(caller, arguments.get("slot_token"))
            key = text_argument(arguments.get("idempotency_key"), "idempotency_key", 128)
            fingerprint = json.dumps([operation, arguments], sort_keys=True, allow_nan=False)
            cache_key = (caller.caller_id, key)
            if cache_key in self.idempotency:
                original, result = self.idempotency[cache_key]
                if fingerprint != original:
                    raise Rejected("idempotency_conflict")
                replay = deepcopy(result)
                allocation = replay.get('allocation', {})
                current = self.slots.get(allocation.get('slot_id'))
                if (current and current.caller.caller_id == caller.caller_id
                        and allocation.get('slot_token') == current.slot_token):
                    allocation.update(self.location(current))
                return {**replay, "replayed": True}
            result = self._mutate(caller, operation, arguments)
            # Admission precedes the worker's next frame/binding commit. A previous
            # successful write is not proof this newly admitted mutation was drawn.
            result.update(backend_epoch=self.epoch, device_delivery=self.delivery,
                          delivery="queued" if self.delivery in ("ready", "written") else self.delivery)
            self.idempotency[cache_key] = (fingerprint, deepcopy(result))
            while len(self.idempotency) > self.config.idempotency_capacity:
                self.idempotency.popitem(last=False)
            return result
        except (Rejected, TypeError, ValueError) as error:
            return {"status": "rejected", "reason": str(error), "backend_epoch": self.epoch}

    def _mutate(self, caller: Caller, operation: str, a: dict) -> dict:
        allowed = {
            "acquire_slot": {"label", "harness"}, "release_slot": {"slot_token"},
            "set_slot_state": {"slot_token", "state", "summary", "progress"},
            "set_notification": {"slot_token", "enabled", "summary"},
            "report_question": {"slot_token", "question_id", "kind", "options", "allow_other"},
            "clear_question": {"slot_token", "question_id", "outcome"},
        }[operation] | {"idempotency_key"}
        if set(a) - allowed:
            raise Rejected("unexpected_argument")
        if operation == "acquire_slot":
            label = text_argument(a.get("label"), "label", 80)
            slot = self.slots.get(self.owners.get(caller.caller_id, -1))
            registered = slot.caller if slot else caller
            harness = a.get("harness")
            if slot and slot.host_registered and not slot.agent_attached and harness is not None:
                registered = replace(registered, surface='unknown', surface_source='unknown')
            if harness is not None:
                if not isinstance(harness, str) or harness not in HARNESSES:
                    raise Rejected("unsupported_harness")
                if harness != "unknown" and not caller.thread_id:
                    raise Rejected("harness_requires_task_identity")
                if registered.surface != "unknown" and registered.surface != harness:
                    raise Rejected("harness_conflict_release_and_reacquire")
                # The model describes its interface, never the target task ID.
                # Keep a pre-existing host registration's provenance if present.
                if registered.surface_source != "registered_by_host":
                    registered = replace(registered, surface=harness, surface_source="agent_reported")
            if slot:
                if not slot.agent_attached:
                    slot.agent_attached = True
                    slot.revision += 1
                    self.event('host_slot_adopted', slot)
                if slot.caller != registered:
                    slot.caller = registered
                    slot.revision += 1
                    self.event("caller_registered", slot, harness=registered.surface,
                               surface_source=registered.surface_source)
                return {"status": "accepted", "allocation": self.snapshot(slot), "caller": asdict(slot.caller)}
            number, position = self._allocation_addresses(caller.caller_id)
            slot = Allocation(number, secrets.token_hex(24), registered, label,
                              identity_color=self._identity_color(caller.caller_id), display_position=position)
            self.slots[number] = slot
            self.owners[caller.caller_id] = number
            self._layout_changed()
            if self.navigation_active and self.candidate() is None and slot in self.visible_slots():
                self._set_cursor(slot.slot_token)
            self.event("slot_acquired", slot)
            return {"status": "accepted", "allocation": self.snapshot(slot), "caller": asdict(slot.caller)}
        token = a.get("slot_token")
        if operation == "release_slot" and isinstance(token, str) and self.released.get(token) == caller.caller_id:
            return {"status": "accepted", "reason": "already_released"}
        slot = self._owned(caller, token)
        if operation == "release_slot":
            self._release(slot)
            self._promote()
            return {"status": "accepted", "slot_id": slot.slot_id}
        if operation == "set_slot_state":
            state = a.get("state")
            if state not in STATES:
                raise Rejected("unsupported_state")
            summary = a.get("summary")
            if summary is not None:
                text_argument(summary, "summary", 500)
            progress = a.get("progress")
            if progress is not None and (state != "progressing" or isinstance(progress, bool)
                    or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1):
                raise Rejected("invalid_progress")
            slot.state, slot.summary, slot.progress = state, summary, progress
        elif operation == "set_notification":
            if type(a.get("enabled")) is not bool:
                raise Rejected("enabled_must_be_boolean")
            summary = a.get("summary")
            if summary is not None:
                text_argument(summary, "summary", 500)
            manual = slot.agent_notification
            if a["enabled"]:
                if manual and manual.status in PENDING:
                    pass  # In particular, a model update never unmutes a call.
                else:
                    reason = self._notification_admission(slot)
                    if reason:
                        return {"status": "skipped", "reason": reason}
                    self._new_notification(slot)
            elif manual:
                manual.status = "cancelled"
                if self.current_call == slot.slot_id and slot.notification is manual:
                    self.current_call = None
            if summary is not None:
                slot.summary = summary
        elif operation == "report_question":
            if slot.caller.surface != "codex_desktop":
                return {"status": "skipped", "reason": "unsupported_question_surface"}
            question_id = text_argument(a.get("question_id"), "question_id", 128)
            kind = a.get("kind")
            options = a.get("options", [])
            other = a.get("allow_other", False)
            if kind not in ("single_choice", "free_text") or type(other) is not bool:
                raise Rejected("unsupported_question_kind")
            if not isinstance(options, list) or len(options) > 8 or (kind == "single_choice" and len(options) < 1) or (kind == "free_text" and options):
                raise Rejected("invalid_question_options")
            ids = set()
            for option in options:
                if not isinstance(option, dict) or set(option) != {"id", "label"}:
                    raise Rejected("each_option_requires_id_and_label")
                option_id = text_argument(option["id"], "option_id", 80)
                text_argument(option["label"], "option_label", 160)
                if option_id in ids:
                    raise Rejected("duplicate_option_id")
                ids.add(option_id)
            if slot.question and slot.question.question_id == question_id:
                q = slot.question
                if (q.kind, q.options, q.allow_other) != (kind, options, other):
                    raise Rejected("question_identity_conflict")
                return {"status": "accepted", "allocation": self.snapshot(slot)}
            reason = self._notification_admission(slot, replacing=True)
            if reason:
                return {"status": "skipped", "reason": reason}
            previous_state, previous_progress = slot.state, slot.progress
            if slot.question and slot.state == "action_requested":
                previous_state, previous_progress = slot.question.previous_state, slot.question.previous_progress
            slot.question = Question(question_id, kind, deepcopy(options), other,
                                     self.clock() + self.config.question_seconds,
                                     previous_state=previous_state, previous_progress=previous_progress)
            slot.state, slot.progress = "action_requested", None
            self._new_notification(slot)
            if self.current_call == slot.slot_id:
                self.current_call = None
            self.background_slot = None if self.selected == slot.slot_id else self.background_slot
            self.event("question_reported", slot, question_id=question_id)
        elif operation == "clear_question":
            if a.get("outcome") not in ("answered", "cancelled", "withdrawn", "skipped", "expired"):
                raise Rejected("invalid_question_outcome")
            question_id = text_argument(a.get("question_id"), "question_id", 128)
            if slot.question is not None:
                if slot.question.question_id != question_id:
                    raise Rejected("stale_question")
                self._finish_question(slot, a["outcome"])
        slot.revision += 1
        self._promote()
        self.event(operation, slot)
        return {"status": "accepted", "allocation": self.snapshot(slot)}

    def _notification_admission(self, slot: Allocation, *, replacing=False) -> str | None:
        if (not replacing and self.clock() - slot.last_notification_at < self.config.notification_cooldown_seconds):
            return "notification_cooldown"
        count = sum(n.status in PENDING for s in self.slots.values() for n in self._notices(s)
                    if not (s is slot and n is slot.agent_notification))
        return "notification_queue_full" if count >= self.config.max_pending_calls else None

    def _new_notification(self, slot: Allocation):
        now = self.clock()
        old = slot.agent_notification
        if old:
            old.status = "cancelled"
        notice = Notification(secrets.token_hex(12), now, now + self.config.notification_seconds)
        slot.agent_notification = notice
        if not self._held_notice(slot) and (slot.notification is None or slot.notification is old
                                          or slot.notification.status != "active"):
            slot.notification = notice
        slot.last_notification_at = now
        # A new attention request must not inherit a prior browsing/background
        # hold. Existing ringing calls and picked-up questions still arbitrate
        # in _promote; only the stale browsing delay is removed here.
        if not self.attention_muted(slot):
            self.hold_until = 0

    @staticmethod
    def _notices(slot: Allocation):
        return ([slot.agent_notification] if slot.agent_notification else []) + list(slot.native_notifications.values())

    def _held_notice(self, slot: Allocation) -> Notification | None:
        if self.selected != slot.slot_id:
            return None
        notice = next((n for n in self._notices(slot) if n.notification_id == self.selected_notice_id), None)
        if notice and (
                (notice is slot.agent_notification and slot.question and slot.question.picked_up)
                or (notice.origin == "codex" and notice.status == "picked_up"
                    and notice.request_id in slot.native_requests)):
            return notice
        return None

    def _select_notice(self, slot: Allocation):
        held = self._held_notice(slot)
        if held:
            slot.notification = held
            return
        notices = self._notices(slot)
        active = next((n for n in notices if n.status == "active"), None)
        queued = sorted((n for n in notices if n.status == "queued"), key=lambda n: n.created_at)
        available = [n for n in notices if self._notice_available(slot, n)]
        if active or queued:
            slot.notification = active or queued[0]
        elif not self._pickup_available(slot) and available:
            slot.notification = available[-1]

    def _clear_notice_focus(self, slot: Allocation, notice: Notification | None):
        if notice and self.selected == slot.slot_id and self.selected_notice_id == notice.notification_id:
            self.selected = self.background_slot = self.background_return_at = None
            self.selected_notice_id = None
            self.focus_revision += 1
            self.hold_until = 0
            self.focus_requests = deque(r for r in self.focus_requests if r[0] != slot.slot_token)

    def observe_codex(self, token: str, observation: dict) -> bool:
        """Internal observer input. Not an agent-callable operation."""
        slot = next((s for s in self.slots.values() if s.slot_token == token), None)
        if slot is None or observation.get("thread_id") != slot.caller.thread_id:
            return False
        if slot.observation == observation:
            return True
        slot.observation = deepcopy(observation)
        questions = observation.get("questions", [])
        for question in questions:
            request_id = question["request_id"]
            stage = question["stage"]
            live = (observation.get("execution") == "running" and question.get("turn_id") == observation.get("turn_id")
                    and (stage == "accepted" or (stage == "invoked" and question.get("tool") == "request_user_input"
                         and observation.get("mode") == "plan")))
            notice = slot.native_notifications.get(request_id)
            if live:
                deadline = slot.native_deadlines.setdefault(request_id, self.clock() + self.config.question_seconds)
                live = self.clock() < deadline
            if live:
                slot.native_requests[request_id] = deepcopy(question)
                if slot.question and request_id not in slot.native_notifications:
                    slot.native_covered_by_question.setdefault(request_id, slot.question.question_id)
                if notice is None and request_id not in slot.native_covered_by_question:
                    pending = sum(n.status in PENDING for s in self.slots.values() for n in self._notices(s))
                    if pending < self.config.max_pending_calls:
                        now = self.clock()
                        notice = Notification(secrets.token_hex(12), now, now + self.config.notification_seconds,
                                              origin="codex", request_id=request_id)
                        slot.native_notifications[request_id] = notice
                        if not self.attention_muted(slot):
                            self.hold_until = 0
                        self.event("codex_question_attention", slot, request_id=request_id, stage=stage)
            elif stage not in ("invoked", "accepted") or self.clock() >= slot.native_deadlines.get(request_id, math.inf):
                self._close_native_question(slot, request_id,
                    stage if stage not in ("invoked", "accepted") else "local_timeout")
        # Retain closed identities for retry protection without unbounded history.
        for request_id in list(slot.native_notifications):
            if len(slot.native_notifications) <= 256:
                break
            notice = slot.native_notifications[request_id]
            if request_id not in slot.native_requests and notice is not slot.notification:
                del slot.native_notifications[request_id]
                slot.native_deadlines.pop(request_id, None)
        for request_id in list(slot.native_deadlines):
            if (len(slot.native_deadlines) > 256 and request_id not in slot.native_requests
                    and request_id not in slot.native_notifications):
                del slot.native_deadlines[request_id]
                slot.native_covered_by_question.pop(request_id, None)
        self._select_notice(slot)
        slot.revision += 1
        self._promote()
        self.event("codex_observed", slot, execution=observation.get("execution", "unknown"))
        return True

    def _close_native_question(self, slot: Allocation, request_id: str, outcome: str):
        existed = slot.native_requests.pop(request_id, None)
        notice = slot.native_notifications.get(request_id)
        if notice:
            notice.status = "cancelled"
            if self.current_call == slot.slot_id and slot.notification is notice:
                self.current_call = None
            self._clear_notice_focus(slot, notice)
        if existed is not None:
            self.event("codex_question_closed", slot, request_id=request_id, outcome=outcome)

    def _finish_question(self, slot: Allocation, outcome: str):
        question = slot.question
        if question is None:
            return
        slot.question = None
        # A legacy pre-reported question already supplied this request's call.
        # Its cleanup must not create a second onset on the next observer poll.
        for request_id, question_id in list(slot.native_covered_by_question.items()):
            if question_id == question.question_id:
                self._close_native_question(slot, request_id, "manual_question_cleared")
                slot.native_deadlines[request_id] = self.clock()
        if slot.state == "action_requested":
            slot.state, slot.progress = question.previous_state, question.previous_progress
        notice = slot.agent_notification
        if notice:
            notice.status = "expired" if outcome == "expired" else "cancelled"
        if self.current_call == slot.slot_id and slot.notification is notice:
            self.current_call = None
        if self.attention_target == slot.slot_id and slot.notification is notice:
            self.attention_target = None
        clearing_focus = self.selected == slot.slot_id and (self.selected_notice_id is None or notice is None
                                            or self.selected_notice_id == notice.notification_id)
        if clearing_focus:
            self.selected = None
            self.selected_notice_id = None
            self.hold_until = 0
        if self.background_slot == slot.slot_id and self.selected is None:
            self.background_slot = self.background_return_at = None
            self.focus_revision += 1
        if clearing_focus:
            self.focus_requests = deque(r for r in self.focus_requests if r[0] != slot.slot_token)
        self.event("question_expired" if outcome == "expired" else "question_cleared", slot,
                   question_id=question.question_id, outcome=outcome)

    def _release(self, slot: Allocation):
        self.event("slot_released", slot)
        self.released[slot.slot_token] = slot.caller.caller_id
        while len(self.released) > self.config.idempotency_capacity:
            self.released.popitem(last=False)
        self.slots.pop(slot.slot_id)
        self.owners.pop(slot.caller.caller_id, None)
        self._layout_changed()
        if self.agent_mutes.pop(slot.slot_token, None) is not None:
            self.attention_policy_revision += 1
        if self.only_agent_token == slot.slot_token:
            self._end_only_agent('released')
        if not self.slots:
            self._clear_knob_preview()
        self.disconnected.pop(slot.caller.caller_id, None)
        if self.cursor_token == slot.slot_token:
            self._set_cursor(None)
        if self.current_call == slot.slot_id:
            self.current_call = None
        if self.attention_target == slot.slot_id:
            self.attention_target = None
        if self.selected == slot.slot_id:
            self.selected = None
            self.selected_notice_id = None
            self.hold_until = 0
        if self.background_slot == slot.slot_id:
            self.background_slot = None
        if self.page >= self.page_count:
            self.page = self.page_count - 1
            self.page_revision += 1
            self._show_page_display()
            self._set_cursor(None)
        if self.navigation_active and self.candidate() is None and self.visible_slots():
            self._set_cursor(self.visible_slots()[0].slot_token)
        self._sync_notification_visuals()

    def visible_slots(self) -> list[Allocation]:
        return sorted((s for s in self.slots.values() if self.page * 12 < s.position <= (self.page + 1) * 12),
                      key=lambda s:s.position)

    def selected_slot(self) -> Allocation | None:
        return self.slots.get(self.selected)

    def focused_slot(self) -> Allocation | None:
        slot = self.selected_slot()
        return slot if slot and self.background_slot == slot.slot_id else None

    def overview_available(self) -> bool:
        selected = self.selected_slot()
        return bool(self.active and self.focused_slot() is None and not
                    (selected and selected.question and selected.question.picked_up))

    def candidate(self) -> Allocation | None:
        return next((s for s in self.visible_slots() if s.slot_token == self.cursor_token), None)

    def overview_candidate(self) -> Allocation | None:
        return self.candidate() if self.selection_preview_active() else None

    def selection_preview_active(self) -> bool:
        return self.active and (self.navigation_active or self.knob_selection_active())

    def knob_selection_active(self) -> bool:
        return self.active and self.knob_mode == 'agents'

    def _set_knob_mode(self, mode: str):
        if mode != self.knob_mode:
            self.knob_mode = mode
            self.knob_selection_revision += 1
        self.knob_selection_deadline = self.clock() + self.config.knob_selection_idle_seconds if mode == 'agents' else None

    def _show_page_display(self):
        self.page_display_until = self.clock() + self.config.page_display_seconds

    def page_display(self) -> dict:
        pages = {(slot.position - 1) // self.page_size for slot in self.slots.values()}
        visible = bool(self.active and pages and (self.knob_selection_active() or self.clock() < self.page_display_until))
        start = (self.page // len(PAGE_KEYS)) * len(PAGE_KEYS)
        return {'visible':visible, 'persistent':visible and self.knob_selection_active(),
                'bank_start':start, 'keys':[
                    {'key':key, 'page':start + i + 1, 'occupied':start + i in pages,
                     'current':start + i == self.page,
                     'captured':visible and self.knob_selection_active() and start + i in pages}
                    for i,key in enumerate(PAGE_KEYS) if visible and (start + i in pages or start + i == self.page)]}

    def jump_to_page(self, page: int, bank: int, revision: int):
        display = self.page_display()
        if (not self.knob_selection_active() or revision != self.knob_selection_revision
                or bank != display['bank_start']
                or not any(item['page'] == page + 1 and item['captured'] for item in display['keys'])):
            return
        self._navigation_activity()
        self.turn_page(page - self.page)

    def overview_snapshot(self) -> dict:
        candidate = self.overview_candidate()
        return {"knob_mode": self.knob_mode, "overview_available": self.overview_available(),
                'knob_selection_remaining_seconds':max(0, self.knob_selection_deadline - self.clock()) if self.knob_selection_deadline else 0,
                'page_display': self.page_display(),
                'layout_revision': self.layout_revision, 'gap_hold':self.gap_snapshot(),
                "navigation_active": self.navigation_active,
                "navigation_remaining_seconds": max(0, self.navigation_deadline - self.clock()) if self.navigation_deadline else 0,
                "only_agent_slot": next((s.slot_id for s in self.slots.values()
                                         if self.only_agent_active() and s.slot_token == self.only_agent_token), None),
                "only_agent_remaining_seconds": max(0, self.only_agent_until - self.clock()) if self.only_agent_active() else 0,
                "candidate_slot": candidate.slot_id if candidate else None,
                "candidate_key": f"F{(candidate.position - 1) % 12 + 1}" if candidate else None}

    def only_agent_active(self) -> bool:
        return self.only_agent_token is not None and self.only_agent_until is not None and self.clock() < self.only_agent_until

    def attention_muted(self, slot: Allocation) -> bool:
        if self.only_agent_active():
            return slot.slot_token != self.only_agent_token
        return self.agent_mutes.get(slot.slot_token, 0) > self.clock()

    def attention_snapshot(self, slot: Allocation) -> dict:
        muted = self.attention_muted(slot)
        source = ('only_agent' if self.only_agent_active() else 'individual') if muted else None
        until = self.only_agent_until if source == 'only_agent' else self.agent_mutes.get(slot.slot_token, 0)
        return {'muted': muted, 'source': source,
                'remaining_seconds': max(0, until - self.clock()) if muted else 0}

    def attention_control_target(self) -> Allocation | None:
        return self.overview_candidate() or self.focused_slot() or self.pending_target()

    def attention_controls(self) -> dict:
        if not self.active or not self.navigation_active:
            return {}
        target = self.attention_control_target()
        if self.only_agent_active():
            return {'INSERT': ('restore_all', '')}
        if target is None:
            return {}
        return {'DELETE': ('unmute_agent' if self.attention_muted(target) else 'mute_agent', target.slot_token),
                'INSERT': ('only_agent', target.slot_token)}

    def _end_only_agent(self, reason: str):
        self.only_agent_token = self.only_agent_until = None
        self.agent_mutes.clear()
        self.attention_policy_revision += 1
        self.event('only_agent_ended', reason=reason)

    def _expire_attention_policy(self):
        now = self.clock()
        if self.only_agent_token is not None and now >= self.only_agent_until:
            self._end_only_agent('expired')
        expired = [token for token, until in self.agent_mutes.items() if now >= until]
        for token in expired:
            del self.agent_mutes[token]
            slot = next((s for s in self.slots.values() if s.slot_token == token), None)
            self.event('agent_mute_expired', slot)
        if expired:
            self.attention_policy_revision += 1

    def change_attention_policy(self, key: str, action: str, token: str, revision: int) -> bool:
        self._expire_attention_policy()
        if revision != self.attention_policy_revision or self.attention_controls().get(key) != (action, token):
            return False
        self._navigation_activity()
        slot = next((s for s in self.slots.values() if s.slot_token == token), None)
        if action == 'restore_all':
            self._end_only_agent('cancelled')
        else:
            if action == 'only_agent':
                self.agent_mutes.clear()
                self.only_agent_token = token
                self.only_agent_until = self.clock() + self.config.agent_mute_seconds
            elif action == 'mute_agent':
                self.agent_mutes[token] = self.clock() + self.config.agent_mute_seconds
            elif action == 'unmute_agent':
                self.agent_mutes.pop(token, None)
            self.attention_policy_revision += 1
            self.event(action, slot)
        self._promote()
        return True

    def restore_individual_mutes(self, revision: int) -> bool:
        self._expire_attention_policy()
        if (not self.active or not self.navigation_active or self.only_agent_active()
                or revision != self.attention_policy_revision or not self.agent_mutes):
            return False
        self.agent_mutes.clear()
        self.attention_policy_revision += 1
        self._navigation_activity()
        self.event('individual_mutes_cleared')
        self._promote()
        return True

    def _set_cursor(self, token: str | None):
        if token != self.cursor_token:
            self.cursor_token = token
            self.cursor_revision += 1

    def _clear_knob_preview(self):
        if not self.navigation_active:
            self._set_knob_mode('pages')
            self.cursor_token = None
        self.cursor_revision += 1

    def _navigation_activity(self):
        if self.knob_selection_active():
            self.knob_selection_deadline = self.clock() + self.config.knob_selection_idle_seconds
        if self.navigation_active:
            self.navigation_deadline = self.clock() + self.config.navigation_timeout_seconds

    def toggle_navigation(self):
        if not self.active or not self.config.keyboard_navigation_enabled:
            return
        if self.navigation_active:
            self.disarm_navigation('hold')
            return
        self.navigation_active = True
        self._set_knob_mode('agents')
        self.navigation_revision += 1
        self._navigation_activity()
        selected = self.selected_slot()
        slots = self.visible_slots()
        self._set_cursor(selected.slot_token if selected in slots else slots[0].slot_token if slots else None)
        self.event('navigation_armed', page=self.page + 1)

    def disarm_navigation(self, reason):
        if not self.navigation_active:
            return
        self.navigation_active = False
        self.navigation_deadline = None
        self.navigation_revision += 1
        if reason != 'timeout' or not self.knob_selection_active():
            self._clear_knob_preview()
        self.event('navigation_disarmed', reason=reason)

    def navigate(self, action: str):
        if not self.active or not self.navigation_active:
            return
        if action not in ('page_previous', 'page_next', 'first_agent', 'last_agent', 'slot_previous', 'slot_next'):
            return
        self._navigation_activity()
        if action in ('page_previous', 'page_next'):
            self.turn_page(-1 if action == 'page_previous' else 1)
        elif action in ('first_agent', 'last_agent'):
            if self.slots:
                target = (min if action == 'first_agent' else max)(self.slots.values(), key=lambda s:s.position)
                page = (target.position - 1) // self.page_size
                if page != self.page:
                    self.page = page
                    self.page_revision += 1
                    self._show_page_display()
                self._set_cursor(target.slot_token)
        elif action in ('slot_previous', 'slot_next'):
            slots = self.visible_slots()
            candidate = self.candidate()
            if slots:
                step = -1 if action == 'slot_previous' else 1
                index = slots.index(candidate) if candidate in slots else (-1 if step > 0 else len(slots))
                self._set_cursor(slots[min(len(slots) - 1, max(0, index + step))].slot_token)
        else:
            return
        self.event('navigation_moved', action=action, page=self.page + 1,
                   candidate_slot=self.candidate().slot_id if self.candidate() else None)

    def cycle_knob(self):
        if not self.active or not self.slots:
            return
        self._set_knob_mode('agents' if self.knob_mode == 'pages' else 'pages')
        slots = self.visible_slots()
        if self.navigation_active:
            self._navigation_activity()
            if self.candidate() is None and slots:
                self._set_cursor(slots[0].slot_token)
        else:
            self._set_cursor(slots[0].slot_token if slots and self.knob_mode == "agents" else None)
        self.event("knob_function_changed", **self.overview_snapshot())

    def rotate_knob(self, delta: int):
        if not self.active:
            # Preserve the guarded page-handler path for diagnostic adapters.
            self.turn_page(delta)
            return
        before_page, before_token = self.page, self.cursor_token
        self._navigation_activity()
        mode = self.knob_mode if self.navigation_active or self.overview_available() or self.knob_selection_active() else "pages"
        if mode == "agents":
            slots = self.visible_slots()
            candidate = self.candidate()
            if slots:
                index = slots.index(candidate) if candidate else (0 if delta > 0 else len(slots) - 1)
                if candidate:
                    index = min(len(slots) - 1, max(0, index + delta))
                self._set_cursor(slots[index].slot_token)
        else:
            self.turn_page(delta)
        # Record boundary detents too: a clamped page is not a missing HID event.
        candidate = self.overview_candidate()
        self.event("knob_rotated", direction=delta, knob_mode=mode,
                   page=self.page + 1, candidate_slot=candidate.slot_id if candidate else None,
                   changed=(before_page, before_token) != (self.page, self.cursor_token))

    def confirm_candidate(self, token: str, revision: int):
        candidate = self.overview_candidate()
        if candidate is None or candidate.slot_token != token or self.cursor_revision != revision:
            return
        self.select_slot(token)
        self.event("candidate_confirmed", candidate)

    def exit_focus(self, token: str) -> bool:
        slot = self.focused_slot()
        if not self.active or slot is None or slot.slot_token != token:
            return False
        # Keep the question, but leaving focus does not rearm an acknowledged call.
        if slot.question and slot.notification is slot.agent_notification:
            slot.question.picked_up = False
        if slot.notification and slot.notification.origin == "codex" and slot.notification.request_id in slot.native_requests:
            slot.notification.status = "muted"
        if slot.notification and slot.notification.status == "active":
            slot.notification.status = "muted"
        if slot.notification:
            slot.notification.controls_dismissed = True
        if self.current_call == slot.slot_id:
            self.current_call = None
        self.selected = self.background_slot = self.background_return_at = None
        self.selected_notice_id = None
        self._clear_knob_preview()
        if self.attention_target in (None, slot.slot_id):
            self.attention_target = slot.slot_id if self._pickup_available(slot) else None
        self.focus_revision += 1
        self.hold_until = self.clock() + self.config.background_seconds
        self.focus_requests = deque(r for r in self.focus_requests if r[0] != token)
        self.event("focus_exited", slot)
        self._sync_notification_visuals()
        return True

    def pending_target(self) -> Allocation | None:
        slot = self.slots.get(self.attention_target)
        return slot if self._pickup_available(slot) and not self.attention_muted(slot) else None

    @staticmethod
    def _pickup_available(slot: Allocation | None) -> bool:
        return bool(slot and Broker._notice_available(slot, slot.notification))

    @staticmethod
    def _notice_available(slot: Allocation, notice: Notification | None) -> bool:
        if notice is None or notice.controls_dismissed:
            return False
        # Attention has a shorter lifetime than the question. Expiring or
        # withdrawing the notification must not strand a still-live question.
        if notice.status in PENDING or (notice is slot.agent_notification and slot.question and not slot.question.picked_up):
            return True
        return bool(notice.origin == "codex" and notice.request_id in slot.native_requests and notice.status != "picked_up")

    def mute_available(self) -> bool:
        slot = self.pending_target()
        return bool(slot and slot.notification.status in PENDING)

    def has_attention(self) -> bool:
        return any(self._notice_available(slot, notice)
                   for slot in self.slots.values() if not self.attention_muted(slot) for notice in self._notices(slot))

    def _promote(self):
        self._promote_call()
        self._sync_notification_visuals()

    def _promote_call(self):
        # Quiet policies park automatic attention without dismissing/answering it.
        for slot in self.slots.values():
            if self.attention_muted(slot):
                if self.current_call == slot.slot_id:
                    self.current_call = None
                for notice in self._notices(slot):
                    if notice.status == 'active':
                        notice.status = 'queued'
        if self.pending_target() is None:
            self.attention_target = None
        selected = self.selected_slot()
        held = self._held_notice(selected) if selected else None
        if held and not self.attention_muted(selected):
            selected.notification = held
            return
        current = self.slots.get(self.current_call)
        if current:
            active = next((n for n in self._notices(current) if n.status == "active"), None)
            if active:
                current.notification = active
                self.attention_target = current.slot_id
                return
        self.current_call = None
        if self.clock() < self.hold_until:
            return
        queued = [(s, n) for s in self.slots.values() if not self.attention_muted(s)
                  for n in self._notices(s) if n.status == "queued"]
        if queued:
            slot, notice = min(queued, key=lambda pair: (pair[1].created_at, pair[0].slot_id))
            slot.notification = notice
            slot.notification.status = "active"
            if slot.notification.started_at is None:
                slot.notification.started_at = self.clock()
            self.current_call = self.attention_target = slot.slot_id
            self.background_slot = None
            self.event("call_presented", slot)

    def step(self):
        now = self.clock()
        if self.gap_feedback:
            if self.gap_feedback['phase'] == 'flash' and now >= self.gap_feedback['until']:
                self.gap_feedback = None
            elif self.gap_feedback['phase'] == 'holding' and not self.gap_valid(self.gap_feedback['gap']):
                self.cancel_gap_hold('context_changed')
        self._expire_attention_policy()
        if self.navigation_active and now >= self.navigation_deadline:
            self.disarm_navigation('timeout')
        if self.knob_selection_active() and self.knob_selection_deadline is not None and now >= self.knob_selection_deadline:
            self._set_knob_mode('pages')
            if not self.navigation_active:
                self._set_cursor(None)
            self.event('knob_selection_expired')
        for owner, disconnected_at in list(self.disconnected.items()):
            if now - disconnected_at >= self.config.disconnect_grace_seconds:
                slot = self.slots.get(self.owners.get(owner, -1))
                if slot and not slot.host_registered:
                    self._release(slot)
                self.disconnected.pop(owner, None)
        for slot in self.slots.values():
            for request_id in list(slot.native_requests):
                if now >= slot.native_deadlines[request_id]:
                    self._close_native_question(slot, request_id, "local_timeout")
                    slot.revision += 1
            if slot.question and now >= slot.question.expires_at:
                self._finish_question(slot, "expired")
                slot.revision += 1
            for n in self._notices(slot):
                if n.status in PENDING and now >= n.expires_at:
                    n.status = "expired"
                    slot.revision += 1
                    self.event("notification_expired", slot, origin=n.origin)
        self._promote()

    def set_active(self, active: bool):
        if self.active == active:
            return
        self.active = active
        if not active:
            self.disarm_navigation('inactive')
        self._clear_knob_preview()
        if (not active and self.background_slot is not None and
                (self.background_return_at is None or self.background_return_at > self.clock())):
            self.background_return_at = self.clock()
        self.event("mode_changed", active=active)

    def turn_page(self, delta: int):
        if not self.active:
            return
        if self.slots:
            self._show_page_display()
        page = min(max(0, self.page + delta), self.page_count - 1)
        if page != self.page:
            candidate = self.candidate()
            position = (candidate.position - 1) % 12 if candidate else 0
            self.page = page
            self.page_revision += 1
            self._set_cursor(None)
            if self.navigation_active or self.knob_selection_active():
                slots = self.visible_slots()
                target = next((s for s in slots if (s.position - 1) % 12 == position), slots[0] if slots else None)
                self._set_cursor(target.slot_token if target else None)
            self.event("page_changed", page=page + 1, page_count=self.page_count)

    def select_slot(self, token: str):
        if not self.active:
            return
        slot = next((s for s in self.visible_slots() if s.slot_token == token), None)
        if not slot:
            return
        self.acknowledge_notification_visuals(slot.slot_token)
        self._clear_knob_preview()
        self._select_notice(slot)
        if self.navigation_active:
            self._set_cursor(slot.slot_token)
            self._navigation_activity()
        notice = slot.notification
        has_question = notice and (notice.request_id in slot.native_requests or
                                    (notice is slot.agent_notification and slot.question is not None))
        # Browsing a slot without a call does not dismiss the attention target.
        # Selecting a pending call is an explicit pickup, with the same
        # acknowledgement, question hold and control release as the pickup key.
        if self._pickup_available(slot) or has_question:
            old = self.slots.get(self.current_call)
            if old and old is not slot and old.notification and old.notification.status == "active":
                old.notification.status = "queued"
            self._pickup_slot(slot)
            slot.revision += 1
            self.event("slot_selected", slot, pickup=True)
            return
        self.selected = slot.slot_id
        self.selected_notice_id = notice.notification_id if notice and (self._pickup_available(slot) or has_question) else None
        self.focus_revision += 1
        self.background_slot = slot.slot_id
        self.background_return_at = self.clock() + self.config.background_seconds
        self.hold_until = self.clock() + self.config.background_seconds
        can_focus = self._request_focus(slot)
        self.event("slot_selected", slot, focus="requested" if can_focus else "unavailable")
        self._sync_notification_visuals()

    def _pickup_slot(self, slot: Allocation):
        """Shared physical pickup behavior for the pickup key and direct selection."""
        self.acknowledge_notification_visuals(slot.slot_token)
        self._clear_knob_preview()
        if self.navigation_active:
            self._set_cursor(slot.slot_token)
            self._navigation_activity()
        slot.notification.status = "picked_up"
        slot.notification.controls_dismissed = True
        self.current_call = None
        self.attention_target = None
        self.selected = self.background_slot = slot.slot_id
        self.selected_notice_id = slot.notification.notification_id
        self.focus_revision += 1
        self.background_return_at = self.clock() + self.config.background_seconds
        self.hold_until = self.background_return_at
        page = (slot.position - 1) // 12
        if page != self.page:
            self.page, self.page_revision = page, self.page_revision + 1
            self._show_page_display()
        if slot.question and slot.notification is slot.agent_notification:
            slot.question.picked_up = True
        can_focus = self._request_focus(slot)
        self.event("call_picked_up", slot, page=self.page + 1,
                   focus="requested" if can_focus else "unavailable")
        self._sync_notification_visuals()

    def _request_focus(self, slot: Allocation) -> bool:
        # Only the latest user-selected destination may be dispatched. Rapid
        # F-key presses must not open earlier queued destinations afterward.
        self.focus_requests.clear()
        can_focus = bool(slot.caller.thread_id and slot.caller.surface == "codex_desktop")
        if can_focus:
            self.focus_requests.append((slot.slot_token, slot.caller.thread_id))
        return can_focus

    def act_on_call(self, action: str, token: str, notification_id: str):
        slot = self.pending_target()
        if not self.active or not slot or slot.slot_token != token or slot.notification.notification_id != notification_id:
            return
        if action == "mute":
            if not self.mute_available():
                return
            slot.notification.status = "muted"
            slot.notification.controls_dismissed = True
            self.current_call = None
            self.attention_target = None
            self.hold_until = 0
            if self.background_slot == slot.slot_id:
                self.background_return_at = self.clock() + self.config.background_seconds
            self.event("call_muted", slot)
            self._promote()
        elif action == "pickup":
            self._pickup_slot(slot)
        else:
            return
        slot.revision += 1

    def question_keys(self, slot: Allocation) -> list[dict]:
        q = slot.question
        if not q:
            return []
        keys = [{"key": str(i + 1), "action": "option", "option_id": option["id"]}
                for i, option in enumerate(q.options)]
        if q.kind == "single_choice" and q.allow_other:
            keys.append({"key": str(len(q.options) + 1), "action": "other"})
        keys.append({"key": "ENTER", "action": "submit_or_advance"})
        return keys

    def admin_snapshot(self) -> dict:
        return {"backend_epoch": self.epoch, "delivery": self.delivery, "device_error": self.device_error,
                "background_brightness_percent": self.config.background_brightness_percent,
                "active": self.active, "page": self.page + 1, "page_count": self.page_count,
                "focus_active": self.focused_slot() is not None,
                **self.overview_snapshot(),
                "attention_slot": self.attention_target,
                "selected_slot": self.selected, "current_call": self.current_call,
                "slots": [{k: v for k, v in self.snapshot(s).items() if k not in ("slot_token", "summary")}
                          | {"label": s.label, "caller_id": s.caller.caller_id}
                          for s in sorted(self.slots.values(), key=lambda s: s.position)],
                "events": list(self.events)}
