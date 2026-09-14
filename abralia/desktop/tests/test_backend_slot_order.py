# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import colorsys
from pathlib import Path
import unittest
from uuid import UUID

from abralia.backend.core import Allocation, Broker, Caller, Rejected
from abralia.backend.slot_order import normal_color, project_anchor, project_color, project_key


class SlotOrderTests(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(clock=lambda: 10.)
        self.serial = 0

    def acquire(self, project='a'):
        self.serial += 1
        caller = Caller('codex:'+str(UUID(int=self.serial)), str(UUID(int=self.serial)),
                        project_id=project, project_path='/same-display-folder', surface='codex_desktop')
        result = self.broker.call(caller, 'acquire_slot', {'label': 'Same label', 'idempotency_key': 'acquire'})
        self.assertEqual(result['status'], 'accepted', result)
        return self.broker.slots[result['allocation']['slot_id']]

    def fill_page(self):
        return [self.acquire(project) for _ in range(4) for project in ('a', 'b', 'c')]

    def test_sort_round_trip_preserves_ids_tokens_colors_and_calls(self):
        slots = self.fill_page()
        original = {slot.slot_id: (slot.slot_token, slot.individual_color, slot.project_color) for slot in slots}
        target = slots[-1]
        self.broker.call(target.caller, 'set_notification', {'slot_token': target.slot_token, 'enabled': True, 'idempotency_key': 'notify'})
        notice_id = target.notification.notification_id
        self.broker.set_active(True)
        self.broker.toggle_navigation()
        self.broker.cursor_token = target.slot_token
        self.broker.selected = slots[0].slot_id
        for _ in range(3):
            revisions = self.broker.layout_revision, self.broker.page_revision, self.broker.cursor_revision
            self.assertTrue(self.broker.toggle_sort())
            self.assertEqual((self.broker.layout_revision, self.broker.page_revision, self.broker.cursor_revision), tuple(n+1 for n in revisions))
            self.assertEqual([slot.slot_id for slot in sorted(slots, key=lambda s:s.position)], [1,4,7,10,2,5,8,11,3,6,9,12])
            self.assertTrue(all(slot.identity_color == slot.project_color for slot in slots))
            self.assertEqual(self.broker.cursor_token, target.slot_token)
            self.assertEqual(self.broker.selected, slots[0].slot_id)
            self.assertEqual(target.notification.notification_id, notice_id)
            self.assertTrue(self.broker.toggle_sort())
            self.assertEqual([slot.slot_id for slot in sorted(slots, key=lambda s:s.position)], list(range(1,13)))
            self.assertTrue(all(slot.identity_color == slot.individual_color for slot in slots))
        self.assertEqual({slot.slot_id: (slot.slot_token, slot.individual_color, slot.project_color) for slot in slots}, original)
        self.assertEqual(self.broker.identity_colors, {slot.caller.caller_id: slot.individual_color for slot in slots})

    def test_grouped_insertion_moves_preview_across_page_without_retargeting(self):
        slots = self.fill_page(); self.broker.toggle_sort()
        last = slots[-1]
        self.broker.set_active(True); self.broker.toggle_navigation()
        self.broker.cursor_token = last.slot_token
        before = self.broker.layout_revision, self.broker.page_revision, self.broker.cursor_revision
        added = self.acquire('a')
        self.assertEqual(added.position, 5)
        self.assertEqual((last.position, self.broker.page, self.broker.candidate()), (13, 1, last))
        self.assertEqual((self.broker.layout_revision, self.broker.page_revision, self.broker.cursor_revision), tuple(n+1 for n in before))
        self.assertEqual(len({slot.position for slot in self.broker.slots.values()}), 13)
        self.broker.toggle_sort()
        self.assertEqual(added.position, 13)
        self.assertEqual(last.position, 12)

    def test_release_leaves_hole_and_incoming_reuses_id_but_appends_placement(self):
        first, middle, last = [self.acquire('a') for _ in range(3)]
        self.broker.call(middle.caller, 'release_slot', {'slot_token':middle.slot_token,'idempotency_key':'release'})
        added = self.acquire('a')
        self.assertEqual(added.slot_id, middle.slot_id)
        self.assertNotEqual(added.slot_token, middle.slot_token)
        self.assertEqual((first.position, last.position, added.position), (1,3,4))
        self.assertIsNone(self.broker.slot_at_position(2))
        self.assertGreater(added.arrival_order, last.arrival_order)

    def test_grouped_insert_fills_available_next_position_without_shifting_later_group(self):
        a, b, c = self.acquire('a'), self.acquire('a'), self.acquire('b')
        self.broker.toggle_sort()
        self.broker.call(b.caller, 'release_slot', {'slot_token':b.slot_token,'idempotency_key':'release'})
        added = self.acquire('a')
        self.assertEqual((a.position, added.position, c.position), (1,2,3))

    def test_project_family_keys_use_native_identity_not_labels(self):
        a, b = self.acquire('one'), self.acquire('two')
        self.assertEqual(a.label, b.label)
        self.assertNotEqual(project_key(a.caller), project_key(b.caller))
        self.assertNotEqual(a.project_color, b.project_color)
        self.assertEqual(project_key(Caller('unassigned')), 'unassigned')
        self.assertEqual(project_key(Caller('path', project_path='/tmp/example/../project')), 'path:'+str(Path('/tmp/project').resolve()))

    def test_pending_reservations_block_sort_and_grouped_allocation_without_mutation(self):
        self.acquire('a'); self.broker.toggle_sort()
        self.broker.reserved_positions = {3: 'recovering'}
        state = self.broker.export_sort_state()
        before = self.broker.admin_snapshot()
        self.assertFalse(self.broker.toggle_sort())
        caller = Caller('new', project_id='new-project')
        result = self.broker.call(caller, 'acquire_slot', {'label':'New','idempotency_key':'a'})
        self.assertEqual(result['reason'], 'recovery_layout_pending')
        self.assertEqual(self.broker.export_sort_state(), state)
        self.assertEqual(self.broker.admin_snapshot(), before)

    def test_state_roundtrip_is_validated_before_mutation_and_recovery_preserves_colors(self):
        slots = self.fill_page(); self.broker.toggle_sort()
        state = self.broker.export_sort_state()
        restored = Broker(); restored.restore_sort_state(state)
        self.assertEqual(restored.export_sort_state(), state)
        for source in reversed(slots):
            slot = deepcopy(source)
            restored.assign_slot_palette(slot)
            self.assertEqual((slot.arrival_order, slot.identity_color, slot.individual_color, slot.project_color, slot.position),
                             (source.arrival_order, source.identity_color, source.individual_color, source.project_color, source.position))
        before = restored.export_sort_state()
        corrupted = deepcopy(state); corrupted['projects'].append(corrupted['projects'][0])
        with self.assertRaises(Rejected): restored.restore_sort_state(corrupted)
        self.assertEqual(restored.export_sort_state(), before)
        self.assertFalse(restored.slots)

    def test_legacy_palette_assignment_never_moves_or_emits_events(self):
        slot = Allocation(23, 'token', Caller('legacy', project_id='p'), 'Legacy', identity_color='2080FF', display_position=5)
        before = self.broker.layout_revision, self.broker.sequence
        self.broker.assign_slot_palette(slot)
        self.assertEqual((slot.slot_id, slot.position, slot.individual_color, slot.arrival_order), (23,5,'2080FF',1))
        self.assertEqual((self.broker.layout_revision, self.broker.sequence), before)
        self.assertFalse(self.broker.slots)

    def test_retry_results_show_current_palette_and_placement(self):
        slot = self.acquire('a'); self.acquire('b'); self.acquire('a')
        self.broker.toggle_sort()
        retry = self.broker.call(slot.caller, 'acquire_slot', {'label':'Same label','idempotency_key':'acquire'})
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['allocation']['identity_color'], slot.project_color)
        self.assertEqual(retry['allocation']['sort_policy'], 'project')
        self.assertEqual(self.broker.status(slot.caller)['sort_policy'], 'project')


