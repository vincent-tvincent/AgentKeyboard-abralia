# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from abralia.rgb import EncoderDirection, LiveKeycodeResolver, load_profile
from abralia.rgb.key_lookup import LiveKeymapAddressSpace, UnrenderableReason
from abralia.rgb.profiles import EncoderPosition


class FakeLiveKeymapReader:
    def __init__(self) -> None:
        self.matrix: dict[tuple[int, int, int], int] = {}
        self.encoders: dict[tuple[int, EncoderPosition], int] = {}

    def keymap_layer_count(self) -> int:
        return 4

    def keymap_address_space(self) -> LiveKeymapAddressSpace:
        return LiveKeymapAddressSpace(6, 17, 1)

    def read_matrix_keycodes(
        self,
        layers: tuple[int, ...],
        *,
        rows: int,
        columns: int,
    ) -> dict[tuple[int, int, int], int]:
        return {
            (layer, row, column): self.matrix.get((layer, row, column), 0)
            for layer in layers
            for row in range(rows)
            for column in range(columns)
        }

    def read_encoder_keycodes(
        self,
        layers: tuple[int, ...],
        positions: tuple[EncoderPosition, ...],
    ) -> dict[tuple[int, EncoderPosition], int]:
        return {
            (layer, position): self.encoders.get((layer, position), 0)
            for layer in layers
            for position in positions
        }


class LiveKeycodeResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        self.reader = FakeLiveKeymapReader()
        self.resolver = LiveKeycodeResolver()

    def test_all_controls_match_and_identical_positions_are_deduplicated(self) -> None:
        self.reader.matrix[(0, 2, 2)] = 0x0004
        self.reader.matrix[(1, 2, 2)] = 0x0004
        self.reader.matrix[(2, 3, 1)] = 0x0004

        resolution = self.resolver.resolve(
            self.reader,
            self.profile,
            0x0004,
            [0, 1, 1, 2],
        )

        self.assertEqual(resolution.queried_layers, (0, 1, 2))
        self.assertEqual(resolution.physical_element_ids, ("W", "A"))
        self.assertEqual(resolution.matches[0].layers, (0, 1))
        self.assertFalse(hasattr(resolution.matches[0], "binding_id"))

    def test_encoder_mappings_resolve_to_stable_physical_control_ids(self) -> None:
        clockwise = EncoderPosition(0, EncoderDirection.CLOCKWISE)
        self.reader.encoders[(0, clockwise)] = 0x0080
        self.reader.encoders[(2, clockwise)] = 0x0080

        resolution = self.resolver.resolve(
            self.reader,
            self.profile,
            0x0080,
            [0, 2],
        )

        self.assertEqual(resolution.physical_element_ids, ("KNOB_CLOCKWISE",))
        self.assertEqual(resolution.matches[0].layers, (0, 2))
        self.assertEqual(resolution.unsupported_rgb_element_ids, ("KNOB_CLOCKWISE",))

    def test_live_match_absent_from_profile_is_surfaced_as_unrenderable(self) -> None:
        # Matrix 3,12 exists in the firmware address space but has no physical
        # element in the ANSI layout profile.
        self.reader.matrix[(0, 3, 12)] = 0x1234
        self.reader.matrix[(2, 3, 12)] = 0x1234

        resolution = self.resolver.resolve(
            self.reader,
            self.profile,
            0x1234,
            [0, 2],
        )

        self.assertEqual(len(resolution.matches), 1)
        match = resolution.matches[0]
        self.assertEqual(match.address.matrix, (3, 12))
        self.assertEqual(match.layers, (0, 2))
        self.assertIsNone(match.element)
        self.assertEqual(match.unrenderable_reason, UnrenderableReason.PROFILE_MISSING)
        self.assertEqual(resolution.unrenderable_control_labels, ("matrix[3,12]",))
        self.assertEqual(resolution.physical_element_ids, ())


if __name__ == "__main__":
    unittest.main()
