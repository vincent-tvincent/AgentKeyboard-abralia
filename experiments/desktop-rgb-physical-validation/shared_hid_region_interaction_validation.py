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
from abralia.rgb import BLACK, PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from abralia.rgb.adapters.keychron_effect25 import KeychronEffect25Adapter
from abralia.rgb.profiles import EncoderDirection
from shared_hid_rgb_validation import PALETTE, combined_scenes, static_region_scene

from abralia import SharedHidMode, SharedRawHidSession

PAUSE_CONTROL = ControlId.key(0, 16)
INTERACTION_POLL_MS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("cooperative", "threaded", "both"),
        default="cooperative",
    )
    parser.add_argument(
        "--seconds-per-region",
        type=float,
        default=0.0,
        help="maximum active seconds per region; 0 waits for double-Pause",
    )
    parser.add_argument(
        "--combined-seconds",
        type=float,
        default=0.0,
        help="maximum active seconds for the combined phase; 0 waits for double-Pause",
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
    controls: list[ControlId] = []
    for element_id in profile.regions[region_id].elements:
        control = control_for_element(profile.element_by_id[element_id])
        if control is None or control == PAUSE_CONTROL or control in controls:
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


def pause_breathing_scene(elapsed: float):
    phase = elapsed / 2.0 * math.tau
    breath = (math.sin(phase - math.pi / 2) + 1.0) / 2.0
    value = round(16 + 239 * breath * breath)
    return PhysicalSceneBuilder().build(
        "unactivated-pause-breathing",
        {"PAUSE": Srgb8(value // 5, value // 2, value)},
        background=BLACK,
        owner="shared-hid-region-interaction-validation",
    )


def wait_for_manual_activation(
    *,
    rgb: RgbController,
    protocol: HostInteractionProtocolClient,
    timeout: float,
    fps: float,
    brightness: int,
    phase_name: str,
) -> bool:
    print(
        f"WAITING_ACTIVATION phase={phase_name} scene=pause_breathing "
        "gesture=double_pause",
        flush=True,
    )
    started = time.monotonic()
    next_frame = started
    last_lease = None
    while timeout == 0 or time.monotonic() - started < timeout:
        elapsed = time.monotonic() - started
        last_lease = rgb.display(
            [pause_breathing_scene(elapsed)],
            brightness_ceiling=brightness,
        )
        for event in protocol.service(timeout_ms=INTERACTION_POLL_MS):
            if event.event_type is EventType.MODE_CHANGED and event.mode_active:
                if last_lease is not None:
                    last_lease.close()
                print(f"MANUAL_MODE phase={phase_name} active=true", flush=True)
                return True
        next_frame += 1.0 / fps
        delay = next_frame - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    if last_lease is not None:
        last_lease.close()
    print(f"ACTIVATION_TIMEOUT phase={phase_name}", flush=True)
    return False


def run_phase(
    *,
    rgb: RgbController,
    interaction: HostInteractionController,
    protocol: HostInteractionProtocolClient,
    profile,
    phase_name: str,
    controls: tuple[ControlId, ...],
    scenes: list,
    seconds: float,
    brightness: int,
    require_all: bool,
    routing: Routing,
    activation_timeout: float,
    pause_fps: float,
) -> bool:
    bindings, labels = bindings_for_controls(profile, controls, routing)
    lease = None
    seen: set[ControlId] = set()
    configured = False
    manual_active = False
    deactivated = False
    try:
        interaction.replace_bindings(bindings)
        configured = True
        manual_active = wait_for_manual_activation(
            rgb=rgb,
            protocol=protocol,
            timeout=activation_timeout,
            fps=pause_fps,
            brightness=brightness,
            phase_name=phase_name,
        )
        if not manual_active:
            return False
        lease = rgb.display(scenes, brightness_ceiling=brightness)
        print(
            f"CAPTURE_DISPLAY phase={phase_name} scene=region controls={len(controls)} "
            f"routing={routing.name}",
            flush=True,
        )
        deadline = time.monotonic() + seconds if seconds else None
        rgb_refresh_at = time.monotonic() + 1.0
        while deadline is None or time.monotonic() < deadline:
            now = time.monotonic()
            if now >= rgb_refresh_at:
                lease.refresh()
                rgb_refresh_at = now + 1.0
            for event in protocol.service(timeout_ms=INTERACTION_POLL_MS):
                if event.event_type is EventType.MODE_CHANGED:
                    if not event.mode_active:
                        manual_active = False
                        deactivated = True
                        print(
                            f"MANUAL_MODE phase={phase_name} active=false",
                            flush=True,
                        )
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
            if deactivated:
                break
            time.sleep(0.02)
        if not deactivated:
            print(
                f"DEACTIVATION_TIMEOUT phase={phase_name} gesture=double_pause",
                flush=True,
            )
            return False
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
        if lease is not None:
            lease.close()
    passed = deactivated and (seen == set(controls) if require_all else bool(seen))
    print(
        f"PHASE_END name={phase_name} seen={len(seen)} required="
        f"{'all' if require_all else 'one'} result={'PASS' if passed else 'FAIL'}",
        flush=True,
    )
    return passed


def run_mode(args: argparse.Namespace, mode: SharedHidMode) -> bool:
    profile = load_profile()
    passed = True
    routing = Routing.CAPTURE if args.routing == "capture" else Routing.MIRROR
    with SharedRawHidSession.open_keychron_v3_8k(
        device_index=args.device_index,
        mode=mode,
    ) as session:
        adapter = KeychronEffect25Adapter(session.rgb_transport(), session.device_info)
        protocol = HostInteractionProtocolClient(session.interaction_transport())
        with RgbController(adapter, profile) as rgb, protocol:
            interaction = HostInteractionController(protocol)
            for index, region_id in enumerate(profile.regions):
                controls = region_controls(profile, region_id)
                excluded_pause = PAUSE_CONTROL in {
                    control_for_element(profile.element_by_id[element_id])
                    for element_id in profile.regions[region_id].elements
                }
                print(
                    f"REGION mode={mode.value} name={region_id} "
                    f"bindable={len(controls)} reserved_pause_excluded={excluded_pause}",
                    flush=True,
                )
                passed &= run_phase(
                    rgb=rgb,
                    interaction=interaction,
                    protocol=protocol,
                    profile=profile,
                    phase_name=region_id,
                    controls=controls,
                    scenes=[
                        static_region_scene(
                            profile, region_id, PALETTE[index % len(PALETTE)]
                        )
                    ],
                    seconds=args.seconds_per_region,
                    brightness=args.brightness,
                    require_all=args.require_all_controls,
                    routing=routing,
                    activation_timeout=args.activation_timeout,
                    pause_fps=args.pause_fps,
                )

            all_controls = tuple(
                dict.fromkeys(
                    control
                    for region_id in profile.regions
                    for control in region_controls(profile, region_id)
                )
            )
            passed &= run_phase(
                rgb=rgb,
                interaction=interaction,
                protocol=protocol,
                profile=profile,
                phase_name="all_regions_combined",
                controls=all_controls,
                scenes=combined_scenes(profile),
                seconds=args.combined_seconds,
                brightness=args.brightness,
                require_all=args.require_all_controls,
                routing=routing,
                activation_timeout=args.activation_timeout,
                pause_fps=args.pause_fps,
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
