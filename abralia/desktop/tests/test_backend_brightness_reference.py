# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Check displayed HSV values after the existing effect-25 frame normalization."""

import unittest

from abralia.backend.core import Broker, Caller
from abralia.backend.render import Renderer
from abralia.rgb import load_profile
from abralia.rgb.colors import to_hsv8


PROFILES = ('builtin:keychron-v3-8k-ansi-encoder-effect25',
            'builtin:keychron-v3-ansi-encoder-effect25')


def output_values(frame, profile, keyboard_limit):
    colors = {key: to_hsv8(frame.colors.get(key, frame.background))
              for key, element in profile.element_by_id.items() if element.rgb_capable}
    peak = max(color.value for color in colors.values())
    result = {}
    for key, color in colors.items():
        value = (max(1, (color.value * keyboard_limit + peak // 2) // peak)
                 if color.value and keyboard_limit and peak else 0)
        result[key] = (color.hue, color.saturation, value)
    return result


class GlobalFrameReferenceTests(unittest.TestCase):
    def new_scene(self, profile_name, percent=50, active=False):
        profile = load_profile(profile_name)
        broker = Broker(clock=lambda: 0)
        broker.set_background_brightness(percent)
        broker.set_active(active)
        return profile, broker, Renderer(profile)

    def acquire(self, broker, number):
        owner = Caller(f'fixture:{number}')
        result = broker.call(owner, 'acquire_slot', {'label': 'Fixture', 'idempotency_key':'acquire'})
        self.assertEqual(result['status'], 'accepted')
        return owner, result['allocation']['slot_token']

    def assert_non_slots_unchanged(self, before, after, renderer):
        self.assertEqual({key:value for key,value in before.items() if key not in renderer.f_keys},
                         {key:value for key,value in after.items() if key not in renderer.f_keys})

    def test_acquisition_release_and_empty_pages_preserve_every_non_slot_key(self):
        for profile_name in PROFILES:
            for percent in (0, 25, 50, 100):
                for active in (False, True):
                    with self.subTest(profile=profile_name, percent=percent, active=active):
                        profile, b, r = self.new_scene(profile_name, percent, active)
                        before = {limit: output_values(r.frame(b).payload, profile, limit)
                                  for limit in (0, 1, 77, 160, 255)}
                        for number in range(25):
                            self.acquire(b, number)
                            frame = r.frame(b).payload
                            for limit, baseline in before.items():
                                self.assert_non_slots_unchanged(baseline, output_values(frame, profile, limit), r)
                        if active:
                            # Create a hole covering the entire current page.
                            for number in range(1, 13):
                                b._release(b.slots[number])
                            for page in (0, 1, 2):
                                b.page = page
                                self.assert_non_slots_unchanged(before[160], output_values(r.frame(b).payload, profile, 160), r)
                        for slot in list(b.slots.values()):
                            b._release(slot)
                        self.assert_non_slots_unchanged(before[160], output_values(r.frame(b).payload, profile, 160), r)

    def test_actual_128_reference_is_used_without_adding_a_lit_key(self):
        profile, b, r = self.new_scene(PROFILES[0])
        original = r.frame(b).payload
        self.assertEqual(original.colors, {})
        self.acquire(b, 1)
        frame = r.frame(b).payload
        self.assertEqual(set(frame.colors), set(r.f_keys))
        self.assertTrue(all(to_hsv8(frame.colors[key]).value == 64 for key in r.f_keys if key != 'F1'))
        self.assertEqual(to_hsv8(frame.colors['F1']).value, 128)
        self.assertEqual(output_values(frame, profile, 160)['A'][2], 160)
        self.assertEqual(frame.background, original.background)
        self.assertEqual(frame.colors['F1'].blue, 128)

    def test_empty_slots_keep_their_relative_dim_level(self):
        profile, b, r = self.new_scene(PROFILES[0])
        for count in (1, 2):
            self.acquire(b, count)
            values = output_values(r.frame(b).payload, profile, 160)
            self.assertEqual(values['A'][2], 160)
            self.assertEqual(values['F1'][2], 160)
            self.assertEqual(values['F12'][2], 80)
        for slot in list(b.slots.values()):
            b._release(slot)
        values = output_values(r.frame(b).payload, profile, 160)
        self.assertEqual(values['F12'][2], values['A'][2])

    def test_background_changes_recompute_reference_without_changing_task_identity(self):
        profile, b, r = self.new_scene(PROFILES[0])
        self.acquire(b, 1)
        identity = b.slots[1].identity_color
        for percent, peak in ((25, 64), (50, 128), (100, 255), (0, 255)):
            b.set_background_brightness(percent)
            frame = r.frame(b).payload
            self.assertEqual(to_hsv8(frame.colors['F1']).value, peak)
            self.assertEqual(b.slots[1].identity_color, identity)
            self.assertEqual(output_values(frame, profile, 160)['F1'][2], 160)
            if percent == 0:
                self.assertEqual(output_values(frame, profile, 160)['A'][2], 0)

    def test_existing_notification_lights_supply_their_own_maximum(self):
        for active in (False, True):
            profile, b, r = self.new_scene(PROFILES[0], active=active)
            owner, token = self.acquire(b, 1)
            b.call(owner, 'set_notification', {'slot_token':token, 'enabled':True, 'idempotency_key':'notice'})
            before = output_values(r.frame(b).payload, profile, 160)
            self.assertEqual(to_hsv8(r.frame(b).payload.colors['F1']).value, 255)
            self.acquire(b, 2)
            self.assert_non_slots_unchanged(before, output_values(r.frame(b).payload, profile, 160), r)

    def test_knob_agent_preview_changes_only_f_row_enter_and_occupied_page_key(self):
        for percent in (0, 25, 50, 100):
            profile, b, r = self.new_scene(PROFILES[0], percent, active=True)
            self.acquire(b, 1); self.acquire(b, 2)
            page = r.frame(b).payload
            before = output_values(page, profile, 160)
            b.cycle_knob()
            preview = r.frame(b).payload
            self.assertEqual(preview.background, page.background)
            after = output_values(preview, profile, 160)
            unchanged = set(before) - set(r.f_keys) - {'ENTER', '1'}
            self.assertEqual({k:before[k] for k in unchanged}, {k:after[k] for k in unchanged})
            self.assertIn('ENTER', preview.colors)
            self.assertEqual(preview.colors['ENTER'].blue, 0)
            self.assertGreater(preview.colors['ENTER'].green, preview.colors['ENTER'].red)


if __name__ == '__main__':
    unittest.main()
