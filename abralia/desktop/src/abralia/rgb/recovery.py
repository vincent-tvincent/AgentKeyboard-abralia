# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""State and Recovery Manager from the RGB architecture diagram."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .adapters.base import DeviceSnapshot, RgbDeviceAdapter
from .errors import RestoreError


@dataclass(slots=True)
class StateRecoveryManager:
    adapter: RgbDeviceAdapter
    cleanup_callbacks: tuple[Callable[[], None], ...] = ()
    snapshot: DeviceSnapshot | None = None
    restored: bool = False

    def capture(self) -> DeviceSnapshot:
        self.snapshot = self.adapter.snapshot()
        self.restored = False
        return self.snapshot

    def restore(self) -> None:
        if self.restored:
            return
        try:
            for cleanup in self.cleanup_callbacks:
                cleanup()
            if self.snapshot is not None:
                self.adapter.restore(self.snapshot)
        except Exception as error:
            raise RestoreError("Failed to restore the device RGB snapshot.") from error
        self.restored = True

    def health(self) -> bool:
        return self.adapter.health().connected
