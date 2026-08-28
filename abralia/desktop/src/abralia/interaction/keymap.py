# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Layout-agnostic live VIA keycode-to-ControlId resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .errors import KeycodeLookupError, ProtocolError
from .protocol import Capabilities, ControlId
from .transport import InteractionTransport


VIA_GET_KEYCODE = 0x04
VIA_GET_LAYER_COUNT = 0x11
VIA_GET_KEYMAP_BUFFER = 0x12
VIA_GET_ENCODER = 0x14


@dataclass(frozen=True, slots=True)
class KeycodeMatch:
    keycode: int
    layer: int
    control_id: ControlId


_NAMED_BASIC_KEYCODES = {
    "KC_NO": 0x0000,
    "KC_TRANSPARENT": 0x0001,
    "KC_TRNS": 0x0001,
    "KC_ENTER": 0x0028,
    "KC_ENT": 0x0028,
    "KC_ESCAPE": 0x0029,
    "KC_ESC": 0x0029,
    "KC_BACKSPACE": 0x002A,
    "KC_BSPC": 0x002A,
    "KC_TAB": 0x002B,
    "KC_SPACE": 0x002C,
    "KC_SPC": 0x002C,
    "KC_MINUS": 0x002D,
    "KC_MINS": 0x002D,
    "KC_EQUAL": 0x002E,
    "KC_EQL": 0x002E,
    "KC_LEFT_BRACKET": 0x002F,
    "KC_LBRC": 0x002F,
    "KC_RIGHT_BRACKET": 0x0030,
    "KC_RBRC": 0x0030,
    "KC_BACKSLASH": 0x0031,
    "KC_BSLS": 0x0031,
    "KC_SEMICOLON": 0x0033,
    "KC_SCLN": 0x0033,
    "KC_QUOTE": 0x0034,
    "KC_QUOT": 0x0034,
    "KC_GRAVE": 0x0035,
    "KC_GRV": 0x0035,
    "KC_COMMA": 0x0036,
    "KC_COMM": 0x0036,
    "KC_DOT": 0x0037,
    "KC_PERIOD": 0x0037,
    "KC_SLASH": 0x0038,
    "KC_SLSH": 0x0038,
    "KC_CAPS_LOCK": 0x0039,
    "KC_CAPS": 0x0039,
    "KC_PRINT_SCREEN": 0x0046,
    "KC_PSCR": 0x0046,
    "KC_SCROLL_LOCK": 0x0047,
    "KC_SLCK": 0x0047,
    "KC_PAUSE": 0x0048,
    "KC_PAUS": 0x0048,
    "KC_INSERT": 0x0049,
    "KC_INS": 0x0049,
    "KC_HOME": 0x004A,
    "KC_PAGE_UP": 0x004B,
    "KC_PGUP": 0x004B,
    "KC_DELETE": 0x004C,
    "KC_DEL": 0x004C,
    "KC_END": 0x004D,
    "KC_PAGE_DOWN": 0x004E,
    "KC_PGDN": 0x004E,
    "KC_RIGHT": 0x004F,
    "KC_RGHT": 0x004F,
    "KC_LEFT": 0x0050,
    "KC_DOWN": 0x0051,
    "KC_UP": 0x0052,
    "KC_APPLICATION": 0x0065,
    "KC_APP": 0x0065,
    "KC_MUTE": 0x007F,
    "KC_VOLUME_UP": 0x0080,
    "KC_VOLU": 0x0080,
    "KC_VOLUME_DOWN": 0x0081,
    "KC_VOLD": 0x0081,
    "KC_LEFT_CTRL": 0x00E0,
    "KC_LCTL": 0x00E0,
    "KC_LEFT_SHIFT": 0x00E1,
    "KC_LSFT": 0x00E1,
    "KC_LEFT_ALT": 0x00E2,
    "KC_LALT": 0x00E2,
    "KC_LEFT_GUI": 0x00E3,
    "KC_LGUI": 0x00E3,
    "KC_RIGHT_CTRL": 0x00E4,
    "KC_RCTL": 0x00E4,
    "KC_RIGHT_SHIFT": 0x00E5,
    "KC_RSFT": 0x00E5,
    "KC_RIGHT_ALT": 0x00E6,
    "KC_RALT": 0x00E6,
    "KC_RIGHT_GUI": 0x00E7,
    "KC_RGUI": 0x00E7,
}


