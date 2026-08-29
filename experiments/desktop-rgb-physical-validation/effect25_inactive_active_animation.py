#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Validate RGB animation while Host Interaction is inactive and active."""

from __future__ import annotations

import argparse
import math
import time

from abralia.interaction import (
    BindingEntry,
    BindingPolicy,
    ConfiguredBinding,
    ControlId,
    EventType,
    HostInteractionController,
    HostInteractionProtocolClient,
    Lifetime,
    Routing,
)
from abralia.rgb import (
    BLACK,
    EffectUnavailableError,
    PhysicalSceneBuilder,
    RgbController,
    Srgb8,
    load_profile,
)
from abralia.rgb.adapters.keychron_effect25 import (
    EffectSelectionPolicy,
    KeychronEffect25Adapter,
)

from abralia import (
    SharedHidMode,
    SharedKeyboardCoordinator,
    SharedKeyboardState,
    SharedRawHidSession,
)

INTERACTION_REGION = "navigation_cluster"
PAUSE_CONTROL = ControlId.key(0, 16)
POLL_MS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("cooperative", "threaded"), default="cooperative"
    )
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--brightness", type=int, default=160)
    parser.add_argument("--routing", choices=("mirror", "capture"), default="mirror")
    parser.add_argument("--device-index", type=int)
    parser.add_argument(
        "--yes", action="store_true", help="skip the typed ANIMATE confirmation"
    )
    return parser.parse_args()


def region_controls(profile) -> tuple[ControlId, ...]:
    controls = []
    for element_id in profile.regions[INTERACTION_REGION].elements:
        matrix = profile.element_by_id[element_id].matrix
        if matrix is None:
            continue
        control = ControlId.key(*matrix)
        if control != PAUSE_CONTROL and control not in controls:
            controls.append(control)
    return tuple(controls)


def bindings(
    controls: tuple[ControlId, ...], routing: Routing
) -> tuple[ConfiguredBinding, ...]:
    policy = BindingPolicy(
        routing=routing,
        lifetime=Lifetime.SESSION,
        duration_ms=0,
        emit_down=True,
        emit_up=False,
    )
    return tuple(
        ConfiguredBinding(BindingEntry(control, binding_id), policy)
        for binding_id, control in enumerate(controls, start=1)
    )


def scale(color: Srgb8, amount: float) -> Srgb8:
    return Srgb8(
        round(color.red * amount),
        round(color.green * amount),
        round(color.blue * amount),
    )


class InteractiveAnimationProducer:
    def __init__(
        self,
        rgb: RgbController,
        profile,
        *,
        fps: float,
        brightness: int,
    ):
        self.rgb = rgb
        self.profile = profile
        self.fps = fps
        self.brightness = brightness
        self.state = SharedKeyboardState.RGB_SUSPENDED
        self.started_at = time.monotonic()
        self.next_frame_at = self.started_at
        self.lease = None
        self.standby_frames = 0
        self.active_frames = 0

    def close_lease(self) -> None:
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def suspend(self) -> None:
        self.close_lease()
        if self.state is SharedKeyboardState.RGB_SUSPENDED:
            return
        self.state = SharedKeyboardState.RGB_SUSPENDED
        print("ANIMATION_STATE=RGB_SUSPENDED", flush=True)

    def resume_standby(self, *, restart: bool) -> None:
        self.close_lease()
        self.state = SharedKeyboardState.STANDBY
        if restart:
            self.started_at = time.monotonic()
        self.next_frame_at = time.monotonic()
        print("ANIMATION_STATE=STANDBY pause=mint", flush=True)

    def resume_active(self, *, restart: bool) -> None:
        self.close_lease()
        self.state = SharedKeyboardState.ACTIVE
        if restart:
            self.started_at = time.monotonic()
        self.next_frame_at = time.monotonic()
        print("ANIMATION_STATE=ACTIVE pause=amber", flush=True)

    def scene(self, elapsed: float):
        colors: dict[str, Srgb8] = {}
        for element in self.profile.elements:
            if element.led_address is None:
                continue
            x = element.geometry.x if element.geometry is not None else element.order
            wave = (math.sin(elapsed * 1.25 - x * 0.55) + 1.0) / 2.0
            colors[element.element_id] = Srgb8(
                round(5 + 12 * wave),
                round(10 + 18 * (1.0 - wave)),
                round(24 + 46 * wave),
            )

        breathing = 0.28 + 0.72 * (
            (math.sin(elapsed / 5.0 * math.tau - math.pi / 2) + 1.0) / 2.0
        )
        pause_color = (
            Srgb8(245, 145, 24)
            if self.state is SharedKeyboardState.ACTIVE
            else Srgb8(55, 235, 165)
        )
        colors["PAUSE"] = scale(pause_color, breathing)
        region_color = (
            Srgb8(155, 78, 15)
            if self.state is SharedKeyboardState.ACTIVE
            else Srgb8(16, 72, 58)
        )
        for element_id in self.profile.regions[INTERACTION_REGION].elements:
            colors[element_id] = region_color
        return PhysicalSceneBuilder().build(
            "interaction-availability-animation",
            colors,
            background=BLACK,
            owner="effect25-inactive-active-animation",
        )

    def tick(self) -> None:
        if self.state is SharedKeyboardState.RGB_SUSPENDED:
            return
        now = time.monotonic()
        if now < self.next_frame_at:
            return
        self.close_lease()
        try:
            self.lease = self.rgb.display(
                [self.scene(now - self.started_at)],
                brightness_ceiling=self.brightness,
            )
        except EffectUnavailableError:
            self.suspend()
            return
        if self.state is SharedKeyboardState.ACTIVE:
            self.active_frames += 1
        else:
            self.standby_frames += 1
        self.next_frame_at = now + 1.0 / self.fps

    def close(self) -> None:
        self.close_lease()


