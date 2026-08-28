# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Public error hierarchy for the Abralia RGB host API."""


class RgbError(RuntimeError):
    """Base class for expected RGB API failures."""


class ColorValidationError(RgbError):
    """A public color value is malformed."""


class ProfileValidationError(RgbError):
    """A keyboard profile violates the schema or semantic invariants."""


class MappingError(RgbError):
    """A scene cannot be mapped to the selected profile target."""


class CapabilityError(RgbError):
    """The adapter or device lacks a required capability."""


class DeviceNotFoundError(RgbError):
    """No matching device was found."""


class AmbiguousDeviceError(RgbError):
    """More than one device matched an operation requiring one target."""


class TransportError(RgbError):
    """Raw HID transport failed or returned malformed data."""


class RestoreError(RgbError):
    """A device snapshot could not be restored exactly."""
