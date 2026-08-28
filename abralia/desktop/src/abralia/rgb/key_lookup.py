# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Live keycode lookup followed by an explicit profile-to-RGB join."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .errors import MappingError, TransportError
from .profiles import (
    EncoderDirection,
    EncoderPosition,
    LayoutProfile,
    PhysicalElement,
)

MatrixKey = tuple[int, int, int]
EncoderKey = tuple[int, EncoderPosition]


@dataclass(frozen=True, slots=True)
class LiveKeymapAddressSpace:
    """Every raw control address exposed by one firmware/adapter pair."""

    matrix_rows: int
    matrix_columns: int
    encoder_count: int

    def __post_init__(self) -> None:
        if self.matrix_rows <= 0 or self.matrix_columns <= 0:
            raise ValueError("Live keymap matrix dimensions must be positive.")
        if self.encoder_count < 0:
            raise ValueError("Live keymap encoder count cannot be negative.")


@dataclass(frozen=True, slots=True)
class RawControlAddress:
    """A matrix position or encoder direction, independent of any profile."""

    matrix: tuple[int, int] | None = None
    encoder: EncoderPosition | None = None

    def __post_init__(self) -> None:
        if (self.matrix is None) == (self.encoder is None):
            raise ValueError("A raw control address must contain exactly one position.")

    @classmethod
    def matrix_position(cls, row: int, column: int) -> RawControlAddress:
        return cls(matrix=(row, column))

    @classmethod
    def encoder_position(cls, position: EncoderPosition) -> RawControlAddress:
        return cls(encoder=position)

    @property
    def label(self) -> str:
        if self.matrix is not None:
            return f"matrix[{self.matrix[0]},{self.matrix[1]}]"
        assert self.encoder is not None
        return f"encoder[{self.encoder.index},{self.encoder.direction.value}]"


@runtime_checkable
class LiveKeymapReader(Protocol):
    """Read every live firmware control address without a layout profile."""

    def keymap_layer_count(self) -> int: ...

    def keymap_address_space(self) -> LiveKeymapAddressSpace: ...

    def read_matrix_keycodes(
        self,
        layers: tuple[int, ...],
        *,
        rows: int,
        columns: int,
    ) -> Mapping[MatrixKey, int]: ...

    def read_encoder_keycodes(
        self,
        layers: tuple[int, ...],
        positions: tuple[EncoderPosition, ...],
    ) -> Mapping[EncoderKey, int]: ...


class UnrenderableReason(str, Enum):
    PROFILE_MISSING = "profile_missing"
    NO_RGB_ADDRESS = "no_rgb_address"


@dataclass(frozen=True, slots=True)
class KeycodeMatch:
    """One deduplicated raw match, optionally joined to a profile element."""

    address: RawControlAddress
    layers: tuple[int, ...]
    element: PhysicalElement | None

    @property
    def unrenderable_reason(self) -> UnrenderableReason | None:
        if self.element is None:
            return UnrenderableReason.PROFILE_MISSING
        if not self.element.rgb_capable:
            return UnrenderableReason.NO_RGB_ADDRESS
        return None

    @property
    def display_label(self) -> str:
        return (
            self.element.element_id if self.element is not None else self.address.label
        )


@dataclass(frozen=True, slots=True)
class KeycodeResolution:
    keycode: int
    queried_layers: tuple[int, ...]
    matches: tuple[KeycodeMatch, ...]

    @property
    def raw_control_addresses(self) -> tuple[RawControlAddress, ...]:
        return tuple(match.address for match in self.matches)

    @property
    def physical_element_ids(self) -> tuple[str, ...]:
        """Every raw match that joined to a profile element, RGB or otherwise."""

        return tuple(
            match.element.element_id
            for match in self.matches
            if match.element is not None
        )

    @property
    def renderable_element_ids(self) -> tuple[str, ...]:
        return tuple(
            match.element.element_id
            for match in self.matches
            if match.element is not None and match.element.rgb_capable
        )

    @property
    def unrenderable_matches(self) -> tuple[KeycodeMatch, ...]:
        return tuple(
            match for match in self.matches if match.unrenderable_reason is not None
        )

    @property
    def unrenderable_control_labels(self) -> tuple[str, ...]:
        return tuple(match.display_label for match in self.unrenderable_matches)

    @property
    def unsupported_rgb_element_ids(self) -> tuple[str, ...]:
        """Compatibility alias that now also includes raw profile-missing labels."""

        return self.unrenderable_control_labels


