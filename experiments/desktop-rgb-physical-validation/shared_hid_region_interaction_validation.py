#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""User-assisted visual-state and interaction validation for every region."""

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
from abralia.rgb.profiles import EncoderDirection
from shared_hid_rgb_validation import PALETTE, combined_scenes, static_region_scene

from abralia import (
    SharedHidMode,
    SharedKeyboardCoordinator,
    SharedKeyboardState,
    SharedRawHidSession,
)

INTERACTION_POLL_MS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, help="explicit bundled profile ID or JSON path"
    )
    parser.add_argument(
        "--mode",
        choices=("cooperative", "threaded", "both"),
        default="cooperative",
    )
    parser.add_argument(
        "--seconds-per-region",
        type=float,
        default=0.0,
        help="maximum active seconds per region; 0 waits for double-tap the profile toggle",
    )
    parser.add_argument(
        "--combined-seconds",
        type=float,
        default=0.0,
        help="maximum active seconds for the combined phase; 0 waits for double-tap the profile toggle",
    )
    parser.add_argument(
        "--activation-timeout",
        type=float,
        default=0.0,
        help="maximum seconds to wait for activation; 0 waits indefinitely",
    )
    parser.add_argument("--pause-fps", type=float, default=20.0)
    parser.add_argument("--brightness", type=int, default=160)
    parser.add_argument(
        "--routing",
        choices=("capture", "mirror"),
        default="capture",
    )
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--require-all-controls", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed SHARED confirmation",
    )
    return parser.parse_args()


def control_for_element(element) -> ControlId | None:
    if element.matrix is not None:
        return ControlId.key(*element.matrix)
    if element.encoder is not None:
        if element.encoder.direction is EncoderDirection.CLOCKWISE:
            return ControlId.encoder_clockwise(element.encoder.index)
        return ControlId.encoder_counterclockwise(element.encoder.index)
    return None


def region_controls(profile, region_id: str) -> tuple[ControlId, ...]:
    toggle_control = ControlId.key(
        *profile.device_profile.require_interaction().toggle_matrix
    )
    controls: list[ControlId] = []
    for element_id in profile.regions[region_id].elements:
        control = control_for_element(profile.element_by_id[element_id])
        if control is None or control == toggle_control or control in controls:
            continue
        controls.append(control)
    return tuple(controls)


def binding_label(profile, control: ControlId) -> str:
    for element in profile.elements:
        if control_for_element(element) == control:
            return element.element_id
    return str(control)


def bindings_for_controls(
    profile,
    controls: tuple[ControlId, ...],
    routing: Routing,
) -> tuple[tuple[ConfiguredBinding, ...], dict[int, tuple[ControlId, str]]]:
    policy = BindingPolicy(
        routing=routing,
        lifetime=Lifetime.SESSION,
        duration_ms=0,
        emit_down=True,
        emit_up=False,
    )
    bindings = []
    labels = {}
    for binding_id, control in enumerate(controls, start=1):
        bindings.append(ConfiguredBinding(BindingEntry(control, binding_id), policy))
        labels[binding_id] = (control, binding_label(profile, control))
    return tuple(bindings), labels


