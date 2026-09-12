#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""F1 notification demo with physical single-tap capture and double-tap activation."""

from __future__ import annotations

import argparse
import math
import subprocess
import time
from uuid import UUID

from abralia import SharedKeyboardCoordinator, SharedRawHidSession
from abralia.interaction import (
    BindingEntry, BindingPolicy, ConfiguredBinding, ControlId, Edge, EventType,
    HostInteractionController, HostInteractionProtocolClient, Lifetime, Routing,
)
from abralia.rgb import EffectUnavailableError, PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from abralia.rgb.adapters.keychron_effect25 import EffectSelectionPolicy, KeychronEffect25Adapter
from abralia.rgb.colors import LinearRgb, linear_to_srgb8, to_linear_rgb
from f1_breathing_notification import OWNER, argument_parser, breathing_color, build_phases
from main_area_notification_flash import FLASH_COLOR
from white_idle_expanding_notification import blend


def task_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a Codex task UUID, not a command or URL.") from error


def open_task(value: str):
    """Dispatch only a validated task URL; never block the HID service loop."""
    target = str(UUID(value))
    return subprocess.Popen(["/usr/bin/open", f"codex://threads/{target}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argument_parser()
    parser.description = __doc__
    parser.add_argument("--mute-element", default=None,
                        help="optional label check; the profile's fixed physical toggle is always used")
    parser.add_argument("--pickup-element", default="SCROLL_LOCK")
    parser.add_argument("--pickup-background", choices=("white", "status"), default="white",
                        help="main-region color after pickup while still active")
    parser.add_argument("--timeout", type=float, default=120.0, help="bounded total runtime")
    parser.add_argument("--pickup-task-id", type=task_id,
                        help="open this exact local Codex task on pickup; omit for a text-only simulation")
    parser.add_argument("--idle-return-seconds", type=float, default=15.0,
                        help="return backlighting to white after muted/picked-up inactivity; activation is unchanged")
    parser.add_argument("--return-fade-seconds", type=float, default=1.0,
                        help="visual transition to white; never changes input activation")
    parser.add_argument("--inactive-saturation-percent", type=float, default=75.0,
                        help="routine color saturation retained while inactive; notifications are exempt; 100 disables reduction")
    return parser.parse_args(argv)


def scale_saturation(color: Srgb8, fraction: float) -> Srgb8:
    """Scale HSV saturation, preserving hue and value (including neutral white)."""
    peak = max(color.red, color.green, color.blue)
    return Srgb8(*(round(peak + (channel - peak) * fraction)
                   for channel in (color.red, color.green, color.blue)))


def toggle_breath(args: argparse.Namespace, color: Srgb8, now: float) -> Srgb8:
    """Breathe a fixed cue color without the main region's status-color blend."""
    wave = (1 + math.cos(2 * math.pi * now / args.breath_period)) / 2
    level = (args.breath_min_percent +
             (args.breath_max_percent - args.breath_min_percent) * wave) / 100
    linear = to_linear_rgb(color)
    return linear_to_srgb8(LinearRgb(*(channel * level for channel in
                                      (linear.red, linear.green, linear.blue))))


def toggle_saturation_breath(args: argparse.Namespace, now: float) -> Srgb8:
    """Breathe white to the attention hue at fixed HSV value, independently of dimming."""
    wave = (1 + math.cos(2 * math.pi * now / args.breath_period)) / 2
    return scale_saturation(FLASH_COLOR, wave)


class Demo:
    """Pure state and rendering; all input is gated by mode and notification generation."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        phases = build_phases(args)
        self.profile = load_profile(args.profile)
        if args.mute_element is None:
            args.mute_element = self.profile.interaction_toggle_element_id()
        self.white = phases[0].frames[0].payload.background
        self.slot_scene = phases[1].frames[0]
        self.onset = phases[2:6]
        self.onset_seconds = sum(p.seconds for p in self.onset)
        self.region_ids = tuple(phases[5].frames[0].payload.colors)
        self.region_ids = tuple(x for x in self.region_ids if x != args.slot_element)
        self.controls = {}
        for action, element_id in (("mute", args.mute_element), ("pickup", args.pickup_element)):
            element = self.profile.element_by_id.get(element_id)
            if element is None or not element.rgb_capable or element.matrix is None:
                raise ValueError(f"{action} needs an RGB-capable matrix element in this profile.")
            is_toggle = element.matrix == self.profile.device_profile.require_interaction().toggle_matrix
            if action == "mute" and not is_toggle:
                raise ValueError("Mute must use the mode-toggle key (Pause); Print Screen is not a substitute.")
            if action == "pickup" and is_toggle:
                raise ValueError("Pickup must be separate from the Pause mode-toggle/mute key.")
            if element_id == args.slot_element or element_id in self.region_ids:
                raise ValueError("Action keys must be separate from the slot and notification region.")
            self.controls[action] = ControlId.key(*element.matrix)
        if args.mute_element == args.pickup_element:
            raise ValueError("Mute and pickup must use different controls.")
        if not math.isfinite(args.timeout) or not 15 <= args.timeout <= 600:
            raise ValueError("--timeout must be finite and in 15...600 seconds.")
        if not math.isfinite(args.idle_return_seconds) or not 0 <= args.idle_return_seconds <= 600:
            raise ValueError("--idle-return-seconds must be finite and in 0...600.")
        if not math.isfinite(args.return_fade_seconds) or not 0 <= args.return_fade_seconds <= 5:
            raise ValueError("--return-fade-seconds must be finite and in 0...5.")
        if not math.isfinite(args.inactive_saturation_percent) or not 0 <= args.inactive_saturation_percent <= 100:
            raise ValueError("--inactive-saturation-percent must be finite and in 0...100.")
        self.active = False
        self.generation = 1
        self.notification_at = args.idle_seconds + args.slot_seconds
        self.outcome = None
        self.selected = False
        self.last_action_at = None
        self.return_started_at = None
        self.exited_after_pickup_at = None

    def pending(self, now: float) -> bool:
        return self.outcome in (None, "muted") and now >= self.notification_at

    def auto_backlight_return_due(self, now: float) -> bool:
        return (self.active and self.outcome in ("muted", "picked_up")
                and self.last_action_at is not None and self.args.idle_return_seconds > 0
                and now - self.last_action_at >= self.args.idle_return_seconds)

    def return_backlight_to_white(self, now: float) -> None:
        if self.selected:
            self.return_started_at = now
        self.selected = False
        self.last_action_at = None

    def set_active(self, active: bool, now: float) -> None:
        if active and not self.active:
            self.exited_after_pickup_at = None
            self.return_started_at = None
        if self.active and not active:
            if self.selected:
                self.return_started_at = now
            self.selected = False
            self.last_action_at = None
        self.active = active
        if not active and self.outcome == "picked_up":
            self.exited_after_pickup_at = now

    def act(self, action: str, generation: int, now: float) -> bool:
        if not self.active or generation != self.generation:
            return False
        if action == "select" and now >= self.args.idle_seconds:
            self.selected = True
            self.return_started_at = None
            self.last_action_at = now
            return True
        if not self.pending(now):
            return False
        if action == "mute":
            if self.outcome == "muted":
                return False
            self.outcome = "muted"
        elif action == "pickup":
            self.outcome = "picked_up"
            self.selected = self.args.pickup_background == "status"
        else:
            return False
        self.last_action_at = now
        return True

    def bindings(self, now: float) -> tuple[ConfiguredBinding, ...]:
        if now < self.args.idle_seconds:
            return ()
        policy = BindingPolicy(routing=Routing.CAPTURE, lifetime=Lifetime.SESSION,
                               emit_down=False, emit_up=True)
        slot = self.profile.element_by_id[self.args.slot_element]
        bindings = [ConfiguredBinding(BindingEntry(ControlId.key(*slot.matrix), 1), policy)]
        if self.pending(now):
            bindings.append(ConfiguredBinding(BindingEntry(self.controls["mute"], self.generation * 2), policy))
            bindings.append(ConfiguredBinding(BindingEntry(self.controls["pickup"], self.generation * 2 + 1), policy))
        return tuple(bindings)

    def frame(self, now: float):
        colors = {}
        notification_visible = False
        if now >= self.args.idle_seconds:
            colors[self.args.slot_element] = self.args.status_color
        if self.active and self.selected:
            colors.update(dict.fromkeys(self.region_ids, self.args.status_color))
        elif self.return_started_at is not None and now - self.return_started_at < self.args.return_fade_seconds:
            fraction = (now - self.return_started_at) / self.args.return_fade_seconds
            colors.update(dict.fromkeys(self.region_ids, blend(self.args.status_color, self.white, fraction)))
        elif self.pending(now) and self.outcome != "muted":
            notification_visible = True
            elapsed = now - self.notification_at
            for phase in self.onset:
                if elapsed < phase.seconds:
                    index = min(len(phase.frames) - 1, int(elapsed / phase.seconds * len(phase.frames)))
                    colors.update(phase.frames[index].payload.colors)
                    break
                elapsed -= phase.seconds
            else:
                colors.update(dict.fromkeys(self.region_ids, breathing_color(self.args, elapsed)))
        if not self.active:
            # Keep the notification's own animation intact; whiten routine colors only.
            colors = {element: color if notification_visible and element in self.region_ids
                      else scale_saturation(color, self.args.inactive_saturation_percent / 100)
                      for element, color in colors.items()}
        if self.active:
            # The gesture remains available even after the notification is handled.
            cue = Srgb8(255, 0, 0) if self.pending(now) else self.white
            colors[self.args.mute_element] = toggle_breath(self.args, cue, now)
            if self.pending(now):
                colors[self.args.pickup_element] = Srgb8(0, 255, 0)
        elif self.pending(now):
            # A muted notification still has a pickup action in active mode.
            colors[self.args.mute_element] = toggle_saturation_breath(self.args, now)
        return PhysicalSceneBuilder().build("interactive-notification", colors,
                                            background=self.white, owner=OWNER)


class Producer:
    def __init__(self, rgb: RgbController, demo: Demo, start: float):
        self.rgb, self.demo, self.start = rgb, demo, start
        self.suspended = True
        self.lease = None
        self.last_payload = None
        self.refresh_at = 0.0
        self.next_frame_at = 0.0

    def close(self):
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def suspend(self):
        self.close()
        self.suspended = True
        self.demo.set_active(False, time.monotonic() - self.start)
        self.last_payload = None

    def resume_standby(self, *, restart: bool):
        self.suspended = False
        self.demo.set_active(False, time.monotonic() - self.start)
        self.next_frame_at = 0.0

    def resume_active(self, *, restart: bool):
        self.suspended = False
        self.demo.set_active(True, time.monotonic() - self.start)
        self.next_frame_at = 0.0

    def tick(self, now: float):
        if self.suspended or now < self.next_frame_at:
            return
        frame = self.demo.frame(now)
        try:
            if self.lease is None or frame.payload != self.last_payload:
                self.close()
                self.lease = self.rgb.display([frame], brightness_ceiling=255)
                self.last_payload = frame.payload
                self.refresh_at = now + 1
            elif now >= self.refresh_at:
                self.lease.refresh()
                self.refresh_at = now + 1
        except EffectUnavailableError:
            self.suspend()
        self.next_frame_at = now + 1 / self.demo.args.fps


def dry_run(demo: Demo) -> None:
    now = demo.notification_at + demo.onset_seconds + 5
    demo.frame(0)
    demo.frame(now)
    assert not demo.act("mute", 1, now)
    print("SIMULATED inactive: white baseline; pending toggle breathes white/yellow-green saturation at fixed value; routine colors whiten; notifications exempt; mute is ignored.")
    demo.set_active(True, now)
    assert demo.act("mute", 1, now)
    demo.frame(now)
    print("SIMULATED double Pause -> active; red toggle breathes; single Pause mute -> white with F1 status; no re-notification.")
    assert demo.act("select", demo.generation, now + 1)
    demo.frame(now + 1)
    print("SIMULATED single F1 after mute -> static status color; mute remains in force.")
    demo.return_backlight_to_white(now + 16)
    assert demo.active
    demo.frame(now + 16 + demo.args.return_fade_seconds)
    print("SIMULATED timed return -> white backlight only; activation remains enabled.")
    now += 16 + demo.args.return_fade_seconds
    assert not demo.act("pickup", 0, now)
    assert demo.act("pickup", demo.generation, now)
    demo.frame(now)
    print(f"SIMULATED pickup: conversation action logged only; background={demo.args.pickup_background}.")
    demo.set_active(False, now)
    demo.frame(now + demo.args.return_fade_seconds)
    print("SIMULATED double Pause -> ordinary input immediately; main region fades to white, F1 status retained.")
    print("DRY_RUN_OK: no keyboard opened; no physical gestures or focus operation performed.")


def run(args: argparse.Namespace) -> None:
    demo = Demo(args)
    if args.dry_run:
        dry_run(demo)
        return
    print("Firmware captures single taps at the fixed toggle; double tap changes activation.")
    print("While active: the red toggle mutes, F1 selects, Scroll Lock picks up; Print Screen stays ordinary.")
    print("The timed white-backlight return changes lighting only.")
    opener = None
    with SharedRawHidSession.open_profile(
        demo.profile.device_profile, device_index=args.device_index
    ) as session:
        adapter = KeychronEffect25Adapter(session.rgb_transport(), session.device_info,
                                         profile=demo.profile.device_profile,
                                         effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED)
        protocol = HostInteractionProtocolClient(session.interaction_transport(), profile=demo.profile.device_profile)
        if not protocol.get_capabilities().supports_toggle_single_tap:
            raise RuntimeError("This demo requires the matching firmware with physical single-tap capture. No substitute key or OS listener will be used.")
        with RgbController(adapter, demo.profile) as rgb, protocol:
            interaction = HostInteractionController(protocol)
            started = time.monotonic()
            producer = Producer(rgb, demo, started)
            coordinator = SharedKeyboardCoordinator(rgb, protocol, producer)
            installed = ()
            installed_generation = None
            try:
                coordinator.initialize()
                if not coordinator.rgb_effect25_selected:
                    raise RuntimeError("Select enabled RGB effect 25 before starting this demo.")
                print("DEMO_READY: physical tap capture enabled; white background begins now.", flush=True)
                while True:
                    now = time.monotonic() - started
                    if now >= args.timeout:
                        print("DEMO_TIMEOUT: releasing temporary controls and restoring RGB.")
                        break
                    if opener is not None and opener.poll() is not None:
                        print(f"TASK_OPEN_{'DISPATCHED_UNVERIFIED' if opener.returncode == 0 else 'FAILED'}", flush=True)
                        opener = None
                    if demo.auto_backlight_return_due(now):
                        demo.return_backlight_to_white(now)
                        print("WHITE_BACKLIGHT_RETURN: activation unchanged.", flush=True)
                    desired = demo.bindings(now)
                    if desired != installed:
                        installed_generation = interaction.replace_bindings(desired).binding_generation
                        installed = desired
                    for event in protocol.service(timeout_ms=10):
                        coordinator.handle_event(event)
                        if event.event_type is EventType.QUEUE_OVERFLOW:
                            raise RuntimeError("Input queue overflow; ending the demo safely.")
                        if event.event_type is EventType.RGB_EFFECT_CHANGED and not event.rgb_effect25_selected:
                            demo.outcome = "cancelled"
                        if event.event_type is EventType.MODE_CHANGED:
                            print(f"ACTIVE={event.mode_active}; backlighting remains on.", flush=True)
                        if (event.event_type is EventType.CONTROL_EDGE and event.edge is Edge.UP
                                and event.binding_generation == installed_generation):
                            slot = demo.profile.element_by_id[args.slot_element]
                            if event.binding_id == 1 and event.control_id == ControlId.key(*slot.matrix):
                                if demo.act("select", demo.generation, time.monotonic() - started):
                                    print("F1_SELECTED: showing status color; mute unchanged.", flush=True)
                            elif event.binding_id == demo.generation * 2 and event.control_id == demo.controls["mute"]:
                                if demo.act("mute", demo.generation, time.monotonic() - started):
                                    print("MUTED_BY_PHYSICAL_TAP: F1 and pickup remain available.", flush=True)
                            elif event.binding_id == demo.generation * 2 + 1 and event.control_id == demo.controls["pickup"]:
                                if demo.act("pickup", demo.generation, time.monotonic() - started):
                                    print("PICKED_UP", flush=True)
                                    if args.pickup_task_id:
                                        try:
                                            opener = open_task(args.pickup_task_id)
                                        except OSError as error:
                                            print(f"TASK_OPEN_FAILED: {error}", flush=True)
                                    else:
                                        print("CONVERSATION_SELECTION_SIMULATED", flush=True)
                    producer.tick(time.monotonic() - started)
                    if demo.exited_after_pickup_at is not None and now - demo.exited_after_pickup_at >= args.post_seconds:
                        break
                    time.sleep(0.002)
            finally:
                producer.close()
                if opener is not None and opener.poll() is None:
                    opener.terminate()


def main() -> int:
    args = parse_args()
    try:
        Demo(args)
        if not args.dry_run and not args.yes:
            if input("Physical tap capture and bounded RGB demo. Type DEMO to start: ") != "DEMO":
                print("Cancelled; no keyboard opened.")
                return 1
        run(args)
    except KeyboardInterrupt:
        print("Interrupted; session release and RGB restoration were requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