def run(args: argparse.Namespace) -> bool:
    profile = load_profile()
    controls = region_controls(profile)
    routing = Routing.MIRROR if args.routing == "mirror" else Routing.CAPTURE
    labels = {
        binding_id: element_id
        for binding_id, element_id in enumerate(
            profile.regions[INTERACTION_REGION].elements, start=1
        )
    }
    with SharedRawHidSession.open_keychron_v3_8k(
        device_index=args.device_index,
        mode=SharedHidMode(args.mode),
    ) as session:
        adapter = KeychronEffect25Adapter(
            session.rgb_transport(),
            session.device_info,
            effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
        )
        protocol = HostInteractionProtocolClient(session.interaction_transport())
        with RgbController(adapter, profile) as rgb, protocol:
            interaction = HostInteractionController(protocol)
            producer = InteractiveAnimationProducer(
                rgb,
                profile,
                fps=args.fps,
                brightness=args.brightness,
            )
            coordinator = SharedKeyboardCoordinator(rgb, protocol, producer)
            interaction.replace_bindings(bindings(controls, routing))
            coordinator.initialize()
            started_at = time.monotonic()
            control_events = 0
            try:
                while time.monotonic() - started_at < args.seconds:
                    for event in protocol.service(timeout_ms=POLL_MS):
                        coordinator.handle_event(event)
                        if event.event_type is EventType.CONTROL_EDGE:
                            control_events += 1
                            print(
                                f"EVENT binding_id={event.binding_id} "
                                f"control_id={event.control_id} "
                                f"element={labels.get(event.binding_id, '?')}",
                                flush=True,
                            )
                        elif event.event_type is EventType.MODE_CHANGED:
                            print(
                                f"MODE active={str(event.mode_active).lower()}",
                                flush=True,
                            )
                        elif event.event_type is EventType.RGB_EFFECT_CHANGED:
                            print(
                                "RGB_EFFECT effect25="
                                f"{str(event.rgb_effect25_selected).lower()}",
                                flush=True,
                            )
                    producer.tick()
                    time.sleep(0.005)
            finally:
                producer.close()
                interaction.turn_off_all()
    passed = (
        producer.standby_frames > 0
        and producer.active_frames > 0
        and control_events > 0
    )
    print(
        f"ANIMATION_RESULT={'PASS' if passed else 'FAIL'} "
        f"standby_frames={producer.standby_frames} "
        f"active_frames={producer.active_frames} "
        f"control_events={control_events}",
        flush=True,
    )
    return passed


def main() -> int:
    args = parse_args()
    if args.seconds <= 0 or args.fps <= 0:
        print("ERROR: --seconds and --fps must be positive.")
        return 1
    if not 0 <= args.brightness <= 255:
        print("ERROR: --brightness must be in 0...255.")
        return 1
    if not args.yes:
        confirmation = input(
            "Type ANIMATE to install volatile navigation-cluster bindings and run: "
        )
        if confirmation != "ANIMATE":
            print("ERROR: confirmation did not match.")
            return 1
    try:
        return 0 if run(args) else 2
    except KeyboardInterrupt:
        print("Interrupted; interaction release and RGB restoration were requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
