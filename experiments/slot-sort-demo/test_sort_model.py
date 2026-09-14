# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import colorsys
from dataclasses import FrozenInstanceError
import json
import unittest

from sort_model import (DEMO_PROJECTS, MAX_PER_PROJECT, MAX_PROJECTS, PROJECT_ANCHORS,
                        SortModel, demo_payload, grouped_palette, individual_palette, palette_metrics)


def populated():
    model = SortModel()
    for number in range(1, 5):
        for project in DEMO_PROJECTS:
            model.add(project, f'{project[-1]}{number}')
    return model


class SortModelTests(unittest.TestCase):
    def test_incoming_colors_match_recorded_current_broker_sequence(self):
        self.assertEqual(individual_palette(8), ['2080FF', 'C1FF20', 'FC20FF', '20FFBB',
                                               'FF7A20', '3920FF', '48FF20', 'FF2089'])
        self.assertEqual(len(set(individual_palette())), 48)

    def test_project_sort_keeps_first_project_and_agent_arrival_order(self):
        model = SortModel()
        one = model.add('Zeta', 'first')
        two = model.add('Alpha', 'second')
        three = model.add('Zeta', 'third')
        self.assertEqual(model.ordered_slots(), [one, two, three])
        self.assertEqual(model.toggle_sort(), 'project')
        self.assertEqual(model.ordered_slots(), [one, three, two])
        self.assertEqual([slot.slot_id for slot in model.ordered_slots()], [1, 3, 2])

    def test_toggle_round_trip_restores_every_individual_color_and_position(self):
        model = populated()
        original = model.snapshot()
        identities = {slot.slot_id: slot for slot in model.ordered_slots()}
        for _ in range(4):
            model.toggle_sort()
            self.assertEqual([slot.slot_id for slot in model.ordered_slots()], [1, 4, 7, 10, 2, 5, 8, 11, 3, 6, 9, 12])
            self.assertTrue(all(model.color(slot) == slot.grouped_color for slot in model.ordered_slots()))
            model.toggle_sort()
            self.assertEqual(model.snapshot(), original)
            self.assertTrue(all(identities[slot.slot_id] is slot for slot in model.ordered_slots()))
        with self.assertRaises(FrozenInstanceError):
            identities[1].slot_id = 99

    def test_incoming_add_opens_second_page_without_moving_old_slots(self):
        model = populated()
        before = model.snapshot()['slots']
        added = model.add('Project A', 'A5')
        after = model.snapshot()
        self.assertEqual(after['slots'][:-1], before)
        self.assertEqual((added.slot_id, after['page_count'], after['slots'][-1]['page'], after['slots'][-1]['f_key']), (13, 2, 2, 'F1'))

    def test_grouped_add_inserts_into_project_and_pushes_stable_agent_across_page(self):
        model = populated(); model.toggle_sort()
        before = {row['slot_id']: row for row in model.snapshot()['slots']}
        added = model.add('Project A', 'A5')
        after = {row['slot_id']: row for row in model.snapshot()['slots']}
        self.assertEqual((after[added.slot_id]['display_position'], after[added.slot_id]['f_key']), (5, 'F5'))
        self.assertEqual((before[12]['page'], before[12]['f_key']), (1, 'F12'))
        self.assertEqual((after[12]['page'], after[12]['f_key']), (2, 'F1'))
        for slot_id in before:
            for field in ('slot_id', 'arrival_sequence', 'project', 'individual_color', 'grouped_color'):
                self.assertEqual(before[slot_id][field], after[slot_id][field])
        model.toggle_sort()
        self.assertEqual([row['slot_id'] for row in model.snapshot()['slots']], list(range(1, 14)))
        self.assertTrue(all(row['color'] == row['individual_color'] for row in model.snapshot()['slots']))

    def test_new_project_does_not_recolor_older_agents_and_limits_are_demo_only(self):
        model = SortModel()
        first = model.add('First', 'agent')
        original = (first.individual_color, first.grouped_color)
        for index in range(1, MAX_PROJECTS):
            model.add(f'Project {index}', 'agent')
        self.assertEqual((first.individual_color, first.grouped_color), original)
        before = model.snapshot()
        with self.assertRaises(ValueError): model.add('Too many', 'agent')
        self.assertEqual(model.snapshot(), before)
        for index in range(1, MAX_PER_PROJECT): model.add('First', str(index))
        with self.assertRaises(ValueError): model.add('First', 'ninth')

    def test_group_colors_stay_in_family_with_modest_saturation_value_variation(self):
        palettes = {str(index): grouped_palette(index) for index in range(MAX_PROJECTS)}
        for project, colors in palettes.items():
            anchor = PROJECT_ANCHORS[int(project)]
            for color in colors:
                hsv = colorsys.rgb_to_hsv(*(int(color[start:start+2], 16)/255 for start in (0, 2, 4)))
                self.assertLessEqual(abs((hsv[0]*360-anchor+180) % 360-180), 18.5)
                self.assertGreaterEqual(hsv[1], .675); self.assertLessEqual(hsv[1], .885)
                self.assertGreaterEqual(hsv[2], .855)
        metrics = palette_metrics(palettes)
        self.assertEqual(metrics['unique_colors'], 48)
        self.assertGreater(metrics['minimum_between_project_hue_degrees'], 23)
        self.assertGreater(metrics['minimum_within_project_rgb_distance'], 0)

    def test_payload_is_json_serializable_and_supplies_palette_for_interactive_add(self):
        payload = json.loads(json.dumps(demo_payload()))
        self.assertEqual(payload['initial'], populated().snapshot())
        self.assertEqual(len(payload['initial']['slots']), 12)
        self.assertEqual(payload['initial']['page_count'], 1)
        self.assertEqual(len(payload['individual_palette']), 48)
        model = populated(); model.toggle_sort()
        added = model.add('Project A', 'A5')
        self.assertEqual(added.individual_color, payload['individual_palette'][12])
        self.assertEqual(added.grouped_color, payload['grouped_palettes']['Project A'][4])


if __name__ == '__main__':
    unittest.main()