class PaletteExtensionTests(unittest.TestCase):
    def test_accepted_project_palette_is_exact(self):
        expected = ['3695F5','47C8FF','245CE0','2ED5FF','4667EB','1D9FF5','3577DB','52A8FF']
        self.assertEqual([project_color(0,index) for index in range(8)], expected)
        self.assertEqual([project_anchor(index) for index in range(6)], [210,90,330,30,150,270])
        anchors = sorted(project_anchor(index) for index in range(12))
        self.assertGreaterEqual(min((b-a) % 360 for a,b in zip(anchors,anchors[1:]+[anchors[0]])),30)

    def test_arbitrary_large_ordinals_are_deterministic_and_bounded_colors(self):
        huge = 10**30
        for ordinal in (0,7,8,1024,huge,10**400):
            normal = normal_color(ordinal)
            color = project_color(ordinal, ordinal)
            self.assertEqual(color, project_color(ordinal, ordinal))
            self.assertEqual(len(color), 6); self.assertEqual(len(normal), 6)
            hsv = colorsys.rgb_to_hsv(*(int(color[i:i+2],16)/255 for i in (0,2,4)))
            distance = abs((hsv[0]*360-project_anchor(ordinal)+180) % 360-180)
            self.assertLessEqual(distance,18.6)


if __name__ == '__main__':
    unittest.main()
