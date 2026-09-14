# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Pure physical binding rules shared by the device worker and simulator."""

from __future__ import annotations
from dataclasses import dataclass
from abralia.interaction.protocol import ControlId, Edge, EventType
from .navigation import NAVIGATION_KEYS, PICKUP_KEY, MUTE_KEY, PAGE_KEYS

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
    frame_id: str = ''


def routes_for(broker, profile, *, hold_enabled=False) -> dict[int, Route]:
    guide = broker.visible_keyboard_frame()
    if guide:
        slot, frame = guide
        return {41: Route(ControlId.key(*profile.element_by_id['ESC'].matrix), 'dismiss_keyboard_frame',
                          slot.slot_token, frame_id=frame.frame_id)}
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
    if broker.active and broker.navigation_active:
        routes[90] = Route(ControlId.key(*profile.element_by_id['ESC'].matrix), 'cycle_sort',
                           navigation_revision=broker.navigation_revision,
                           layout_revision=broker.layout_revision)
    elif broker.config.escape_exits_focus and focused:
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
    if broker.visible_keyboard_frame() and route.action != 'dismiss_keyboard_frame':
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
    if route.action == 'cycle_sort':
        if (broker.active and broker.navigation_active
                and route.navigation_revision == broker.navigation_revision
                and route.layout_revision == broker.layout_revision):
            broker.toggle_sort()
        return
    if route.action == 'dismiss_keyboard_frame':
        broker.dismiss_keyboard_frame(route.token, route.frame_id)
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


