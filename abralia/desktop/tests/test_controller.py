# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest
from pathlib import Path

from abralia.layout import load_compatibility_layout
from abralia.rgb import (
    Canvas,
    MappingStrategy,
    PhysicalSceneBuilder,
    RectangularSceneBuilder,
    RgbController,
    Srgb8,
    load_profile,
)
from abralia.rgb.adapters.base import AdapterHealth, DeviceSnapshot
from abralia.rgb.compatibility import AdapterCapabilities
from abralia.rgb.errors import CapabilityError, OutputSuspendedError
from abralia.rgb.key_lookup import LiveKeymapAddressSpace
from abralia.rgb.led_mapper import DeviceFrame
from abralia.rgb.profiles import EncoderDirection, EncoderPosition
from profile_fixtures import REFERENCE


class FakeAdapter:
    adapter_id = "keychron-effect25-rawhid"
    adapter_version = 1

    def __init__(self, *, profile) -> None:
        self.profile = profile
        self.original = DeviceSnapshot(self.adapter_id, {"original": True})
        self.submitted: list[DeviceFrame] = []
        self.refresh_count = 0
        self.restored: DeviceSnapshot | None = None
        self.handoff_count = 0
        self.closed = False

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(True, True, True, True, 2.0)

    def snapshot(self) -> DeviceSnapshot:
        return self.original

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
            (layer, row, column): self._matrix_keycode(layer, row, column)
            for layer in layers
            for row in range(rows)
            for column in range(columns)
        }

    @staticmethod
    def _matrix_keycode(layer: int, row: int, column: int) -> int:
        if (row, column) == (2, 2) and layer in (0, 1):
            return 0x0004
        if (row, column) == (3, 12) and layer in (0, 2):
            return 0x1234
        return 0

    def read_encoder_keycodes(
        self,
        layers: tuple[int, ...],
        positions: tuple[EncoderPosition, ...],
    ) -> dict[tuple[int, EncoderPosition], int]:
        return {
            (layer, position): (
                0x0080
                if position.direction is EncoderDirection.CLOCKWISE and layer in (0, 2)
                else 0
            )
            for layer in layers
            for position in positions
        }

    def submit_frame(self, frame: DeviceFrame, *, brightness_ceiling: int) -> int:
        self.submitted.append(frame)
        self.brightness_ceiling = brightness_ceiling
        return 7

    def refresh(self) -> int:
        self.refresh_count += 1
        return 8

    def clear(self) -> None:
        pass

    def health(self) -> AdapterHealth:
        return AdapterHealth(True, "test")

    def restore(self, snapshot: DeviceSnapshot) -> None:
        self.restored = snapshot

    def restore_preserving_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot:
        self.handoff_count += 1
        return DeviceSnapshot(self.adapter_id, {"rebased": snapshot.payload})

    def rebase_current_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot:
        return DeviceSnapshot(self.adapter_id, {"resumed": snapshot.payload})

    def close(self) -> None:
        self.closed = True


class ControllerTests(unittest.TestCase):
    def test_compatibility_layout_replaces_runtime_region(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        source = (
            Path(__file__).parents[1]
            / "examples"
            / "compatibility"
            / "f-row-navigation.json"
        )
        compatibility = load_compatibility_layout(profile, source)
        controller = RgbController(adapter, profile, compatibility=compatibility)
        scene = RectangularSceneBuilder().build(
            "f-row",
            Canvas(3, 2, (Srgb8(255, 0, 0),) * 6),
            target="navigation_cluster",
            strategy=MappingStrategy.ROW_KEY_INDEX,
            owner="test",
        )

        with controller:
            controller.display([scene])

        lit = [
            item.address
            for item in adapter.submitted[0].leds
            if item.color == Srgb8(255, 0, 0)
        ]
        self.assertEqual(lit, [1, 2, 3, 4, 5, 6])

    def test_context_captures_displays_refreshes_and_restores(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )
        scene = PhysicalSceneBuilder().build(
            "test", {"W": Srgb8(255, 0, 0)}, owner="test"
        )

        with controller:
            lease = controller.display([scene], brightness_ceiling=128)
            self.assertEqual(lease.refresh(), 8)
            lease.close()

        self.assertEqual(len(adapter.submitted), 1)
        self.assertEqual(adapter.brightness_ceiling, 128)
        self.assertEqual(adapter.refresh_count, 1)
        self.assertEqual(adapter.restored, adapter.original)
        self.assertTrue(adapter.closed)

    def test_manual_restore_ends_the_session(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )
        scene = PhysicalSceneBuilder().build("test", {}, owner="test")

        controller.__enter__()
        controller.restore()

        with self.assertRaisesRegex(RuntimeError, "context manager"):
            controller.display([scene])
        controller.close()

    def test_suspend_revokes_leases_and_rebases_final_recovery(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )
        scene = PhysicalSceneBuilder().build(
            "test", {"W": Srgb8(255, 0, 0)}, owner="test"
        )

        with controller:
            lease = controller.display([scene])
            controller.suspend_output()
            self.assertTrue(controller.output_suspended)
            self.assertEqual(adapter.handoff_count, 1)
            with self.assertRaises(OutputSuspendedError):
                lease.refresh()
            with self.assertRaises(OutputSuspendedError):
                controller.display([scene])
            controller.resume_output()
            controller.display([scene]).close()

        self.assertEqual(
            adapter.restored.payload,
            {"resumed": {"rebased": adapter.original.payload}},
        )

    def test_keycode_scene_targets_every_deduplicated_physical_match(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )

        with controller:
            scene, resolution = controller.build_keycode_scene(
                "keycode",
                keycode=0x0004,
                layers=[0, 1, 1],
                color=Srgb8(255, 0, 0),
                owner="test",
            )
            lease = controller.display([scene])
            lease.close()

        self.assertEqual(resolution.physical_element_ids, ("W",))
        self.assertEqual(resolution.matches[0].layers, (0, 1))
        self.assertEqual(adapter.submitted[0].leds[35].color, Srgb8(255, 0, 0))

    def test_keycode_scene_rejects_matching_control_without_rgb(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )

        with controller, self.assertRaisesRegex(CapabilityError, "KNOB_CLOCKWISE"):
            controller.build_keycode_scene(
                "encoder",
                keycode=0x0080,
                layers=[0, 2],
                color=Srgb8(255, 0, 0),
                owner="test",
            )

    def test_keycode_scene_rejects_raw_match_missing_from_profile(self) -> None:
        adapter = FakeAdapter(profile=REFERENCE)
        controller = RgbController(
            adapter, load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
        )

        with controller, self.assertRaisesRegex(CapabilityError, r"matrix\[3,12\]"):
            controller.build_keycode_scene(
                "profile-missing",
                keycode=0x1234,
                layers=[0, 2],
                color=Srgb8(255, 0, 0),
                owner="test",
            )


if __name__ == "__main__":
    unittest.main()