def pause_breathing_scene(elapsed: float, element_id: str):
    phase = elapsed / 2.0 * math.tau
    breath = (math.sin(phase - math.pi / 2) + 1.0) / 2.0
    value = round(16 + 239 * breath * breath)
    return PhysicalSceneBuilder().build(
        "unactivated-pause-breathing",
        {element_id: Srgb8(value // 5, value // 2, value)},
        background=BLACK,
        owner="shared-hid-region-interaction-validation",
    )


class ValidationProducer:
    def __init__(self, rgb: RgbController, *, brightness: int, pause_fps: float):
        self.rgb = rgb
        self.toggle_element_id = rgb.hardware_profile.interaction_toggle_element_id()
        self.brightness = brightness
        self.pause_fps = pause_fps
        self.phase_name = ""
        self.active_scenes: list = []
        self.active_description = ""
        self.state = SharedKeyboardState.RGB_SUSPENDED
        self.started_at = time.monotonic()
        self.next_frame_at = self.started_at
        self.refresh_at = self.started_at
        self.lease = None

    def configure_phase(
        self, phase_name: str, scenes: list, active_description: str
    ) -> None:
        self.close_lease()
        self.phase_name = phase_name
        self.active_scenes = scenes
        self.active_description = active_description

    def close_lease(self) -> None:
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def suspend(self) -> None:
        self.close_lease()
        if self.state is SharedKeyboardState.RGB_SUSPENDED:
            return
        self.state = SharedKeyboardState.RGB_SUSPENDED
        print(f"RGB_STANDBY phase={self.phase_name} effect25=false", flush=True)

    def resume_standby(self, *, restart: bool) -> None:
        self.close_lease()
        self.state = SharedKeyboardState.STANDBY
        if restart:
            self.started_at = time.monotonic()
        self.next_frame_at = time.monotonic()
        print(
            f"WAITING_ACTIVATION phase={self.phase_name} "
            "scene=pause_breathing gesture=double_toggle",
            flush=True,
        )

    def resume_active(self, *, restart: bool) -> None:
        self.close_lease()
        self.state = SharedKeyboardState.ACTIVE
        if restart:
            self.started_at = time.monotonic()
        self.refresh_at = time.monotonic()
        print(self.active_description, flush=True)

    def tick(self) -> None:
        now = time.monotonic()
        try:
            if self.state is SharedKeyboardState.STANDBY and now >= self.next_frame_at:
                self.close_lease()
                self.lease = self.rgb.display(
                    [
                        pause_breathing_scene(
                            now - self.started_at, self.toggle_element_id
                        )
                    ],
                    brightness_ceiling=self.brightness,
                )
                self.next_frame_at = now + 1.0 / self.pause_fps
            elif self.state is SharedKeyboardState.ACTIVE:
                if self.lease is None:
                    self.lease = self.rgb.display(
                        self.active_scenes,
                        brightness_ceiling=self.brightness,
                    )
                    self.refresh_at = now + 1.0
                elif now >= self.refresh_at:
                    self.lease.refresh()
                    self.refresh_at = now + 1.0
        except EffectUnavailableError:
            self.suspend()

    def close(self) -> None:
        self.close_lease()


def run_phase(
    *,
    interaction: HostInteractionController,
    protocol: HostInteractionProtocolClient,
    coordinator: SharedKeyboardCoordinator,
    producer: ValidationProducer,
    initialize_coordinator: bool,
    profile,
    phase_name: str,
    controls: tuple[ControlId, ...],
    scenes: list,
    seconds: float,
    require_all: bool,
    routing: Routing,
    activation_timeout: float,
) -> bool:
    bindings, labels = bindings_for_controls(profile, controls, routing)
    seen: set[ControlId] = set()
    configured = False
    manual_active = False
    deactivated = False
    wait_started = time.monotonic()
    active_deadline = None
    producer.configure_phase(
        phase_name,
        scenes,
        f"CAPTURE_DISPLAY phase={phase_name} scene=region controls={len(controls)} "
        f"routing={routing.name}",
    )
    try:
        interaction.replace_bindings(bindings)
        configured = True
        if initialize_coordinator:
            coordinator.initialize()
        elif coordinator.state is SharedKeyboardState.STANDBY:
            producer.resume_standby(restart=True)
        while True:
            now = time.monotonic()
            for event in protocol.service(timeout_ms=INTERACTION_POLL_MS):
                effect_available_before = coordinator.rgb_effect25_selected
                coordinator.handle_event(event)
                if event.event_type is EventType.RGB_EFFECT_CHANGED:
                    print(
                        f"RGB_EFFECT phase={phase_name} "
                        f"effect25={str(event.rgb_effect25_selected).lower()}",
                        flush=True,
                    )
                    if event.rgb_effect25_selected:
                        wait_started = time.monotonic()
                    else:
                        manual_active = False
                        active_deadline = None
                    continue
                if event.event_type is EventType.MODE_CHANGED:
                    manual_active = event.mode_active
                    print(
                        f"MANUAL_MODE phase={phase_name} "
                        f"active={str(event.mode_active).lower()}",
                        flush=True,
                    )
                    if event.mode_active:
                        active_deadline = (
                            time.monotonic() + seconds if seconds else None
                        )
                    elif effect_available_before:
                        manual_active = False
                        deactivated = True
                    continue
                if event.event_type is not EventType.CONTROL_EDGE:
                    continue
                control, label = labels.get(
                    event.binding_id, (event.control_id, str(event.control_id))
                )
                seen.add(control)
                print(
                    f"EVENT phase={phase_name} binding_id={event.binding_id} "
                    f"control_id={event.control_id} element={label}",
                    flush=True,
                )
            producer.tick()
            if deactivated:
                break
            if (
                coordinator.state is not SharedKeyboardState.ACTIVE
                and activation_timeout
                and now - wait_started >= activation_timeout
            ):
                print(f"ACTIVATION_TIMEOUT phase={phase_name}", flush=True)
                return False
            if active_deadline is not None and now >= active_deadline:
                print(
                    f"DEACTIVATION_TIMEOUT phase={phase_name} gesture=double_toggle",
                    flush=True,
                )
                return False
            time.sleep(0.01)
        interaction.clear_bindings()
        configured = False
    finally:
        if manual_active:
            try:
                interaction.turn_off_all(reclaim_session=True)
                configured = False
            except Exception as error:  # noqa: BLE001 - physical cleanup is best effort.
                print(
                    f"CLEANUP_ERROR phase={phase_name} action=release_session "
                    f"error={error}"
                )
        elif configured:
            try:
                interaction.clear_bindings()
            except Exception as error:  # noqa: BLE001 - physical cleanup is best effort.
                print(
                    f"CLEANUP_ERROR phase={phase_name} action=clear_bindings "
                    f"error={error}"
                )
        producer.close()
    passed = deactivated and (seen == set(controls) if require_all else bool(seen))
    print(
        f"PHASE_END name={phase_name} seen={len(seen)} required="
        f"{'all' if require_all else 'one'} result={'PASS' if passed else 'FAIL'}",
        flush=True,
    )
    return passed


def run_mode(args: argparse.Namespace, mode: SharedHidMode) -> bool:
    profile = load_profile(args.profile)
    toggle_control = ControlId.key(
        *profile.device_profile.require_interaction().toggle_matrix
    )
    passed = True
    routing = Routing.CAPTURE if args.routing == "capture" else Routing.MIRROR
    with SharedRawHidSession.open_profile(
        profile.device_profile,
        device_index=args.device_index,
        mode=mode,
    ) as session:
        adapter = KeychronEffect25Adapter(
            session.rgb_transport(),
            session.device_info,
            profile=profile.device_profile,
            effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
        )
        protocol = HostInteractionProtocolClient(
            session.interaction_transport(), profile=profile.device_profile
        )
        with RgbController(adapter, profile) as rgb, protocol:
            interaction = HostInteractionController(protocol)
            producer = ValidationProducer(
                rgb,
                brightness=args.brightness,
                pause_fps=args.pause_fps,
            )
            coordinator = SharedKeyboardCoordinator(rgb, protocol, producer)
            initialize_coordinator = True
            for index, region_id in enumerate(profile.regions):
                controls = region_controls(profile, region_id)
                excluded_pause = toggle_control in {
                    control_for_element(profile.element_by_id[element_id])
                    for element_id in profile.regions[region_id].elements
                }
                print(
                    f"REGION mode={mode.value} name={region_id} "
                    f"bindable={len(controls)} reserved_pause_excluded={excluded_pause}",
                    flush=True,
                )
                phase_passed = run_phase(
                    interaction=interaction,
                    protocol=protocol,
                    coordinator=coordinator,
                    producer=producer,
                    initialize_coordinator=initialize_coordinator,
                    profile=profile,
                    phase_name=region_id,
                    controls=controls,
                    scenes=[
                        static_region_scene(
                            profile, region_id, PALETTE[index % len(PALETTE)]
                        )
                    ],
                    seconds=args.seconds_per_region,
                    require_all=args.require_all_controls,
                    routing=routing,
                    activation_timeout=args.activation_timeout,
                )
                passed &= phase_passed
                initialize_coordinator = False
                if not phase_passed:
                    return False

            all_controls = tuple(
                dict.fromkeys(
                    control
                    for region_id in profile.regions
                    for control in region_controls(profile, region_id)
                )
            )
            passed &= run_phase(
                interaction=interaction,
                protocol=protocol,
                coordinator=coordinator,
                producer=producer,
                initialize_coordinator=initialize_coordinator,
                profile=profile,
                phase_name="all_regions_combined",
                controls=all_controls,
                scenes=combined_scenes(profile),
                seconds=args.combined_seconds,
                require_all=args.require_all_controls,
                routing=routing,
                activation_timeout=args.activation_timeout,
            )
    return passed


def main() -> int:
    args = parse_args()
    if (
        args.seconds_per_region < 0
        or args.combined_seconds < 0
        or args.activation_timeout < 0
        or args.pause_fps <= 0
    ):
        print("ERROR: timeouts must be nonnegative and --pause-fps must be positive.")
        return 1
    if not 0 <= args.brightness <= 255:
        print("ERROR: --brightness must be in 0...255.")
        return 1
    if not args.yes:
        confirmation = input(
            f"Type SHARED to install inactive {args.routing.upper()} bindings; "
            "double Pause will enter and exit each phase: "
        )
        if confirmation != "SHARED":
            print("ERROR: confirmation did not match.")
            return 1
    modes = tuple(SharedHidMode) if args.mode == "both" else (SharedHidMode(args.mode),)
    try:
        passed = all(run_mode(args, mode) for mode in modes)
    except KeyboardInterrupt:
        print("Interrupted; release and RGB restoration were requested.")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"SHARED_REGION_INTERACTION_RESULT={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
