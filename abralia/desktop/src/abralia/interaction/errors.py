# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Expected failures from the Abralia Host Interaction desktop API."""


class HostInteractionError(RuntimeError):
    """Base class for expected Host Interaction failures."""


class ProtocolError(HostInteractionError):
    """A packet, field, state transition, or firmware response is invalid."""


class FirmwareRejectedError(ProtocolError):
    """Firmware returned a non-OK result while preserving its full response."""

    def __init__(self, message: str, response: object):
        super().__init__(message)
        self.response = response


class TransportError(HostInteractionError):
    """Raw HID discovery or I/O failed."""


class DeviceNotFoundError(TransportError):
    """No compatible Host Interaction Raw HID interface was found."""


class AmbiguousDeviceError(TransportError):
    """More than one compatible Raw HID interface matched."""


class SessionError(HostInteractionError):
    """An operation requires a live claimed Host Interaction session."""


class KeycodeLookupError(HostInteractionError):
    """A keycode is malformed or has no live VIA keymap match."""
