# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import unittest
from importlib import resources

from abralia.rgb import (
    Canvas,
    Hsv8,
    MappingStrategy,
    PhysicalSceneBuilder,
    RectangularSceneBuilder,
    Srgb8,
    load_profile,
)
from abralia.rgb.colors import ColorValidationError, parse_color, weighted_average
from abralia.rgb.compatibility import AdapterCapabilities, SceneCompiler
from abralia.rgb.composer import PriorityOverlayComposer
from abralia.rgb.errors import CapabilityError
from abralia.rgb.led_mapper import PhysicalElementLedMapper
from abralia.rgb.profiles import validate_profile

CAPABILITIES = AdapterCapabilities(True, True, True, True, 2.0)


class ProfileTests(unittest.TestCase):
    def test_bundled_v3_profile_has_exact_physical_and_led_coverage(self) -> None:
        profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")

        self.assertEqual(len(profile.elements), 90)
        self.assertEqual(len(profile.rgb_elements), 87)
        self.assertEqual(profile.element_by_id["SPACE"].led_address, 79)
        self.assertIsNone(profile.element_by_id["KNOB_PRESS"].led_address)
        self.assertEqual(profile.element_by_id["W"].matrix, (2, 2))
        self.assertEqual(profile.element_by_id["W"].led_address, 35)
        self.assertEqual(profile.element_by_id["KNOB_CLOCKWISE"].encoder.index, 0)
        self.assertNotIn("LEFT_OPTION", profile.element_by_id)
        self.assertEqual(profile.resolve_element_id("LEFT_OPTION"), "LEFT_MODIFIER_2")
        self.assertEqual(profile.resolve_element_id(" left_option "), "LEFT_MODIFIER_2")
        self.assertEqual(
            sorted(element.led_address for element in profile.rgb_elements),
            list(range(87)),
        )

    def test_profile_semantics_reject_unknown_alias_target(self) -> None:
        data = json.loads(
            resources.files("abralia")
            .joinpath("resources/profiles/keychron-v3-8k-ansi-encoder-effect25.json")
            .read_text(encoding="utf-8")
        )
        data["aliases"]["BROKEN"] = "DOES_NOT_EXIST"

        with self.assertRaisesRegex(Exception, "Aliases reference unknown"):
            validate_profile(data)


class ColorTests(unittest.TestCase):
    def test_tagged_rgb_and_hsv_are_both_public_inputs(self) -> None:
        self.assertEqual(
            parse_color({"space": "srgb", "red": 1, "green": 2, "blue": 3}),
            Srgb8(1, 2, 3),
        )
        self.assertEqual(
            parse_color({"space": "hsv", "hue": 4, "saturation": 5, "value": 6}),
            Hsv8(4, 5, 6),
        )

    def test_tagged_color_does_not_coerce_strings_or_booleans(self) -> None:
        with self.assertRaises(ColorValidationError):
            parse_color({"space": "srgb", "red": "1", "green": 2, "blue": 3})
        with self.assertRaises(ColorValidationError):
            parse_color({"space": "srgb", "red": True, "green": 2, "blue": 3})

    def test_averaging_occurs_in_linear_rgb(self) -> None:
        color = weighted_average([(Srgb8(255, 0, 0), 1), (Srgb8(0, 255, 0), 1)])
        self.assertEqual(color, Srgb8(188, 188, 0))


class ScenePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        self.compiler = SceneCompiler()

    def test_physical_scene_becomes_a_complete_ordered_device_frame(self) -> None:
        scene = PhysicalSceneBuilder().build(
            "physical",
            {"W": Srgb8(255, 0, 0)},
            background=Srgb8(0, 0, 0),
            owner="test",
        )
        resolved, report = self.compiler.compile(scene, self.profile, CAPABILITIES)
        physical, device = PhysicalElementLedMapper().map(resolved, self.profile)

        self.assertEqual(report.uncovered_cells, ())
        self.assertEqual(len(physical.colors), 87)
        self.assertEqual([item.address for item in device.leds], list(range(87)))
        self.assertEqual(physical.colors["W"], Srgb8(255, 0, 0))

    def test_physical_scene_rejects_control_without_rgb(self) -> None:
        scene = PhysicalSceneBuilder().build(
            "encoder",
            {"KNOB_CLOCKWISE": Srgb8(255, 0, 0)},
            owner="test",
        )

        with self.assertRaisesRegex(CapabilityError, "KNOB_CLOCKWISE"):
            self.compiler.compile(scene, self.profile, CAPABILITIES)

    def test_geometry_mapping_uses_linear_blend_and_space_led_point(self) -> None:
        cells = [Srgb8(0, 0, 0)] * (15 * 5)
        cells[13] = Srgb8(255, 0, 0)
        cells[14] = Srgb8(0, 255, 0)
        cells[4 * 15 + 6] = Srgb8(0, 0, 255)
        scene = RectangularSceneBuilder().build(
            "geometry",
            Canvas(15, 5, tuple(cells)),
            target="alphanumeric_block",
            strategy=MappingStrategy.GEOMETRY_RESAMPLE,
            owner="test",
        )

        resolved, report = self.compiler.compile(scene, self.profile, CAPABILITIES)
        colors = {visual.element_id: visual.color for visual in resolved.visuals}

        self.assertEqual(colors["BACKSPACE"], Srgb8(188, 188, 0))
        self.assertEqual(colors["SPACE"], Srgb8(0, 0, 255))
        self.assertIn(("SPACE", 4, 6), report.large_key_selections)

    def test_row_index_and_anchored_mapping_are_explicit(self) -> None:
        row_scene = RectangularSceneBuilder().build(
            "rows",
            Canvas(3, 2, tuple(Srgb8(index * 20, 0, 0) for index in range(6))),
            target="navigation_cluster",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="test",
        )
        anchored_scene = RectangularSceneBuilder().build(
            "anchored",
            Canvas(3, 2, tuple(Srgb8(0, index * 20, 0) for index in range(6))),
            target="navigation_cluster",
            strategy=MappingStrategy.ANCHORED_ROW_GRID,
            owner="test",
        )

        row_resolved, _ = self.compiler.compile(row_scene, self.profile, CAPABILITIES)
        anchored_resolved, _ = self.compiler.compile(
            anchored_scene, self.profile, CAPABILITIES
        )
        row_colors = {
            visual.element_id: visual.color for visual in row_resolved.visuals
        }
        anchored_colors = {
            visual.element_id: visual.color for visual in anchored_resolved.visuals
        }

        self.assertEqual(row_colors["PAGE_DOWN"], Srgb8(100, 0, 0))
        self.assertEqual(anchored_colors["PAGE_DOWN"], Srgb8(0, 100, 0))

    def test_composer_overlays_target_region_without_blacking_other_keys(self) -> None:
        base = PhysicalSceneBuilder().build(
            "base",
            {},
            background=Srgb8(1, 2, 3),
            owner="base",
            priority=0,
        )
        overlay = RectangularSceneBuilder().build(
            "overlay",
            Canvas(3, 2, (Srgb8(255, 0, 0),) * 6),
            target="navigation_cluster",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="overlay",
            priority=10,
        )
        compiled = [
            self.compiler.compile(scene, self.profile, CAPABILITIES)[0]
            for scene in (base, overlay)
        ]

        composed = PriorityOverlayComposer().compose(compiled)
        colors = {visual.element_id: visual.color for visual in composed.visuals}

        self.assertEqual(colors["W"], Srgb8(1, 2, 3))
        self.assertEqual(colors["HOME"], Srgb8(255, 0, 0))


if __name__ == "__main__":
    unittest.main()
