# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Profile-driven rendering of the accepted notification and paged slot UI."""

from __future__ import annotations

import math

from abralia.rgb import PhysicalSceneBuilder, Srgb8
from abralia.rgb.colors import LinearRgb, linear_to_srgb8, to_linear_rgb
from .core import Allocation, Broker
from .navigation import NAVIGATION_KEYS, NAVIGATION_COLOR_ROLES, PICKUP_KEY, MUTE_KEY, PAGE_KEYS

WHITE = Srgb8(255, 255, 255)
ATTENTION = Srgb8(160, 255, 0)
ENTER_HIGHLIGHT = ATTENTION
RED = Srgb8(255, 0, 0)
GREEN = Srgb8(0, 255, 0)
ESC_HIGHLIGHT = RED
SLOT_BACKGROUND_RATIO = .5


def hex_color(value: str) -> Srgb8:
    return Srgb8(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


def blend(a: Srgb8, b: Srgb8, fraction: float) -> Srgb8:
    f = min(1, max(0, fraction))
    return Srgb8(*(round(x + (y - x) * f) for x, y in zip(
        (a.red, a.green, a.blue), (b.red, b.green, b.blue))))


def saturation(color: Srgb8, fraction: float) -> Srgb8:
    peak = max(color.red, color.green, color.blue)
    return Srgb8(*(round(peak + (c - peak) * fraction) for c in (color.red, color.green, color.blue)))


def intensity(color: Srgb8, value: float) -> Srgb8:
    linear = to_linear_rgb(color)
    return linear_to_srgb8(LinearRgb(*(c * value for c in (linear.red, linear.green, linear.blue))))


def wave(now: float, period: float = 2) -> float:
    return (1 + math.cos(2 * math.pi * now / period)) / 2


def within_frame_peak(color: Srgb8, ceiling: int) -> Srgb8:
    """Encode a full-range color relative to the existing frame maximum."""
    return Srgb8(*(round(channel * ceiling / 255) for channel in (color.red, color.green, color.blue)))


def at_value(color: Srgb8, value: int) -> Srgb8:
    peak = max(color.red, color.green, color.blue)
    return Srgb8(*(round(channel * value / peak) for channel in (color.red, color.green, color.blue))) if peak else color


def breathing_status(color: Srgb8, elapsed: float) -> Srgb8:
    fraction = min(1, max(0, elapsed / 4))
    fraction = fraction * fraction * (3 - 2 * fraction)
    level = .12 + (1 + (.5 - 1) * fraction - .12) * wave(elapsed)
    a, b = to_linear_rgb(ATTENTION), to_linear_rgb(color)
    return linear_to_srgb8(LinearRgb(*((x + (y - x) * fraction) * level
        for x, y in zip((a.red, a.green, a.blue), (b.red, b.green, b.blue)))))


class Renderer:
    def __init__(self, profile, *, fps: float = 30):
        self.profile = profile
        self.toggle = profile.interaction_toggle_element_id()
        self.pickup, self.mute = PICKUP_KEY, MUTE_KEY
        self.f_keys = [f"F{i}" for i in range(1, 13)]
        for element in (*self.f_keys, self.toggle, self.pickup, self.mute, *PAGE_KEYS):
            if element not in profile.element_by_id or not profile.element_by_id[element].rgb_capable:
                raise ValueError(f"profile cannot render required control: {element}")
        self.region = tuple(profile.regions["alphanumeric_block"].elements)
        if set(self.region) & set((*self.f_keys, self.toggle, self.pickup, self.mute)):
            raise ValueError("notification region overlaps reserved controls")
        raw = []
        for key in self.region:
            e = profile.element_by_id[key]
            if not e.rgb_capable:
                raise ValueError(f"notification key {key} has no RGB address")
            raw.append((key, e.led_point.x if e.led_point else e.geometry.x + e.geometry.width / 2,
                        e.led_point.y if e.led_point else e.geometry.y + e.geometry.height / 2))
        left, right = min(x for _, x, _ in raw), max(x for _, x, _ in raw)
        top, bottom = min(y for _, _, y in raw), max(y for _, _, y in raw)
        points = [(key, math.hypot((x - (left + right) / 2) / max((right - left) / 2, .5),
                                  (y - (top + bottom) / 2) / max((bottom - top) / 2, .5))) for key, x, y in raw]
        self.points, self.fps = points, fps
        self.background_percent = None
        self._set_background(50)

    def _set_background(self, percent: float):
        if percent == self.background_percent:
            return
        self.background_percent = percent
        value = round(255 * percent / 100)
        self.white = Srgb8(value, value, value)
        points, fps = self.points, self.fps
        low, high = min(r for _, r in points), max(r for _, r in points)
        steps = max(2, math.ceil(.2 * fps))
        activation = {key: round((radius - low) / max(high - low, 1e-9) * (steps - 1)) for key, radius in points}
        levels = dict.fromkeys(self.region, 0.0)
        expansion = []
        base_value = min(value, 26)
        base = Srgb8(base_value, base_value, base_value)
        for step in range(steps):
            levels = {key: 1.0 if activation[key] == step else level * .5 for key, level in levels.items()}
            expansion.append({key: blend(base, ATTENTION, level) for key, level in levels.items()})
        fill = []
        steps = max(2, math.ceil(.1 * fps))
        for step in range(steps):
            frontier = high * (1 - step / (steps - 1))
            frame = {}
            for key, radius in points:
                p = min(1, max(0, (radius - frontier + .25) / .25))
                frame[key] = blend(expansion[-1][key], ATTENTION, p * p * (3 - 2 * p))
            fill.append(frame)
        steps = max(2, math.ceil(.18 * fps))
        prepare = [dict.fromkeys(self.region, blend(self.white, base, step / steps)) for step in range(1, steps + 1)]
        self.onset = ((.18, prepare), (.2, expansion), (.1, fill), (.1, [dict.fromkeys(self.region, ATTENTION)]))

    def state_color(self, broker: Broker, slot: Allocation, now: float, *, animate=True) -> Srgb8:
        """Task identity stays steady; explicit notifications own animation."""
        return hex_color(slot.identity_color)

    def slot_background(self, broker: Broker, color: Srgb8) -> Srgb8:
        # Scale the current background, not the keyboard's global brightness.
        return (blend(Srgb8(0, 0, 0), color, SLOT_BACKGROUND_RATIO)
                if broker.slots else color)

    def _render_overview(self, broker: Broker, colors: dict) -> Srgb8:
        if not broker.selection_preview_active():
            return self.white
        tint = blend(Srgb8(0, 0, 0), hex_color(broker.config.overview_tint),
                     broker.config.overview_tint_percent / 100)
        occupied = {f"F{(slot.position - 1) % 12 + 1}" for slot in broker.visible_slots()}
        for key in self.f_keys:
            if key not in occupied:
                colors[key] = self.slot_background(broker, tint)
        candidate = broker.overview_candidate()
        if candidate:
            key = f"F{(candidate.position - 1) % 12 + 1}"
            colors[key] = blend(hex_color(candidate.identity_color), ATTENTION,
                                broker.config.overview_highlight_percent / 100)
            colors["ENTER"] = ENTER_HIGHLIGHT
        return self.white

    def frame(self, broker: Broker):
        self._set_background(broker.config.background_brightness_percent)
        now = broker.clock()
        # Routine status is steady. A varying brightest key also modulates the
        # entire background on legacy effect-25 frame normalization.
        colors = (dict.fromkeys(self.f_keys, self.slot_background(broker, self.white))
                  if broker.slots else {})
        colors.update({f"F{(s.position - 1) % 12 + 1}": self.state_color(broker, s, now, animate=False)
                       for s in broker.visible_slots()})
        background = broker.slots.get(broker.background_slot)
        return_fraction = 1.0
        if background:
            returned = broker.background_return_at
            fraction = 0.0 if returned is None else min(1.0, max(0.0, (now - returned) / broker.config.fade_seconds))
            return_fraction = fraction * fraction * (3 - 2 * fraction)
        if not broker.active:
            colors = {key: saturation(color, broker.config.inactive_saturation_percent / 100) for key, color in colors.items()}
        # Notification output is composed after routine whitening so its colors are exempt.
        slot = broker.slots.get(broker.current_call)
        if slot and slot.notification and slot.notification.status == "active" and background is None:
            elapsed = max(0, now - slot.notification.started_at)
            for seconds, frames in self.onset:
                if elapsed < seconds:
                    colors.update(frames[min(len(frames) - 1, int(elapsed / seconds * len(frames)))])
                    break
                elapsed -= seconds
            else:
                color = self.state_color(broker, slot, now, animate=False)
                colors.update(dict.fromkeys(self.region, breathing_status(color, elapsed)))
        # Paint valid overview controls after notification frames so Enter's
        # cue stays visible. Picked-up questions disable overview capture.
        scene_background = self._render_overview(broker, colors)
        scaled_controls = {"ENTER"} if broker.overview_candidate() else set()
        question_highlights = set()
        target = broker.pending_target()
        if broker.active:
            if broker.navigation_active:
                for key, action in NAVIGATION_KEYS.items():
                    colors[key] = hex_color(broker.config.navigation_colors[NAVIGATION_COLOR_ROLES[action]])
                    scaled_controls.add(key)
                for key, (action, _) in broker.attention_controls().items():
                    colors[key] = ATTENTION if action in ('unmute_agent', 'restore_all') else RED
                    scaled_controls.add(key)
            if broker.config.escape_exits_focus and broker.focused_slot() and return_fraction < 1:
                colors["ESC"] = ESC_HIGHLIGHT
                scaled_controls.add("ESC")
            if target:
                colors[self.pickup] = GREEN
            if broker.mute_available():
                colors[self.mute] = intensity(RED, .5)
            selected = broker.selected_slot()
            if (selected and selected.question and selected.question.picked_up
                    and not broker.navigation_active and not broker.knob_selection_active()):
                for binding in broker.question_keys(selected):
                    key = binding["key"]
                    question_highlights.add(key)
                    if key not in self.profile.element_by_id:
                        raise ValueError(f"question shortcut not present in profile: {key}")
                    colors[key] = (ENTER_HIGHLIGHT if binding["action"] == "submit_or_advance" else ATTENTION
                                   if binding["action"] == "other" else hex_color(broker.config.colors["progressing"]))
        elif broker.has_attention():
            colors[self.toggle] = saturation(ATTENTION, wave(now))

        for item in broker.page_display()['keys']:
            key = item['key']
            if key in question_highlights:
                continue
            color = hex_color(broker.config.page_current_color if item['current'] else broker.config.page_occupied_color)
            colors[key] = color if item['current'] else within_frame_peak(
                color, round(255 * broker.config.page_occupied_brightness_percent / 100))
            scaled_controls.add(key)

        # The existing non-slot scene supplies the global frame reference.
        # Occupying an F-key must not raise that maximum and make unchanged
        # keys dim through effect 25's normalization. Empty slot backgrounds
        # retain their own relative multiplier. With an entirely black base,
        # retain visible slot signals: there is no lit background to preserve.
        global_reference_v = max(channel for color in (scene_background, *(
            color for key, color in colors.items() if key not in self.f_keys and key not in scaled_controls))
            for channel in (color.red, color.green, color.blue))

        if broker.active:
            # The normal mode cue breathes in saturation at fixed HSV value.
            # This gesture cue is independent of the separate call controls.
            # A black background still permits the Agent Mode indicator.
            cue = saturation(ATTENTION, wave(now))
            colors[self.toggle] = within_frame_peak(cue, global_reference_v or 255)
            global_reference_v = max(global_reference_v, colors[self.toggle].red,
                                 colors[self.toggle].green, colors[self.toggle].blue)

        if global_reference_v:
            for slot in broker.visible_slots():
                key = f"F{(slot.position - 1) % 12 + 1}"
                colors[key] = within_frame_peak(colors[key], global_reference_v)
            for key in scaled_controls:
                colors[key] = within_frame_peak(colors[key], global_reference_v)
            if "ESC" in scaled_controls:
                # Blend after reference scaling, otherwise the background
                # endpoint would be scaled twice and end below the main area.
                colors["ESC"] = blend(colors["ESC"], scene_background, return_fraction)

        candidate = broker.overview_candidate()
        if candidate:
            key = f'F{(candidate.position - 1) % 12 + 1}'
            others = [f'F{(slot.position - 1) % 12 + 1}' for slot in broker.visible_slots() if slot is not candidate]
            value = lambda c: max(c.red, c.green, c.blue)
            factor = broker.config.selection_brightness_percent / 100
            reference = max((value(colors[k]) for k in others), default=value(colors[key]))
            target = min(global_reference_v or 255, round(max(value(colors[key]), reference * factor)))
            colors[key] = at_value(saturation(colors[key], broker.config.selection_saturation_percent / 100), target)
            # Raising the frame maximum would dim the rest of this firmware's
            # scene. When headroom is exhausted, lower only the other slots.
            other_limit = math.floor(target / factor)
            for other in others:
                if value(colors[other]) > other_limit:
                    colors[other] = at_value(colors[other], other_limit)

        # Apply the muted appearance after the candidate blend and frame scaling.
        # This changes occupied F-keys only, never the shared brightness reference.
        for slot in broker.visible_slots():
            if broker.attention_muted(slot):
                key = f"F{(slot.position - 1) % 12 + 1}"
                colors[key] = within_frame_peak(
                    saturation(colors[key], broker.config.muted_saturation_percent / 100),
                    round(255 * broker.config.muted_brightness_percent / 100))

        if background and return_fraction < 1:
            # Fit the selected-color overlay to the already established scene
            # maximum. Its fade must not change other keys' brightness through
            # normalization. Keep live question/action cues above this layer.
            color = within_frame_peak(self.state_color(broker, background, now, animate=False),
                                      global_reference_v or 255)
            if not broker.active:
                color = saturation(color, broker.config.inactive_saturation_percent / 100)
            color = blend(color, scene_background, return_fraction)
            colors.update({key: color for key in self.region
                           if key not in question_highlights and key not in scaled_controls})

        gap = broker.gap_snapshot()
        if broker.active and gap and gap['page'] == broker.page + 1:
            feedback = broker.gap_feedback
            if gap['phase'] == 'flash' or broker.gap_valid(feedback['gap']):
                ceiling = global_reference_v or 255
                for key in gap['keys']:
                    if gap['phase'] == 'flash':
                        colors[key] = within_frame_peak(ATTENTION, ceiling)
                    else:
                        current = colors.get(key, scene_background)
                        start = max(current.red, current.green, current.blue)
                        progress = gap['progress'] ** 2 * (3 - 2 * gap['progress'])
                        colors[key] = at_value(current if start else WHITE,
                                               round(start + (ceiling - start) * progress))

        return PhysicalSceneBuilder().build("abralia-agent-backend", colors, background=scene_background, owner="abralia-backend")