class LiveKeycodeResolver:
    """Resolve raw firmware matches first, then join them to RGB profile data."""

    def resolve(
        self,
        reader: LiveKeymapReader,
        profile: LayoutProfile,
        keycode: int,
        layers: Sequence[int],
    ) -> KeycodeResolution:
        if (
            isinstance(keycode, bool)
            or not isinstance(keycode, int)
            or not 0 <= keycode <= 0xFFFF
        ):
            raise MappingError("QMK/VIA keycode must be an integer in 0x0000...0xFFFF.")
        queried_layers = tuple(dict.fromkeys(layers))
        if not queried_layers:
            raise MappingError("At least one keymap layer must be queried.")
        layer_count = reader.keymap_layer_count()
        if any(
            isinstance(layer, bool)
            or not isinstance(layer, int)
            or not 0 <= layer < layer_count
            for layer in queried_layers
        ):
            raise MappingError(
                f"Queried layers must be integers in 0...{layer_count - 1}."
            )

        address_space = reader.keymap_address_space()
        matrix_values = reader.read_matrix_keycodes(
            queried_layers,
            rows=address_space.matrix_rows,
            columns=address_space.matrix_columns,
        )
        encoder_positions = tuple(
            EncoderPosition(index, direction)
            for index in range(address_space.encoder_count)
            for direction in (
                EncoderDirection.COUNTERCLOCKWISE,
                EncoderDirection.CLOCKWISE,
            )
        )
        encoder_values = reader.read_encoder_keycodes(queried_layers, encoder_positions)

        # Phase 1: enumerate every raw address and deduplicate matches across layers.
        raw_layers: dict[RawControlAddress, list[int]] = {}
        for row in range(address_space.matrix_rows):
            for column in range(address_space.matrix_columns):
                address = RawControlAddress.matrix_position(row, column)
                for layer in queried_layers:
                    lookup_key = (layer, row, column)
                    try:
                        configured = matrix_values[lookup_key]
                    except KeyError as error:
                        raise TransportError(
                            f"Live keymap omitted matrix position {lookup_key!r}."
                        ) from error
                    if configured == keycode:
                        raw_layers.setdefault(address, []).append(layer)

        for position in encoder_positions:
            address = RawControlAddress.encoder_position(position)
            for layer in queried_layers:
                lookup_key = (layer, position)
                try:
                    configured = encoder_values[lookup_key]
                except KeyError as error:
                    raise TransportError(
                        f"Live keymap omitted encoder position {lookup_key!r}."
                    ) from error
                if configured == keycode:
                    raw_layers.setdefault(address, []).append(layer)

        if not raw_layers:
            layer_text = ", ".join(str(layer) for layer in queried_layers)
            raise MappingError(
                f"UNBOUND keycode 0x{keycode:04X} on queried layer(s) {layer_text}."
            )

        # Phase 2: join raw controls to the optional RGB profile mapping.
        profile_by_address = {
            RawControlAddress.matrix_position(*element.matrix): element
            for element in profile.elements
            if element.matrix is not None
        }
        profile_by_address.update(
            {
                RawControlAddress.encoder_position(element.encoder): element
                for element in profile.elements
                if element.encoder is not None
            }
        )
        matches = tuple(
            KeycodeMatch(
                address, tuple(matched_layers), profile_by_address.get(address)
            )
            for address, matched_layers in raw_layers.items()
        )
        return KeycodeResolution(keycode, queried_layers, matches)
