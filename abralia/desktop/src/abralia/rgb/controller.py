# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Diagram-aligned orchestration without a daemon or hidden worker thread."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .adapters.base import RgbDeviceAdapter
from .adapters.keychron_effect25 import KeychronEffect25Adapter
from .colors import Color
from .compatibility import MappingReport, SceneCompiler
from .composer import PriorityOverlayComposer
from .errors import AmbiguousDeviceError, CapabilityError, DeviceNotFoundError
from .key_lookup import KeycodeResolution, LiveKeycodeResolver, LiveKeymapReader
from .led_mapper import DeviceFrame, PhysicalElementLedMapper
from .profiles import DEFAULT_PROFILE, LayoutProfile, load_profile
from .recovery import StateRecoveryManager
from .scene import (
    AbstractScene,
    PhysicalFrame,
    PhysicalOverlaySceneBuilder,
    ResolvedScene,
)


@dataclass(slots=True)
class DisplayLease:
    adapter: RgbDeviceAdapter
    active: bool = True

    def refresh(self) -> int:
        if not self.active:
            raise RuntimeError("Display lease is closed.")
        return self.adapter.refresh()

    def close(self) -> None:
        self.active = False


class RgbController:
    def __init__(
        self,
        adapter: RgbDeviceAdapter,
        profile: LayoutProfile,
        *,
        compiler: SceneCompiler | None = None,
        composer: PriorityOverlayComposer | None = None,
        mapper: PhysicalElementLedMapper | None = None,
    ):
        self.adapter = adapter
        self.profile = profile
        self.compiler = compiler or SceneCompiler()
        self.composer = composer or PriorityOverlayComposer()
        self.mapper = mapper or PhysicalElementLedMapper()
        self.keycode_resolver = LiveKeycodeResolver()
        if profile.adapter_id != adapter.adapter_id:
            raise CapabilityError(
                f"Profile requires adapter {profile.adapter_id!r}, not {adapter.adapter_id!r}."
            )
        if adapter.adapter_version < profile.adapter_min_version:
            raise CapabilityError(
                f"Profile requires adapter version {profile.adapter_min_version} or newer."
            )
        self.recovery = StateRecoveryManager(
            adapter,
            cleanup_callbacks=(self.compiler.resolver.overlay.clear,),
        )
        self._open = False

    @classmethod
    def open(
        cls,
        profile_source: str | Path = DEFAULT_PROFILE,
        *,
        device_index: int | None = None,
    ) -> RgbController:
        """Select the v1 adapter and an exactly matching Raw HID interface."""

        profile = load_profile(profile_source)
        if profile.adapter_id != KeychronEffect25Adapter.adapter_id:
            raise CapabilityError(
                f"No installed adapter implements {profile.adapter_id!r}."
            )
        match = profile.device_match
        devices = [
            device
            for device in KeychronEffect25Adapter.discover()
            if device.vendor_id == match.vendor_id
            and device.product_id == match.product_id
            and device.usage_page == match.usage_page
            and device.usage == match.usage
        ]
        if not devices:
            raise DeviceNotFoundError(
                "No Raw HID device matches the selected RGB profile."
            )
        if device_index is None:
            if len(devices) != 1:
                raise AmbiguousDeviceError(
                    "Several matching keyboards were found; select one by device index."
                )
            device = devices[0]
        else:
            if not 0 <= device_index < len(devices):
                raise DeviceNotFoundError(
                    f"Device index {device_index} is outside 0...{len(devices) - 1}."
                )
            device = devices[device_index]
        return cls(KeychronEffect25Adapter.open(device), profile)

    def __enter__(self) -> Self:
        try:
            self.adapter.capabilities()
            self.recovery.capture()
            self._open = True
            return self
        except Exception:
            self.adapter.close()
            raise

    def compile(
        self, scenes: list[AbstractScene]
    ) -> tuple[PhysicalFrame, DeviceFrame, list[MappingReport]]:
        capabilities = self.adapter.capabilities()
        compiled: list[ResolvedScene] = []
        reports: list[MappingReport] = []
        for scene in scenes:
            resolved, report = self.compiler.compile(scene, self.profile, capabilities)
            compiled.append(resolved)
            reports.append(report)
        composed = self.composer.compose(compiled)
        physical, device = self.mapper.map(composed, self.profile)
        return physical, device, reports

    def display(
        self, scenes: list[AbstractScene], *, brightness_ceiling: int = 255
    ) -> DisplayLease:
        if not self._open:
            raise RuntimeError("RgbController must be used as a context manager.")
        _physical, device, _reports = self.compile(scenes)
        self.adapter.submit_frame(device, brightness_ceiling=brightness_ceiling)
        return DisplayLease(self.adapter)

    def resolve_keycode(
        self, keycode: int, *, layers: Sequence[int]
    ) -> KeycodeResolution:
        """Resolve an exact live QMK/VIA keycode to deduplicated physical controls."""

        if not self._open:
            raise RuntimeError("RgbController must be used as a context manager.")
        if not isinstance(self.adapter, LiveKeymapReader):
            raise CapabilityError("The selected adapter cannot read a live keymap.")
        return self.keycode_resolver.resolve(
            self.adapter, self.profile, keycode, layers
        )

    def build_keycode_scene(
        self,
        scene_id: str,
        *,
        keycode: int,
        layers: Sequence[int],
        color: Color | Mapping[str, object],
        owner: str,
        priority: int = 0,
        expires_at: float | None = None,
    ) -> tuple[AbstractScene, KeycodeResolution]:
        """Build a sparse RGB overlay for every live physical keycode match."""

        resolution = self.resolve_keycode(keycode, layers=layers)
        unsupported = resolution.unrenderable_control_labels
        if unsupported:
            raise CapabilityError(
                "Matched raw controls are unrenderable in the selected RGB profile: "
                + ", ".join(unsupported)
            )
        scene = PhysicalOverlaySceneBuilder().build(
            scene_id,
            {element_id: color for element_id in resolution.renderable_element_ids},
            owner=owner,
            priority=priority,
            expires_at=expires_at,
        )
        return scene, resolution

    def restore(self) -> None:
        self.recovery.restore()
        self._open = False

    def close(self) -> None:
        try:
            self.restore()
        finally:
            self.adapter.close()
            self._open = False

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()
