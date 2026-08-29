# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Device Adapter Interface from the RGB architecture diagram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..compatibility import AdapterCapabilities
from ..led_mapper import DeviceFrame
from ..transport import HidDeviceInfo


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Adapter-owned restoration payload behind a transport-neutral wrapper."""

    adapter_id: str
    payload: object


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    connected: bool
    detail: str


class RgbDeviceAdapter(Protocol):
    adapter_id: str
    adapter_version: int

    @classmethod
    def discover(cls) -> list[HidDeviceInfo]: ...

    def capabilities(self) -> AdapterCapabilities: ...
    def snapshot(self) -> DeviceSnapshot: ...
    def submit_frame(self, frame: DeviceFrame, *, brightness_ceiling: int) -> int: ...
    def refresh(self) -> int: ...
    def clear(self) -> None: ...
    def health(self) -> AdapterHealth: ...
    def restore(self, snapshot: DeviceSnapshot) -> None: ...
    def restore_preserving_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot: ...
    def rebase_current_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot: ...
    def close(self) -> None: ...
