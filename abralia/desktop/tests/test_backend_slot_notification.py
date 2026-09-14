# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Brief notification-slot cues use notice age and preserve frame brightness."""

from dataclasses import replace
import unittest
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.render import Renderer
from abralia.rgb import load_profile
from abralia.rgb.colors import to_hsv8
from test_backend_brightness_reference import output_values


PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class SlotNotificationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.b = Broker(clock=lambda: self.now)
        self.profile = load_profile(PROFILE)
        self.renderer = Renderer(self.profile)
        self.serial = 0
        self.b.set_active(True)
        self.slot = self.agent(1)

    def agent(self, number):
        thread = str(UUID(int=number))
        caller = Caller('codex:' + thread, thread, 'codex_metadata', 'codex_desktop')
        result = self.b.call(caller, 'acquire_slot', {'label': f'Task {number}', 'idempotency_key': 'acquire'})
        self.assertEqual(result['status'], 'accepted', result)
        return self.b.slots[result['allocation']['slot_id']]

    def call(self, slot, operation, **arguments):
        self.serial += 1
        arguments.setdefault('idempotency_key', str(self.serial))
        result = self.b.call(slot.caller, operation, {'slot_token': slot.slot_token, **arguments})
        self.assertEqual(result['status'], 'accepted', result)
        return result

    def notify(self, slot=None, **arguments):
        slot = slot or self.slot
        self.call(slot, 'set_notification', enabled=True, **arguments)
        return slot.agent_notification

    def advance(self, seconds):
        self.now += seconds
        self.b.step()

    def breaths(self):
        return {row['slot_id']: row for row in self.b.notification_visuals()['slot_breaths']}

    def frame(self, *, enabled=True, renderer=None):
        renderer = renderer or self.renderer
        original = self.b.config
        try:
            if not enabled:
                self.b.config = replace(original, notification_slot_breath_seconds=0)
            return renderer.frame(self.b).payload
        finally:
            self.b.config = original

    def completion(self, slot=None, ids=('turn-1',)):
        slot = slot or self.slot
        return self.b.observe_codex(slot.slot_token, {
            'source': 'codex_observer', 'thread_id': slot.caller.thread_id,
            'execution': 'idle', 'turn_id': ids[-1], 'questions': [],
            'completed_turn_ids': list(ids), 'error': None})

    def question(self, slot=None):
        slot = slot or self.slot
        return self.b.observe_codex(slot.slot_token, {
            'source': 'codex_observer', 'thread_id': slot.caller.thread_id,
            'execution': 'running', 'turn_id': 'question-turn', 'error': None,
            'questions': [{'request_id': 'question-1', 'turn_id': 'question-turn',
                           'stage': 'accepted', 'tool': 'request_user_input_async'}]})

    def test_configuration_has_bounded_duration_and_depth_and_zero_disables(self):
        self.assertEqual(BrokerConfig().notification_slot_breath_seconds, 6)
        self.assertEqual(BrokerConfig().notification_slot_breath_min_percent, 65)
        for field, values in (('notification_slot_breath_seconds', (-1, 31, float('nan'), True)),
                              ('notification_slot_breath_min_percent', (-1, 101, float('inf'), False))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    BrokerConfig(**{field: value})
        for duration, minimum in ((0, 0), (30, 100)):
            BrokerConfig(notification_slot_breath_seconds=duration, notification_slot_breath_min_percent=minimum)
        self.b.config = replace(self.b.config, notification_slot_breath_seconds=0)
        self.notify()
        self.assertFalse(self.breaths())
        first = self.frame().colors['F1']
        self.advance(1)
        self.assertEqual(self.frame().colors['F1'], first)

    def test_routine_status_updates_leave_slots_steady_without_attention(self):
        baseline = self.frame().colors['F1']
        for state in ('progressing', 'error', 'action_requested', 'completed', 'idle'):
            self.call(self.slot, 'set_slot_state', state=state)
            self.advance(.7)
            self.assertFalse(self.breaths())
            self.assertEqual(self.frame().colors['F1'], baseline)

    def test_three_value_breaths_keep_identity_and_end_at_six_seconds(self):
        self.notify()
        values, hues, saturations = [], [], []
        for elapsed in range(7):
            self.now = 100 + elapsed
            self.b.step()
            enabled = to_hsv8(self.frame().colors['F1'])
            disabled = to_hsv8(self.frame(enabled=False).colors['F1'])
            values.append(enabled.value)
            hues.append(enabled.hue)
            saturations.append(enabled.saturation)
            self.assertLessEqual(enabled.value, disabled.value)
            self.assertAlmostEqual(enabled.hue, disabled.hue, delta=1)
            self.assertAlmostEqual(enabled.saturation, disabled.saturation, delta=2)
            if elapsed < 6:
                self.assertAlmostEqual(self.breaths()[1]['elapsed'], elapsed)
                self.assertEqual(self.breaths()[1]['duration'], 6)
            else:
                self.assertFalse(self.breaths())
                self.assertEqual(enabled, disabled)
        self.assertEqual(values[::2], [values[0]] * 4)
        self.assertEqual(values[1::2], [values[1]] * 3)
        self.assertAlmostEqual(values[1], values[0] * .65, delta=1)
        self.assertLessEqual(max(hues)-min(hues), 1)
        self.assertLessEqual(max(saturations)-min(saturations), 2)

    def test_retries_do_not_restart_the_age_or_revive_an_expired_cue(self):
        notice = self.notify(idempotency_key='same-notice')
        identity, created = notice.notification_id, notice.created_at
        self.advance(1)
        trough = self.frame().colors['F1']
        self.notify(idempotency_key='same-notice')
        self.notify()  # An enabled update of the existing call also keeps its onset.
        self.assertEqual(self.frame().colors['F1'], trough)
        self.assertEqual((notice.notification_id, notice.created_at), (identity, created))
        self.assertEqual(self.breaths()[1]['elapsed'], 1)
        self.advance(5)
        self.notify(idempotency_key='same-notice')
        self.assertFalse(self.breaths())

    def test_native_completion_and_question_use_the_same_fresh_slot_cue(self):
        second = self.agent(2)
        self.assertTrue(self.completion())
        self.assertTrue(self.question(second))
        self.assertEqual(set(self.breaths()), {1, 2})
        self.advance(1)
        self.assertTrue(self.completion())
        self.assertTrue(self.question(second))
        self.assertEqual([row['elapsed'] for row in self.breaths().values()], [1, 1])
        for key in ('F1', 'F2'):
            self.assertLess(to_hsv8(self.frame().colors[key]).value,
                            to_hsv8(self.frame(enabled=False).colors[key]).value)

    def test_queued_and_off_page_notices_age_before_presentation_or_visibility(self):
        for number in range(2, 14):
            self.agent(number)
        self.notify()
        self.advance(1)
        last = self.b.slots[13]
        notice = self.notify(last)
        self.assertEqual(notice.status, 'queued')
        self.advance(1)
        self.assertEqual(self.b.notification_visuals()['presentation']['slot_id'], 1)
        self.assertEqual(self.breaths()[13]['elapsed'], 1)
        self.b.turn_page(1)
        self.assertLess(to_hsv8(self.frame().colors['F1']).value,
                        to_hsv8(self.frame(enabled=False).colors['F1']).value)
        self.advance(6)
        self.b.turn_page(-1)
        self.b.turn_page(1)
        self.assertNotIn(13, self.breaths())
        self.assertEqual(self.frame().colors['F1'], self.frame(enabled=False).colors['F1'])

    def test_mute_pickup_direct_selection_and_withdrawal_stop_the_cue(self):
        for action in ('mute', 'pickup', 'select', 'withdraw'):
            with self.subTest(action=action):
                self.setUp()
                notice = self.notify()
                self.advance(1)
                self.assertTrue(self.breaths())
                if action == 'select':
                    self.b.select_slot(self.slot.slot_token)
                elif action == 'withdraw':
                    self.call(self.slot, 'set_notification', enabled=False)
                else:
                    self.b.act_on_call(action, self.slot.slot_token, notice.notification_id)
                self.assertFalse(self.breaths())
                self.assertEqual(self.frame().colors['F1'], self.frame(enabled=False).colors['F1'])

    def test_pickup_covers_queued_notices_but_later_arrival_survives_old_ack(self):
        self.completion(ids=('first',))
        self.advance(1)
        self.completion(ids=('first', 'second'))
        first = self.slot.completion_notifications['first']
        second = self.slot.completion_notifications['second']
        self.assertEqual(second.status, 'queued')
        self.b.act_on_call('pickup', self.slot.slot_token, first.notification_id)
        self.assertFalse(self.breaths())
        self.advance(.5)
        self.completion(ids=('first', 'second', 'third'))
        self.b.acknowledge_notification_visuals(self.slot.slot_token,
                                               (first.notification_id, second.notification_id))
        self.assertEqual(self.breaths()[1]['elapsed'], 0)
        self.advance(1)
        self.assertEqual(self.breaths()[1]['elapsed'], 1)

    def test_early_project_unmute_does_not_revive_existing_or_muted_arrivals(self):
        second = self.agent(2)
        self.notify()
        self.advance(.5)
        self.b.set_project_muted(True)
        self.notify(second)
        self.assertFalse(self.breaths())
        self.advance(.5)
        self.b.set_project_muted(False)
        self.assertFalse(self.breaths())
        self.assertEqual(self.frame().colors['F1'], self.frame(enabled=False).colors['F1'])
        self.assertEqual(self.frame().colors['F2'], self.frame(enabled=False).colors['F2'])

    def test_expiry_and_release_clear_cues_without_affecting_reused_slot(self):
        self.b.config = replace(self.b.config, notification_seconds=1)
        self.notify()
        self.advance(.5)
        self.assertTrue(self.breaths())
        self.advance(.5)
        self.assertFalse(self.breaths())
        self.call(self.slot, 'release_slot')
        replacement = self.agent(2)
        self.assertEqual(replacement.slot_id, 1)
        self.assertFalse(self.breaths())
        self.assertEqual(self.frame().colors['F1'], self.frame(enabled=False).colors['F1'])

    def test_normalized_non_slot_output_and_other_slots_are_unchanged(self):
        self.agent(2)
        self.notify()
        profiles = (PROFILE, 'builtin:keychron-v3-ansi-encoder-effect25')
        renderers = [(load_profile(name), Renderer(load_profile(name))) for name in profiles]
        for elapsed in (.5, 1, 3, 5):
            self.now = 100 + elapsed
            self.b.step()
            for profile_name, (profile, renderer) in zip(profiles, renderers):
                for active in (False, True):
                    self.b.set_active(active)
                    for brightness in (0, 25, 50, 100):
                        self.b.set_background_brightness(brightness)
                        enabled, disabled = self.frame(renderer=renderer), self.frame(enabled=False, renderer=renderer)
                        for limit in (0, 1, 77, 160, 255):
                            with self.subTest(profile=profile_name, active=active, background=brightness,
                                              elapsed=elapsed, limit=limit):
                                before, after = output_values(disabled, profile, limit), output_values(enabled, profile, limit)
                                unaffected = set(before) - {'F1'}
                                self.assertEqual({key: before[key] for key in unaffected},
                                                 {key: after[key] for key in unaffected})
                                self.assertLessEqual(max(value[2] for value in after.values()), limit)

    def test_preview_candidate_keeps_its_existing_emphasis_and_breathing(self):
        second = self.agent(2)
        self.agent(3)
        self.notify()
        self.notify(second)
        self.b.cycle_knob()
        self.assertIs(self.b.overview_candidate(), self.slot)
        self.advance(1)
        enabled, disabled = self.frame(), self.frame(enabled=False)
        self.assertEqual(enabled.colors['F1'], disabled.colors['F1'])
        self.assertEqual(enabled.colors['F3'], disabled.colors['F3'])
        self.assertLess(to_hsv8(enabled.colors['F2']).value, to_hsv8(disabled.colors['F2']).value)
        self.assertGreaterEqual(to_hsv8(enabled.colors['F1']).value,
                                1.3 * to_hsv8(enabled.colors['F2']).value)


if __name__ == '__main__':
    unittest.main()
