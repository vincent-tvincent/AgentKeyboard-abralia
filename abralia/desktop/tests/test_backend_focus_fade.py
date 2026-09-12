# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest

from abralia.backend.core import Broker, Caller
from abralia.backend.device import routes_for
from abralia.backend.render import Renderer
from abralia.rgb import Srgb8, load_profile


class FocusFadeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(clock=lambda: self.now)
        self.profile = load_profile('builtin:keychron-v3-8k-ansi-encoder-effect25')
        self.r = Renderer(self.profile)
        self.owner = Caller('fixture', '00000000-0000-0000-0000-000000000071', surface='codex_desktop')
        self.token = self.b.call(self.owner, 'acquire_slot', {'label':'Fixture','idempotency_key':'start'})['allocation']['slot_token']
        self.b.set_active(True)

    def test_plain_selection_and_escape_share_hold_midpoint_and_background_endpoint(self):
        self.b.select_slot(self.token)
        self.assertEqual(self.b.background_return_at, 15)
        self.assertEqual(self.b.config.fade_seconds, 5)
        start = self.r.frame(self.b).payload
        self.assertEqual(start.colors['A'], Srgb8(16, 64, 128))
        self.assertEqual(start.colors['ESC'], Srgb8(128, 0, 0))
        self.now = 14.9
        hold = self.r.frame(self.b).payload
        self.assertEqual(hold.colors['A'], start.colors['A'])
        self.assertEqual(hold.colors['ESC'], start.colors['ESC'])
        self.now = 17.5
        middle = self.r.frame(self.b).payload
        self.assertEqual(middle.colors['A'], Srgb8(72, 96, 128))
        self.assertEqual(middle.colors['ESC'], Srgb8(128, 64, 64))
        self.now = 20
        end = self.r.frame(self.b).payload
        self.assertNotIn('A', end.colors)
        self.assertNotIn('ESC', end.colors)
        self.assertEqual(end.background, Srgb8(128, 128, 128))
        self.assertEqual(end.colors['F1'], start.colors['F1'])
        self.assertTrue(self.b.active)
        self.assertIsNotNone(self.b.focused_slot())
        self.assertIn(40, routes_for(self.b, self.profile))
        self.assertTrue(self.b.exit_focus(self.token))
        self.assertNotIn(40, routes_for(self.b, self.profile))

    def test_reselect_restarts_both_visual_timers(self):
        self.b.select_slot(self.token)
        self.now = 18
        faded = self.r.frame(self.b).payload
        self.b.select_slot(self.token)
        restored = self.r.frame(self.b).payload
        self.assertEqual(self.b.background_return_at, 33)
        self.assertNotEqual(faded.colors['A'], restored.colors['A'])
        self.assertEqual(restored.colors['ESC'], Srgb8(128, 0, 0))

    def test_black_background_has_matching_fade_without_hiding_slot(self):
        self.b.set_background_brightness(0)
        self.b.select_slot(self.token)
        self.now = 17.5
        middle = self.r.frame(self.b).payload
        self.assertEqual(middle.colors['A'], Srgb8(16, 64, 128))
        self.assertEqual(middle.colors['ESC'], Srgb8(128, 0, 0))
        self.now = 20
        end = self.r.frame(self.b).payload
        self.assertNotIn('A', end.colors)
        self.assertNotIn('ESC', end.colors)
        self.assertEqual(end.background, Srgb8(0, 0, 0))
        self.assertNotEqual(end.colors['F1'], end.background)

    def test_fade_does_not_resolve_picked_native_question(self):
        self.b.observe_codex(self.token, {'thread_id':self.owner.thread_id,'execution':'running','turn_id':'turn',
            'questions':[{'request_id':'native','turn_id':'turn','tool':'request_user_input_async','stage':'accepted'}]})
        self.b.select_slot(self.token)
        self.now = 21; self.b.step()
        frame = self.r.frame(self.b).payload
        self.assertNotIn('A', frame.colors)
        self.assertNotIn('ESC', frame.colors)
        self.assertIn('native', self.b.slots[1].native_requests)
        self.assertEqual(self.b.slots[1].notification.status, 'picked_up')
        self.assertIn(40, routes_for(self.b, self.profile))


if __name__ == '__main__':
    unittest.main()
