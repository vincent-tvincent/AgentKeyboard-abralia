# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Abralia desktop-side APIs."""

from .coordinator import (
    CoordinatorTransition,
    RgbProducerLifecycle,
    SharedKeyboardCoordinator,
    SharedKeyboardState,
)
from .device_profile import DeviceProfile, DeviceProfileError, load_device_profile
from .shared_hid import (
    SharedHidError,
    SharedHidMode,
    SharedInteractionTransportView,
    SharedRawHidSession,
    SharedRgbTransportView,
)

__all__ = [
    "CoordinatorTransition",
    "DeviceProfile",
    "DeviceProfileError",
    "RgbProducerLifecycle",
    "SharedHidError",
    "SharedHidMode",
    "SharedInteractionTransportView",
    "SharedKeyboardCoordinator",
    "SharedKeyboardState",
    "SharedRawHidSession",
    "SharedRgbTransportView",
    "load_device_profile",
]
