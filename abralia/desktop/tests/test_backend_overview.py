# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import dispatch_event, routes_for
from abralia.backend.render import ATTENTION, Renderer, blend, hex_color, within_frame_peak
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8, load_profile


class BackendOverviewTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.b = Broker(clock=lambda: self.now)
        self.profile = load_profile('builtin:keychron-v3-8k-ansi-encoder-effect25')
        self.renderer = Renderer(self.profile)
        self.owners, self.tokens = [], []
        self.counter = 0
        for i in range(25):
            owner = Caller(f'agent:{i}', str(UUID(int=i + 1)), surface='codex_desktop')
            self.owners.append(owner)
            self.tokens.append(self.call(i, 'acquire_slot', label=f'Agent {i}')['allocation']['slot_token'])

    def call(self, index, operation, **arguments):
        self.counter += 1
        arguments['idempotency_key'] = str(self.counter)
        result = self.b.call(self.owners[index], operation, arguments)
        self.assertEqual(result['status'], 'accepted', result)
        return result

    def edge(self, binding, *, routes=None, generation=7):
        routes = routes_for(self.b, self.profile) if routes is None else routes
        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation, binding,
                           routes[binding].control, int(Edge.UP), EventFlags(0), 0)

    def send(self, binding, *, routes=None, generation=7):
        routes = routes_for(self.b, self.profile) if routes is None else routes
        dispatch_event(self.b, self.edge(binding, routes=routes, generation=generation), routes, generation)

    def test_five_agents_on_one_page_can_preview_and_enter_opens_only_candidate(self):
        for i in range(5, 25):
            self.call(i, 'release_slot', slot_token=self.tokens[i])
        self.b.set_active(True)
        self.send(21)
        self.assertEqual(self.b.page, 0)
        self.assertEqual(self.b.events[-1]['kind'], 'knob_rotated')
        self.assertFalse(self.b.events[-1]['changed'])
        self.send(22)
        self.assertEqual(self.b.candidate().slot_id, 1)
        self.send(21)
        self.send(21)
        self.assertEqual(self.b.candidate().slot_id, 3)
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.focus_requests)
        self.send(32)
        self.assertEqual(self.b.selected, 3)
        self.assertEqual(list(self.b.focus_requests), [(self.tokens[2], self.owners[2].thread_id)])
        self.assertNotIn(32, routes_for(self.b, self.profile))
        self.send(40)
        self.assertIsNone(self.b.selected)
        self.assertTrue(self.b.active)
        self.assertEqual(self.b.knob_mode, 'pages')

    def test_agent_rotation_skips_holes_and_clamps_without_paging(self):
        self.call(1, 'release_slot', slot_token=self.tokens[1])
        self.b.set_active(True)
        self.send(22)
        self.send(21)
        self.assertEqual(self.b.candidate().slot_id, 3)
        for _ in range(20): self.send(21)
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (0, 12))
        for _ in range(20): self.send(20)
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (0, 1))
        self.send(22)
        self.send(21)
        self.assertEqual(self.b.page, 1)
        self.assertIsNone(self.b.candidate())
        self.send(22)
        self.assertEqual(self.b.candidate().slot_id, 13)

    def test_ordered_knob_burst_survives_binding_generation_changes(self):
        self.b.set_active(True)
        old = routes_for(self.b, self.profile)
        for binding in (21, 21, 20, 22, 21, 21, 20):
            event = self.edge(binding, routes=old, generation=1)
            dispatch_event(self.b, event, routes_for(self.b, self.profile), 20)
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (1, 14))
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.focus_requests)

    def test_stale_enter_cursor_generation_and_reused_slot_cannot_commit(self):
        self.b.set_active(True)
        self.send(22)
        old = routes_for(self.b, self.profile)
        event = self.edge(32, routes=old)
        self.send(21)
        dispatch_event(self.b, event, old, 7)
        self.assertIsNone(self.b.selected)
        current = routes_for(self.b, self.profile)
        dispatch_event(self.b, self.edge(32, routes=current, generation=6), current, 7)
        self.assertIsNone(self.b.selected)
        self.send(20)
        old = routes_for(self.b, self.profile)
        self.call(0, 'release_slot', slot_token=self.tokens[0])
        self.call(0, 'acquire_slot', label='New allocation')
        dispatch_event(self.b, self.edge(32, routes=old), old, 7)
        self.assertIsNone(self.b.selected)
        self.assertNotIn(32, routes_for(self.b, self.profile))
        self.send(21)
        self.assertEqual(self.b.candidate().slot_id, 1)
        self.assertNotEqual(self.b.candidate().slot_token, self.tokens[0])

    def test_direct_f_key_commits_while_candidate_only_previews(self):
        self.b.set_active(True)
        self.send(22)
        self.send(21)
        self.send(4)
        self.assertEqual(self.b.selected, 4)
        self.assertIsNone(self.b.candidate())
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertEqual(list(self.b.focus_requests), [(self.tokens[3], self.owners[3].thread_id)])

    def test_inactive_knob_is_ordinary_and_focused_knob_can_reenter_selection(self):
        inactive = routes_for(self.b, self.profile)
        self.assertNotIn(22, inactive)
        self.assertNotIn(32, inactive)
        self.send(21)
        self.assertEqual(self.b.page, 0)
        self.b.set_active(True)
        old = routes_for(self.b, self.profile)
        self.send(1)
        self.assertIn(22, routes_for(self.b, self.profile))
        self.assertNotIn(32, routes_for(self.b, self.profile))
        dispatch_event(self.b, self.edge(22, routes=old), old, 7)
        self.assertEqual(self.b.knob_mode, 'agents')
        self.send(40)
        self.send(22)
        self.b.set_active(False)
        self.assertNotIn(32, routes_for(self.b, self.profile))
        self.assertIsNone(self.b.candidate())

    def test_incoming_call_keeps_candidate_and_independent_call_controls(self):
        self.b.set_active(True)
        self.send(22)
        self.call(2, 'set_notification', slot_token=self.tokens[2], enabled=True)
        self.assertTrue({1, 2, 3, 22, 30, 31, 32}.issubset(routes_for(self.b, self.profile)))
        self.assertEqual(self.b.current_call, 3)
        self.send(21)
        self.assertEqual(self.b.candidate().slot_id, 2)
        self.assertEqual(self.b.pending_target().slot_id, 3)
        self.now += .4
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.colors['ENTER'], ATTENTION)
        self.assertEqual(frame.colors['SCREENSHOT'], Srgb8(0, 255, 0))
        self.send(30)
        self.assertEqual(self.b.slots[3].notification.status, 'muted')
        self.assertEqual(self.b.candidate().slot_id, 2)
        self.assertFalse({30, 31} & routes_for(self.b, self.profile).keys())
        self.send(32)
        self.assertEqual(self.b.selected, 2)
        self.assertEqual(self.b.slots[3].notification.status, 'muted')
        self.assertNotIn(32, routes_for(self.b, self.profile))

    def test_picked_question_releases_enter_and_old_preview_cannot_open_a_task(self):
        self.b.set_active(True)
        self.send(22)
        old = routes_for(self.b, self.profile)
        self.call(2, 'report_question', slot_token=self.tokens[2], question_id='native-q',
                  kind='single_choice', options=[{'id':'a','label':'A'}], allow_other=True)
        self.send(31)
        self.assertEqual(self.b.selected, 3)
        controls = {r.control for r in routes_for(self.b, self.profile).values()}
        self.assertNotIn(ControlId.key(*self.profile.element_by_id['ENTER'].matrix), controls)
        self.assertNotIn(32, routes_for(self.b, self.profile))
        self.b.focus_requests.clear()
        dispatch_event(self.b, self.edge(32, routes=old), old, 7)
        self.assertFalse(self.b.focus_requests)
        self.now += 20
        self.b.step()
        self.assertNotIn(32, routes_for(self.b, self.profile))
        self.assertEqual(self.renderer.frame(self.b).payload.colors['ENTER'], ATTENTION)
        self.call(2, 'clear_question', slot_token=self.tokens[2], question_id='native-q', outcome='answered')
        self.assertTrue(self.b.overview_available())
        self.assertEqual(self.b.knob_mode, 'pages')

    def test_released_cursor_clears_cue_and_last_release_resets_mode(self):
        self.b.set_active(True)
        self.send(22)
        for i in range(25): self.call(i, 'release_slot', slot_token=self.tokens[i])
        self.assertIsNone(self.b.candidate())
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertFalse(routes_for(self.b, self.profile))
        self.assertNotIn('ENTER', self.renderer.frame(self.b).payload.colors)

    def test_selection_appearance_is_steady_and_resets_after_confirmation(self):
        self.call(3, 'release_slot', slot_token=self.tokens[3])
        self.b.set_active(True)
        original = self.renderer.frame(self.b).payload
        self.send(22)
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.background, original.background)
        self.assertEqual(frame.colors['F4'], Srgb8(18, 17, 20))
        self.assertEqual(max(frame.colors['F1'].red, frame.colors['F1'].green, frame.colors['F1'].blue),128)
        self.assertLess(max(frame.colors['F2'].red, frame.colors['F2'].green, frame.colors['F2'].blue),100)
        self.assertGreater(frame.colors['F1'].blue,19)  # Slightly whiter than the prior blend.
        self.assertEqual(frame.colors['ENTER'], Srgb8(80, 128, 0))
        self.now += .5
        self.assertEqual(self.renderer.frame(self.b).payload.colors['F1'], frame.colors['F1'])
        self.send(32)
        focused = self.renderer.frame(self.b).payload
        self.assertEqual(focused.background, original.background)
        self.assertEqual(focused.colors['ENTER'], focused.colors['A'])
        self.send(40)
        self.assertNotIn('ENTER', self.renderer.frame(self.b).payload.colors)

    def test_snapshot_exposes_navigation_without_ownership_token(self):
        self.b.set_active(True)
        self.send(22)
        self.send(21)
        snapshot = self.b.admin_snapshot()
        self.assertEqual((snapshot['knob_mode'], snapshot['candidate_slot'], snapshot['candidate_key']), ('agents', 2, 'F2'))
        self.assertTrue(snapshot['overview_available'])
        self.assertNotIn('cursor_token', snapshot)
        self.assertEqual(self.b.status(self.owners[0])['candidate_slot'], 2)

    def test_empty_page_selection_tint_does_not_breathe_with_pause(self):
        for i in range(12): self.call(i, 'release_slot', slot_token=self.tokens[i])
        self.b.set_active(True)
        self.send(22)
        self.assertIsNone(self.b.candidate())
        peaks, pause = set(), set()
        for _ in range(20):
            frame = self.renderer.frame(self.b).payload
            peaks.add(max(channel for color in (frame.background, *frame.colors.values())
                          for channel in (color.red, color.green, color.blue)))
            pause.add(frame.colors['PAUSE'])
            self.now += .1
        self.assertEqual(peaks, {128})
        self.assertGreater(len(pause), 1)

    def test_enter_confirmation_of_pending_call_is_pickup(self):
        self.b.set_active(True)
        self.send(22)
        self.call(0, 'report_question', slot_token=self.tokens[0], question_id='enter-q',
                  kind='free_text')
        self.send(32)
        self.assertTrue(self.b.slots[1].question.picked_up)
        self.assertEqual(self.b.slots[1].notification.status, 'picked_up')
        self.assertFalse({30, 31, 32} & routes_for(self.b, self.profile).keys())
        self.assertIn(40, routes_for(self.b, self.profile))

    def test_invalid_overview_appearance_is_rejected(self):
        for values in ({'overview_tint':'invalid'}, {'overview_tint_percent':-1},
                       {'overview_highlight_percent':101}, {'overview_tint_percent':True}):
            with self.subTest(values=values), self.assertRaises(ValueError): BrokerConfig(**values)


if __name__ == '__main__':
    unittest.main()
