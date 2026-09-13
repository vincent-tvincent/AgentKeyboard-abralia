# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import math
import unittest

from abralia.backend.fog import FogField
from abralia.rgb import Srgb8


def record(key, color="D05010", opacity=1., quiet=False):
    return dict(orb_key=key, identity_color=color, opacity=opacity, quiet=quiet)


def coincident(field):
    for body in field.bodies.values():
        body.x, body.y, body.phase, body.vx, body.vy = 6., 2., 0., .45, 0.


def positions(field):
    return [(b.key, b.x, b.y, b.vx, b.vy) for b in field.bodies.values()]


class FogFieldTests(unittest.TestCase):
    def test_complementary_and_same_hue_mixing_is_linear(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("a", "FF0000"), record("b", "00FFFF")], 0)
        coincident(field)
        mixed, coverage = field.sample_layer(6, 2)
        # Equal red/cyan radiance plus identical white cores produces neutral
        # linear .525, approximately sRGB192 rather than sRGB arithmetic134.
        self.assertEqual(mixed, Srgb8(192, 192, 192))
        self.assertGreater(coverage, field.sample_layer(6, 2, orb_key="a")[1])
        field.update([record("a", "FF0000"), record("b", "FF0000")], 0)
        self.assertEqual(field.sample_layer(6, 2)[0], field.sample_layer(6, 2, orb_key="a")[0])

    def test_core_keeps_identity_saturation_without_raising_brightness(self):
        from abralia.rgb.colors import to_hsv8
        for identity in ('2080FF', 'C1FF20', 'FC20FF'):
            field = FogField((0, 0, 18, 5))
            field.update([record('a', identity)], 0)
            coincident(field)
            tint, alpha = field.sample_layer(6, 2)
            hsv = to_hsv8(tint)
            # The former 20% linear white core reduced these identities to
            # about 50% HSV saturation. Retain at least 70% in the new core.
            self.assertGreaterEqual(hsv.saturation, round(.70 * 255))
            self.assertEqual(hsv.value, 255)
            self.assertGreater(alpha, .8)
            bounded = field.sample(6, 2, Srgb8(128, 128, 128), ceiling=128)
            self.assertEqual(max(bounded.red, bounded.green, bounded.blue), 128)

    def test_permutations_preserve_motion_and_mixing(self):
        entries = [record("a", "FF0000"), record("b", "00FF00"), record("c", "0000FF")]
        a, b = FogField((0, 0, 18, 5)), FogField((0, 0, 18, 5))
        for i in range(100):
            a.update(entries, i / 20)
            b.update(entries[::-1], i / 20)
        self.assertEqual(positions(a), positions(b))
        for point in ((2, 1), (6, 2), (12, 4)):
            self.assertEqual(a.sample(*point, Srgb8(82, 113, 43)), b.sample(*point, Srgb8(82, 113, 43)))

    def test_zero_opacity_and_removal_restore_exact_background(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("a", opacity=0)], 0)
        base = Srgb8(67, 138, 94)
        self.assertIs(field.sample(5, 2, base, ceiling=10), base)
        field.update([], 1)
        self.assertEqual(field.bodies, {})
        self.assertIs(field.sample(5, 2, base), base)

    def test_fade_moves_monotonically_to_arbitrary_background(self):
        field = FogField((0, 0, 18, 5))
        base = Srgb8(95, 121, 141)
        distances = []
        for opacity in (1., .8, .6, .4, .2, 0.):
            field.update([record("a", "D04010", opacity)], 0)
            coincident(field)
            color = field.sample(6, 2, base, ceiling=180)
            distances.append(sum(abs(a - b) for a, b in zip(
                (color.red, color.green, color.blue), (base.red, base.green, base.blue))))
        self.assertEqual(distances, sorted(distances, reverse=True))
        self.assertEqual(distances[-1], 0)

    def test_emission_ceiling_precedes_background_composition(self):
        field = FogField((0, 0, 18, 5))
        field.update([record(str(i), "FFFFFF") for i in range(25)], 0)
        coincident(field)
        color = field.sample(6, 2, Srgb8(30, 60, 90), ceiling=128)
        self.assertLessEqual(max(color.red, color.green, color.blue), 128)
        above = field.sample(6, 2, Srgb8(255, 255, 255), ceiling=128)
        self.assertGreaterEqual(min(above.red, above.green, above.blue), 128)

    def test_forming_orb_filters_and_spread_preserve_body_physics(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("a", "FF0000"), record("b", "0000FF")], 0)
        coincident(field)
        before = positions(field)
        self.assertEqual(field.sample_layer(6, 2, exclude_key="a"), field.sample_layer(6, 2, orb_key="b"))
        self.assertEqual(field.sample_layer(6, 2, orb_key="a", exclude_key="a")[1], 0)
        small = field.sample_layer(10, 2, orb_key="a")[1]
        large = field.sample_layer(10, 2, orb_key="a", spread=4)[1]
        self.assertGreater(large, small)
        self.assertEqual(before, positions(field))

    def test_identity_survives_reordering_and_generation_replaces_body(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("task:old"), record("other")], 0)
        body = field.bodies["task:old"]
        field.update([record("other"), record("task:old", "0099FF", .5)], 0)
        self.assertIs(field.bodies["task:old"], body)
        self.assertEqual(body.color, Srgb8(0, 153, 255))
        field.update([record("task:new")], 0)
        self.assertNotIn("task:old", field.bodies)
        self.assertIsNot(field.bodies["task:new"], body)

    def test_combined_formation_endpoint_matches_simultaneous_overlap(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("old", "FF0000"), record("new", "0000FF")], 0)
        coincident(field)
        base = Srgb8(128, 128, 128)
        steady = field.sample(6, 2, base, ceiling=128)
        endpoint = field.sample(6, 2, base, spread_key="new", spread=1, ceiling=128)
        self.assertEqual(endpoint, steady)
        self.assertEqual(steady.red, steady.blue)
        self.assertEqual(field.sample_layer(6, 2, spread_key="new", spread=1),
                         field.sample_layer(6, 2))
        # Growing one body must leave the older body's light unchanged while
        # expanding the combined field beyond that body's usual volume.
        old_before = field.sample_layer(10, 2, orb_key="old")
        expanded = field.sample_layer(10, 2, spread_key="new", spread=4)
        self.assertGreater(expanded[1], field.sample_layer(10, 2)[1])
        self.assertEqual(field.sample_layer(10, 2, orb_key="old", spread_key="new", spread=4), old_before)

    def test_fixed_steps_are_frame_cadence_independent_and_stall_bounded(self):
        entry = [record("a")]
        a, b = FogField((0, 0, 18, 5)), FogField((0, 0, 18, 5))
        for i in range(121):
            a.update(entry, i / 30)
        for i in range(241):
            b.update(entry, i / 60)
        self.assertEqual(positions(a), positions(b))
        before = positions(a)
        a.update(entry, 4)
        self.assertEqual(before, positions(a))
        a.update(entry, 4000)
        displacement = math.hypot(a.bodies["a"].x - before[0][1], a.bodies["a"].y - before[0][2])
        self.assertLessEqual(displacement, .45 * .25 + 1e-9)
        a.update(entry, 3999)
        self.assertEqual(a._last_time, 4000)

    def test_collisions_separate_small_cores_while_visible_fog_overlaps(self):
        field = FogField((0, 0, 18, 5))
        entries = [record("a"), record("b")]
        field.update(entries, 0)
        coincident(field)
        a, b = field.bodies.values()
        a.vx, b.vx = .45, -.45
        field.update(entries, 1 / 30)
        distance = math.hypot(a.x - b.x, a.y - b.y)
        self.assertGreaterEqual(distance, a.collision_radius + b.collision_radius - 1e-9)
        self.assertLess(distance, a.radius + b.radius)
        self.assertTrue(all(math.isfinite(v) for p in positions(field) for v in p[1:]))

    def test_twenty_five_particles_remain_bounded_through_varied_updates(self):
        field = FogField((0, 0, 18, 5))
        entries = [record(f"agent:{i}", f"{i * 9:02X}80B0") for i in range(25)]
        now = 0.
        for i in range(120):
            field.update(entries, now)
            now += (1 / 30, 1 / 20, .005, .2, 20)[i % 5]
            for body in field.bodies.values():
                core = body.collision_radius
                self.assertTrue(core <= body.x <= 18 - core)
                self.assertTrue(core <= body.y <= 5 - core)
                self.assertAlmostEqual(math.hypot(body.vx, body.vy), .45, places=10)
            color = field.sample(9, 2.5, Srgb8(128, 128, 128), ceiling=128)
            self.assertTrue(all(0 <= c <= 128 for c in (color.red, color.green, color.blue)))

    def test_quiet_reduces_coverage_without_changing_identity_tint(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("a")], 0)
        coincident(field)
        tint, alpha = field.sample_layer(6, 2)
        field.update([record("a", quiet=True)], 0)
        quiet_tint, quiet_alpha = field.sample_layer(6, 2)
        self.assertEqual(quiet_tint, tint)
        self.assertLess(quiet_alpha, alpha)

    def test_invalid_snapshot_does_not_modify_existing_particles(self):
        field = FogField((0, 0, 18, 5))
        field.update([record("a")], 0)
        before = positions(field)
        for records in ([record("a", opacity=math.nan)], [record("a"), record("a")],
                        [record("b", "bad")], [record("b", opacity=2)]):
            with self.assertRaises(ValueError):
                field.update(records, 1)
            self.assertEqual(positions(field), before)
        with self.assertRaises(ValueError):
            field.update([], math.inf)


if __name__ == "__main__":
    unittest.main()
