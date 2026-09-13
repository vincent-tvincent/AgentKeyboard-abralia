# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Deterministic notification presentation, acknowledgement and quiet policies."""

from copy import copy
import unittest
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.notification_visuals import NOTIFICATION_ONSET_SECONDS


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class NotificationVisualTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.b = Broker(clock=self.clock)
        self.counter = 0

    def call(self, slot, operation, **arguments):
        self.counter += 1
        result = self.b.call(slot.caller, operation, {
            'slot_token': slot.slot_token, 'idempotency_key': str(self.counter), **arguments})
        self.assertNotEqual(result['status'], 'rejected', result)
        return result

    def agent(self, index):
        caller = Caller(str(index), str(UUID(int=index)), surface='codex_desktop')
        result = self.b.call(caller, 'acquire_slot', {'label': str(index), 'idempotency_key': 'acquire'})
        return self.b.slots[result['allocation']['slot_id']]

    def notify(self, slot):
        self.call(slot, 'set_notification', enabled=True)
        return slot.agent_notification.notification_id

    def advance(self, seconds):
        self.clock.now += seconds
        self.b.step()

    def complete(self):
        self.advance(NOTIFICATION_ONSET_SECONDS + self.b.config.notification_breath_seconds
                     + self.b.config.orb_formation_seconds + .00001)

    def visual(self):
        return self.b.notification_visuals()

    def bodies(self):
        return {orb['slot_id']: orb for orb in self.visual()['orbs']}

    def observe_question(self, slot, request, stage='accepted'):
        self.b.observe_codex(slot.slot_token, {
            'thread_id': slot.caller.thread_id, 'execution': 'running', 'turn_id': 'turn',
            'questions': [{'request_id': request, 'stage': stage, 'turn_id': 'turn',
                           'tool': 'request_user_input_async'}]})

    def test_phase_progress_and_queue_advance_leave_pickup_arbitration_unchanged(self):
        first, second = self.agent(1), self.agent(2)
        self.notify(first)
        self.notify(second)
        self.assertEqual(self.visual()['presentation']['phase'], 'onset')
        self.assertEqual(self.visual()['queued_tasks'], 1)
        key = self.bodies()[first.slot_id]['orb_key']
        self.assertNotEqual(key, first.slot_token)
        self.advance(NOTIFICATION_ONSET_SECONDS + .2)
        self.assertEqual(self.visual()['presentation']['phase'], 'breathing')
        self.advance(self.b.config.notification_breath_seconds)
        self.assertEqual(self.visual()['presentation']['phase'], 'condensing')
        self.advance(self.b.config.orb_formation_seconds)
        self.assertEqual(self.visual()['presentation']['slot_id'], second.slot_id)
        self.assertEqual(self.b.current_call, first.slot_id)
        self.assertEqual(first.notification.status, 'active')
        self.assertEqual(second.notification.status, 'queued')
        self.assertEqual(self.bodies()[first.slot_id]['orb_key'], key)
        self.assertEqual(self.bodies()[first.slot_id]['opacity'], 1)

    def test_twenty_five_simultaneous_tasks_form_one_orb_each(self):
        slots = [self.agent(index) for index in range(1, 26)]
        self.b.config = BrokerConfig(notification_seconds=1000)
        for slot in slots:
            self.notify(slot)
        for _ in slots:
            self.complete()
        # Older bodies may naturally expire during a long presentation queue.
        self.assertIsNone(self.visual()['presentation'])
        self.assertEqual(self.b.current_call, slots[0].slot_id)
        keys = [item['orb_key'] for item in self.visual()['orbs']]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(self.b.slots[slot.slot_id] is slot for slot in slots))
        self.assertEqual(self.visual()['queued_tasks'], 0)

    def test_same_notice_and_idempotency_retries_never_refresh(self):
        slot = self.agent(1)
        request = {'slot_token': slot.slot_token, 'enabled': True, 'idempotency_key': 'notify-once'}
        self.b.call(slot.caller, 'set_notification', request)
        self.complete()
        formed = self.bodies()[slot.slot_id]['formed_at']
        key = self.bodies()[slot.slot_id]['orb_key']
        self.advance(10)
        replay = self.b.call(slot.caller, 'set_notification', request)
        self.assertTrue(replay['replayed'])
        self.notify(slot)
        self.call(slot, 'set_slot_state', state='progressing', progress=.6)
        self.assertIsNone(self.visual()['presentation'])
        self.assertEqual(self.bodies()[slot.slot_id]['formed_at'], formed)
        self.assertEqual(self.bodies()[slot.slot_id]['orb_key'], key)

    def test_multiple_notices_from_same_task_share_body_and_refresh_only_after_presentation(self):
        slot = self.agent(1)
        self.notify(slot)
        self.complete()
        original = self.bodies()[slot.slot_id]
        self.observe_question(slot, 'request1')
        self.assertEqual(self.visual()['presentation']['slot_id'], slot.slot_id)
        self.assertEqual(self.bodies()[slot.slot_id]['orb_key'], original['orb_key'])
        self.assertEqual(self.bodies()[slot.slot_id]['formed_at'], original['formed_at'])
        self.observe_question(slot, 'request2')
        self.assertEqual(len(self.visual()['orbs']), 1)
        self.assertEqual(len(self.bodies()[slot.slot_id]['notice_ids']), 3)
        self.complete()
        self.assertGreater(self.bodies()[slot.slot_id]['formed_at'], original['formed_at'])
        self.assertIsNone(self.visual()['presentation'])

    def test_reported_question_native_observation_does_not_add_duplicate_orb(self):
        slot = self.agent(1)
        self.call(slot, 'report_question', question_id='manual', kind='free_text', options=[])
        self.observe_question(slot, 'native')
        self.assertEqual(len(self.visual()['orbs']), 1)
        self.assertEqual(len(self.bodies()[slot.slot_id]['notice_ids']), 1)
        self.assertFalse(slot.native_notifications)
        self.call(slot, 'clear_question', question_id='manual', outcome='answered')
        self.advance(self.b.config.orb_dismiss_seconds)
        self.observe_question(slot, 'native')
        self.assertFalse(self.visual()['orbs'])

    def test_pickup_clears_existing_visual_coverage_but_preserves_unanswered_question(self):
        slot = self.agent(1)
        self.notify(slot)
        self.observe_question(slot, 'request')
        self.b.set_active(True)
        self.b.act_on_call('pickup', slot.slot_token, slot.notification.notification_id)
        self.assertIsNone(self.visual()['presentation'])
        self.advance(self.b.config.orb_dismiss_seconds)
        self.assertFalse(self.visual()['orbs'])
        self.assertIn('request', slot.native_requests)
        self.b.step()
        self.assertFalse(self.visual()['orbs'])

    def test_direct_selection_removes_quiet_orb_after_mute(self):
        slot = self.agent(1)
        self.notify(slot)
        self.b.set_active(True)
        self.b.act_on_call('mute', slot.slot_token, slot.notification.notification_id)
        self.assertIsNone(self.visual()['presentation'])
        self.assertTrue(self.bodies()[slot.slot_id]['quiet'])
        self.assertIsNone(self.b.pending_target())
        self.b.select_slot(slot.slot_token)
        self.advance(self.b.config.orb_dismiss_seconds)
        self.assertFalse(self.visual()['orbs'])
        self.assertEqual(slot.notification.status, 'muted')

    def test_delayed_view_acknowledgement_cannot_clear_new_arrival(self):
        slot = self.agent(1)
        old = self.notify(slot)
        self.complete()
        self.observe_question(slot, 'new-arrival')
        latest = slot.native_notifications['new-arrival'].notification_id
        self.b.acknowledge_notification_visuals(slot.slot_token, [old])
        body = self.bodies()[slot.slot_id]
        self.assertEqual(body['notice_ids'], [latest])
        self.assertEqual(self.visual()['presentation']['notice_id'], latest)
        self.complete()
        self.assertEqual(self.bodies()[slot.slot_id]['opacity'], 1)

    def test_new_notice_after_pickup_remains_eligible(self):
        slot = self.agent(1)
        self.notify(slot)
        self.complete()
        self.b.set_active(True)
        self.b.select_slot(slot.slot_token)
        self.notify(slot)
        self.assertEqual(self.visual()['presentation']['notice_id'], slot.agent_notification.notification_id)
        self.assertEqual(len(self.visual()['orbs']), 1)

    def test_timed_mute_hides_body_without_refresh_and_does_not_replay_on_unmute(self):
        slot = self.agent(1)
        self.notify(slot)
        self.complete()
        original = self.bodies()[slot.slot_id]
        self.b.set_active(True)
        self.b.toggle_navigation()
        self.b._set_cursor(slot.slot_token)
        self.assertTrue(self.b.change_attention_policy('DELETE', 'mute_agent', slot.slot_token,
                                                       self.b.attention_policy_revision))
        self.assertTrue(self.bodies()[slot.slot_id]['hidden'])
        self.assertEqual(self.bodies()[slot.slot_id]['opacity'], 0)
        self.advance(10)
        self.assertTrue(self.b.change_attention_policy('DELETE', 'unmute_agent', slot.slot_token,
                                                       self.b.attention_policy_revision))
        current = self.bodies()[slot.slot_id]
        self.assertFalse(current['hidden'])
        self.assertEqual(current['formed_at'], original['formed_at'])
        self.assertEqual(current['orb_key'], original['orb_key'])
        self.assertIsNone(self.visual()['presentation'])

    def test_notification_arriving_during_policy_mute_ages_quietly(self):
        slot = self.agent(1)
        self.b.agent_mutes = {slot.slot_token: 50}
        self.notify(slot)
        self.assertIsNone(self.visual()['presentation'])
        original = self.bodies()[slot.slot_id]
        self.assertTrue(original['hidden'])
        self.advance(50)
        current = self.bodies()[slot.slot_id]
        self.assertFalse(current['hidden'])
        self.assertEqual(current['formed_at'], original['formed_at'])
        self.assertIsNone(self.visual()['presentation'])

    def test_expiry_fades_without_resurrecting_live_native_question(self):
        slot = self.agent(1)
        self.b.config = BrokerConfig(orb_hold_seconds=1, orb_fade_seconds=2)
        self.observe_question(slot, 'native')
        self.complete()
        self.advance(2)
        self.assertAlmostEqual(self.bodies()[slot.slot_id]['opacity'], .5, places=4)
        self.advance(1)
        self.assertFalse(self.visual()['orbs'])
        self.assertIn('native', slot.native_requests)
        self.observe_question(slot, 'native')
        self.b.step()
        self.assertFalse(self.visual()['orbs'])
        self.assertIsNotNone(self.b.pending_target())

    def test_notification_deadline_does_not_leave_question_orb_after_expiry(self):
        slot = self.agent(1)
        self.b.config = BrokerConfig(notification_seconds=15)
        self.observe_question(slot, 'native')
        self.complete()
        self.advance(4)
        self.assertLess(self.bodies()[slot.slot_id]['opacity'], .1)
        self.advance(1)
        self.advance(self.b.config.orb_dismiss_seconds)
        self.assertFalse(self.visual()['orbs'])
        self.assertIn('native', slot.native_requests)
        self.assertIsNotNone(self.b.pending_target())

    def test_cancel_one_of_multiple_notices_keeps_other_coverage(self):
        slot = self.agent(1)
        self.notify(slot)
        self.observe_question(slot, 'native')
        self.complete()
        self.call(slot, 'set_notification', enabled=False)
        body = self.bodies()[slot.slot_id]
        self.assertEqual(body['notice_ids'], [slot.native_notifications['native'].notification_id])
        self.observe_question(slot, 'native', stage='answered')
        self.advance(self.b.config.orb_dismiss_seconds)
        self.assertFalse(self.visual()['orbs'])

    def test_pinned_question_defers_other_task_onset_until_question_ends(self):
        first, second = self.agent(1), self.agent(2)
        self.call(first, 'report_question', question_id='manual', kind='free_text', options=[])
        self.b.set_active(True)
        self.b.select_slot(first.slot_token)
        self.notify(second)
        self.complete()
        self.assertEqual(self.b.selected, first.slot_id)
        self.assertTrue(first.question.picked_up)
        self.assertIsNone(self.b.current_call)
        self.assertFalse(self.visual()['orbs'])
        self.assertIsNone(self.visual()['presentation'])
        self.assertEqual(self.visual()['queued_tasks'], 1)
        self.call(first, 'clear_question', question_id='manual', outcome='answered')
        self.assertEqual(self.visual()['presentation']['phase'], 'onset')
        self.complete()
        self.assertEqual(self.bodies()[second.slot_id]['opacity'], 1)

    def test_pickup_before_orb_forms_does_not_materialize_phantom_fog(self):
        slot = self.agent(1)
        self.notify(slot)
        self.b.set_active(True)
        self.b.select_slot(slot.slot_token)
        self.assertFalse(self.visual()['orbs'])
        self.assertIsNone(self.visual()['presentation'])

    def test_cancellation_before_orb_forms_does_not_materialize_phantom_fog(self):
        slot = self.agent(1)
        self.notify(slot)
        self.advance(2)
        self.call(slot, 'set_notification', enabled=False)
        self.assertFalse(self.visual()['orbs'])
        self.assertIsNone(self.visual()['presentation'])

    def test_selection_hold_pauses_inflight_presentation_elapsed(self):
        first, second = self.agent(1), self.agent(2)
        self.notify(first)
        self.advance(3)
        before = self.visual()['presentation']['elapsed']
        self.b.set_active(True)
        self.b.select_slot(second.slot_token)
        # Ordinary selection holds the full background while the notice waits.
        self.assertTrue(self.visual()['presentation_paused'])
        self.assertIsNone(self.visual()['presentation'])
        self.advance(self.b.config.background_seconds)
        self.assertFalse(self.visual()['presentation_paused'])
        self.assertAlmostEqual(self.visual()['presentation']['elapsed'], before)
        self.assertIsNone(self.b.background_slot)

    def test_genuine_arrival_overrides_old_browsing_hold_without_hiding_onset(self):
        first, second = self.agent(1), self.agent(2)
        self.b.set_active(True)
        self.b.select_slot(second.slot_token)
        self.assertEqual(self.b.background_slot, second.slot_id)
        self.notify(first)
        self.assertEqual(self.visual()['presentation']['phase'], 'onset')
        self.assertFalse(self.visual()['presentation_paused'])
        self.assertIsNone(self.b.background_slot)

    def test_old_orb_keeps_aging_while_a_new_presentation_is_paused(self):
        first, second = self.agent(1), self.agent(2)
        self.b.config = BrokerConfig(orb_hold_seconds=1, orb_fade_seconds=60)
        self.notify(first)
        self.complete()
        self.observe_question(first, 'new')
        self.b.set_active(True)
        self.b.select_slot(second.slot_token)
        self.b.step()
        self.advance(10)
        self.assertTrue(self.visual()['presentation_paused'])
        self.assertLess(self.bodies()[first.slot_id]['opacity'], 1)

    def test_layout_move_preserves_visual_identity_release_does_not(self):
        slot = self.agent(1)
        self.notify(slot)
        original = self.bodies()[slot.slot_id]['orb_key']
        slot.display_position = 20
        self.b.step()
        self.assertEqual(self.bodies()[slot.slot_id]['orb_key'], original)
        self.call(slot, 'release_slot')
        self.assertFalse(self.visual()['orbs'])
        replacement = self.agent(2)
        self.notify(replacement)
        self.assertNotEqual(self.bodies()[replacement.slot_id]['orb_key'], original)

    def test_renderer_snapshot_is_read_only_and_shallow_checkpoint_is_safe(self):
        slot = self.agent(1)
        self.notify(slot)
        checkpoint = copy(self.b._notification_visual_state)
        notices = checkpoint.notices.copy()
        first = self.visual()
        first['orbs'][0]['identity_color'] = '000000'
        first['orbs'][0]['notice_ids'].clear()
        self.assertNotEqual(first, self.visual())
        self.assertEqual(checkpoint.notices, notices)
        self.b.acknowledge_notification_visuals(slot.slot_token)
        self.assertEqual(checkpoint.notices, notices)
        self.assertIsNotNone(checkpoint.presentation)
        self.assertIsNone(self.visual()['presentation'])

    def test_visual_configuration_is_host_only_and_validated(self):
        for name in ('notification_breath_seconds', 'orb_formation_seconds',
                     'orb_hold_seconds', 'orb_fade_seconds', 'orb_dismiss_seconds'):
            for value in (True, float('nan'), -1, '2'):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        BrokerConfig(**{name: value})
        slot = self.agent(1)
        result = self.b.call(slot.caller, 'set_notification', {
            'slot_token': slot.slot_token, 'enabled': True, 'orb_hold_seconds': 10,
            'idempotency_key': 'not-model-config'})
        self.assertEqual(result['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
