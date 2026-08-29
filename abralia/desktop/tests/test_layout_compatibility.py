# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from abralia.interaction import ControlId
from abralia.layout import CompatibilityLayoutError, load_compatibility_layout
from abralia.rgb import Canvas, MappingStrategy, RectangularSceneBuilder, Srgb8
from abralia.rgb.compatibility import AdapterCapabilities, SceneCompiler
from abralia.rgb.errors import CapabilityError
from abralia.rgb.profiles import load_profile

CAPABILITIES = AdapterCapabilities(True, True, True, True, 2.0)
EXAMPLE = (
    Path(__file__).parents[1] / "examples" / "compatibility" / "f-row-navigation.json"
)


class CompatibilityLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile()

    def write_json(self, directory: str, name: str, data: dict[str, object]) -> Path:
        path = Path(directory) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_imported_aliases_override_region_for_rgb_and_controls(self) -> None:
        layout = load_compatibility_layout(self.profile, EXAMPLE)
        region = layout.region("navigation_cluster")

        self.assertEqual(
            region.controls,
            tuple(ControlId.key(0, column) for column in range(1, 7)),
        )
        self.assertEqual(
            tuple(tuple(control.element_id for control in row) for row in region.rows),
            (("F1", "F2", "F3"), ("F4", "F5", "F6")),
        )
        effective = layout.apply_to_profile(self.profile)
        self.assertEqual(
            effective.regions["navigation_cluster"].rows,
            (("F1", "F2", "F3"), ("F4", "F5", "F6")),
        )
        exported = layout.to_dict()
        self.assertEqual(
            exported["matrix_aliases"]["NAV_HOME"]["source"],
            "./f-row-navigation-aliases.json",
        )

    def test_row_index_canvas_uses_resolved_f_row_region(self) -> None:
        layout = load_compatibility_layout(self.profile, EXAMPLE)
        effective = layout.apply_to_profile(self.profile)
        colors = tuple(Srgb8(index * 20, 0, 0) for index in range(6))
        scene = RectangularSceneBuilder().build(
            "f-row-navigation",
            Canvas(3, 2, colors),
            target="navigation_cluster",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="test",
        )

        resolved, report = SceneCompiler().compile(scene, effective, CAPABILITIES)
        mapped = {visual.element_id: visual.color for visual in resolved.visuals}

        self.assertEqual(mapped["F1"], Srgb8(0, 0, 0))
        self.assertEqual(mapped["F6"], Srgb8(100, 0, 0))
        self.assertEqual(report.unrenderable_controls, ())

    def test_alias_direct_matrix_and_element_selectors_share_one_control_model(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            overlay = self.write_json(
                directory,
                "layout.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "matrix_aliases": {"FIRST": [0, 1]},
                    "regions": [
                        {
                            "id": "mixed",
                            "rows": [
                                [
                                    "FIRST",
                                    {"matrix": [0, 2]},
                                    {"element": "F3"},
                                ]
                            ],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )

            region = load_compatibility_layout(self.profile, overlay).region("mixed")

        self.assertEqual(
            region.controls,
            (ControlId.key(0, 1), ControlId.key(0, 2), ControlId.key(0, 3)),
        )
        self.assertEqual(
            tuple(control.source for control in region.rows[0]),
            ("alias:FIRST@layout.json", "matrix[0,2]", "element:F3"),
        )

    def test_profile_missing_control_is_retained_and_reported_to_rgb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            overlay = self.write_json(
                directory,
                "layout.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "regions": [
                        {
                            "id": "partial",
                            "rows": [[{"matrix": [0, 1]}, {"matrix": [3, 12]}]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )
            layout = load_compatibility_layout(self.profile, overlay)

        region = layout.region("partial")
        self.assertEqual(region.controls[-1], ControlId.key(3, 12))
        self.assertEqual(region.rows[0][-1].rgb_issue, "profile_missing")
        effective = layout.apply_to_profile(self.profile)
        scene = RectangularSceneBuilder().build(
            "partial",
            Canvas(2, 1, (Srgb8(1, 2, 3), Srgb8(4, 5, 6))),
            target="partial",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="test",
        )
        resolved, report = SceneCompiler().compile(scene, effective, CAPABILITIES)
        mapped = {visual.element_id: visual.color for visual in resolved.visuals}
        self.assertEqual(mapped["F1"], Srgb8(1, 2, 3))
        self.assertEqual(report.unrenderable_controls, (("0x030C", "profile_missing"),))

    def test_all_unrenderable_region_fails_only_when_rgb_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            overlay = self.write_json(
                directory,
                "layout.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "regions": [
                        {
                            "id": "input_only",
                            "rows": [[{"matrix": [3, 12]}]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )
            layout = load_compatibility_layout(self.profile, overlay)

        self.assertEqual(layout.region("input_only").controls, (ControlId.key(3, 12),))
        effective = layout.apply_to_profile(self.profile)
        scene = RectangularSceneBuilder().build(
            "input-only",
            Canvas(1, 1, (Srgb8(1, 2, 3),)),
            target="input_only",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="test",
        )
        with self.assertRaisesRegex(CapabilityError, "no RGB-renderable"):
            SceneCompiler().compile(scene, effective, CAPABILITIES)

    def test_undefined_duplicate_and_unsafe_aliases_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            undefined = self.write_json(
                directory,
                "undefined.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "regions": [
                        {
                            "id": "bad",
                            "rows": [["MISSING"]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(CompatibilityLayoutError, "undefined"):
                load_compatibility_layout(self.profile, undefined)

            duplicate = self.write_json(
                directory,
                "duplicate.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "matrix_aliases": {"SAME": [0, 1]},
                    "regions": [
                        {
                            "id": "bad",
                            "rows": [["SAME", {"matrix": [0, 1]}]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(CompatibilityLayoutError, "more than once"):
                load_compatibility_layout(self.profile, duplicate)

            unsafe = self.write_json(
                directory,
                "unsafe.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "alias_imports": ["../outside.json"],
                    "regions": [
                        {
                            "id": "bad",
                            "rows": [[{"matrix": [0, 1]}]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(CompatibilityLayoutError, "escapes"):
                load_compatibility_layout(self.profile, unsafe)

    def test_imported_and_inline_alias_conflict_fails_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.write_json(
                directory,
                "aliases.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "matrix_aliases": {"SAME": [0, 1]},
                },
            )
            overlay = self.write_json(
                directory,
                "layout.json",
                {
                    "schema_version": 1,
                    "profile_id": self.profile.profile_id,
                    "alias_imports": ["./aliases.json"],
                    "matrix_aliases": {"SAME": [0, 2]},
                    "regions": [
                        {
                            "id": "bad",
                            "rows": [["SAME"]],
                            "strategies": ["row_key_index"],
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(
                CompatibilityLayoutError, "defined more than once"
            ):
                load_compatibility_layout(self.profile, overlay)


if __name__ == "__main__":
    unittest.main()