def parse_keycode(value: int | str) -> int:
    """Parse numeric keycodes plus common QMK basic and layer-key names."""

    if isinstance(value, bool):
        raise KeycodeLookupError("Keycode cannot be boolean.")
    if isinstance(value, int):
        if 0 <= value <= 0xFFFF:
            return value
        raise KeycodeLookupError("Keycode must fit uint16.")

    normalized = value.strip().upper()
    try:
        numeric = int(normalized, 0)
    except ValueError:
        numeric = -1
    if 0 <= numeric <= 0xFFFF:
        return numeric

    if normalized in _NAMED_BASIC_KEYCODES:
        return _NAMED_BASIC_KEYCODES[normalized]
    if re.fullmatch(r"KC_[A-Z]", normalized):
        return 0x0004 + ord(normalized[-1]) - ord("A")
    if re.fullmatch(r"KC_[1-9]", normalized):
        return 0x001E + int(normalized[-1]) - 1
    if normalized == "KC_0":
        return 0x0027
    function_match = re.fullmatch(r"KC_F(\d{1,2})", normalized)
    if function_match:
        number = int(function_match.group(1))
        if 1 <= number <= 12:
            return 0x003A + number - 1
        if 13 <= number <= 24:
            return 0x0068 + number - 13
    layer_match = re.fullmatch(r"(MO|TO|DF|TG|OSL|TT)\((\d{1,2})\)", normalized)
    if layer_match:
        layer = int(layer_match.group(2))
        if not 0 <= layer <= 31:
            raise KeycodeLookupError("Layer keycode layer must be in 0...31.")
        bases = {
            "TO": 0x5200,
            "MO": 0x5220,
            "DF": 0x5240,
            "TG": 0x5260,
            "OSL": 0x5280,
            "TT": 0x52C0,
        }
        return bases[layer_match.group(1)] | layer
    raise KeycodeLookupError(
        f"Unknown symbolic keycode {value!r}; pass its 16-bit VIA/QMK value instead."
    )


class ViaKeymapReader:
    """Read the current volatile/persistent VIA mapping without modifying it."""

    def __init__(self, transport: InteractionTransport):
        self._transport = transport

    def _transact(self, request: Sequence[int], opcode: int) -> bytes:
        return self._transport.transact(
            request,
            lambda report: len(report) == 32 and report[0] == opcode,
        )

    def layer_count(self) -> int:
        response = self._transact([VIA_GET_LAYER_COUNT], VIA_GET_LAYER_COUNT)
        count = response[1]
        if count == 0:
            raise ProtocolError("VIA reported zero dynamic keymap layers.")
        return count

    def read_keymap(
        self, capabilities: Capabilities
    ) -> dict[tuple[int, int, int], int]:
        layers = self.layer_count()
        byte_count = (
            layers
            * capabilities.matrix_rows
            * capabilities.matrix_columns
            * 2
        )
        raw = bytearray()
        for offset in range(0, byte_count, 28):
            size = min(28, byte_count - offset)
            response = self._transact(
                [VIA_GET_KEYMAP_BUFFER, offset >> 8, offset & 0xFF, size],
                VIA_GET_KEYMAP_BUFFER,
            )
            raw.extend(response[4 : 4 + size])

        keymap: dict[tuple[int, int, int], int] = {}
        controls_per_layer = capabilities.matrix_rows * capabilities.matrix_columns
        for byte_offset in range(0, len(raw), 2):
            flat_index = byte_offset // 2
            layer = flat_index // controls_per_layer
            within_layer = flat_index % controls_per_layer
            row = within_layer // capabilities.matrix_columns
            column = within_layer % capabilities.matrix_columns
            keymap[(layer, row, column)] = (raw[byte_offset] << 8) | raw[byte_offset + 1]
        return keymap

    def encoder_keycode(self, layer: int, index: int, *, clockwise: bool) -> int:
        response = self._transact(
            [VIA_GET_ENCODER, layer, index, int(clockwise)], VIA_GET_ENCODER
        )
        return (response[4] << 8) | response[5]

    def resolve(
        self,
        keycode: int | str,
        capabilities: Capabilities,
        *,
        layers: Sequence[int] | None = None,
        include_encoders: bool = True,
    ) -> tuple[KeycodeMatch, ...]:
        wanted = parse_keycode(keycode)
        if wanted == 0:
            raise KeycodeLookupError(
                "KC_NO cannot identify physical controls without layout assumptions."
            )
        keymap = self.read_keymap(capabilities)
        layer_count = max(layer for layer, _row, _column in keymap) + 1
        selected_layers = tuple(range(layer_count)) if layers is None else tuple(layers)
        if any(layer < 0 or layer >= layer_count for layer in selected_layers):
            raise KeycodeLookupError(
                f"Requested layers must be within 0...{layer_count - 1}."
            )

        matches: list[KeycodeMatch] = []
        for layer in selected_layers:
            for row in range(capabilities.matrix_rows):
                for column in range(capabilities.matrix_columns):
                    if keymap[(layer, row, column)] != wanted:
                        continue
                    matches.append(
                        KeycodeMatch(wanted, layer, ControlId.key(row, column))
                    )

        if include_encoders:
            for layer in selected_layers:
                for index in range(capabilities.encoder_count):
                    for clockwise in (True, False):
                        if self.encoder_keycode(
                            layer, index, clockwise=clockwise
                        ) != wanted:
                            continue
                        control_id = (
                            ControlId.encoder_clockwise(index)
                            if clockwise
                            else ControlId.encoder_counterclockwise(index)
                        )
                        matches.append(KeycodeMatch(wanted, layer, control_id))
        return tuple(matches)

    def resolve_controls(
        self,
        keycode: int | str,
        capabilities: Capabilities,
        *,
        layers: Sequence[int] | None = None,
        include_encoders: bool = True,
    ) -> tuple[ControlId, ...]:
        matches = self.resolve(
            keycode,
            capabilities,
            layers=layers,
            include_encoders=include_encoders,
        )
        controls = tuple(sorted({match.control_id for match in matches}))
        if not controls:
            raise KeycodeLookupError(
                f"Keycode {keycode!r} is not assigned to any queried physical control."
            )
        return controls
