# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import MagicMock

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.render import Renderer
from abralia.interaction import DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8, load_profile
from abralia.rgb.colors import to_hsv8

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class AttentionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(BrokerConfig(notification_seconds=1200), clock=lambda: self.now)
        self.profile = load_profile(PROFILE)
        self.renderer = Renderer(self.profile)
        self.owners, self.tokens = [], []
        self.serial = 0
        for _ in range(3):
            self.add()
        self.b.set_active(True)
        self.b.toggle_navigation()

    def add(self):
        n = len(self.owners) + 1
        owner = Caller(f'codex:00000000-0000-0000-0000-{n:012d}',
                       f'00000000-0000-0000-0000-{n:012d}', surface='codex_desktop')
        result = self.b.call(owner, 'acquire_slot', {'label':f'Agent {n}', 'idempotency_key':'start'})
        self.owners.append(owner)
        self.tokens.append(result['allocation']['slot_token'])
        return self.b.slots[result['allocation']['slot_id']]

    def call(self, index, op, **args):
        self.serial += 1
        return self.b.call(self.owners[index], op, {'slot_token':self.tokens[index],
                           'idempotency_key':str(self.serial), **args})

    def click(self, key):
        action, token = self.b.attention_controls()[key]
        return self.b.change_attention_policy(key, action, token, self.b.attention_policy_revision)

    def preview(self, index):
        slot = self.b.slots[index + 1]
        self.b.turn_page((slot.slot_id - 1) // 12 - self.b.page)
        self.b._set_cursor(slot.slot_token)

    def test_target_prefers_preview_then_focus_then_current_call(self):
        self.call(2, 'set_notification', enabled=True)
        self.b.select_slot(self.tokens[0])
        self.preview(1)
        self.assertEqual(self.b.attention_control_target().slot_id, 2)
        self.b._set_cursor(None)
        self.assertEqual(self.b.attention_control_target().slot_id, 1)
        self.b.exit_focus(self.tokens[0])
        self.assertEqual(self.b.attention_control_target().slot_id, 3)

    def test_delete_mute_toggle_and_exact_ten_minute_expiry_outlive_navigation(self):
        self.click('DELETE')
        self.assertEqual(self.b.attention_snapshot(self.b.slots[1]),
                         {'muted':True, 'source':'individual', 'remaining_seconds':600})
        self.click('DELETE')
        self.assertFalse(self.b.attention_muted(self.b.slots[1]))
        self.click('DELETE')
        self.now = 599.99
        self.b.step()
        self.assertFalse(self.b.navigation_active)
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.now = 600
        self.b.step()
        self.assertFalse(self.b.attention_muted(self.b.slots[1]))
        self.assertFalse(self.b.agent_mutes)

    def test_only_agent_discards_individual_mutes_and_includes_new_agents(self):
        self.click('DELETE')
        self.preview(1)
        self.click('DELETE')
        self.click('INSERT')
        self.assertFalse(self.b.agent_mutes)
        self.assertFalse(self.b.attention_muted(self.b.slots[2]))
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.assertTrue(self.b.attention_muted(self.add()))
        self.assertNotIn('DELETE', self.b.attention_controls())
        self.assertFalse(self.b.restore_individual_mutes(self.b.attention_policy_revision))
        self.preview(0)
        self.assertEqual(self.b.only_agent_token, self.tokens[1])
        self.click('INSERT')
        self.assertTrue(all(not self.b.attention_muted(s) for s in self.b.slots.values()))
        self.assertFalse(self.b.agent_mutes)

    def test_only_agent_expiry_or_release_restores_all_and_reuse_has_no_mute(self):
        for end in ('expiry', 'release'):
            with self.subTest(end=end):
                self.setUp()
                self.click('DELETE')
                self.preview(1)
                self.click('INSERT')
                if end == 'expiry':
                    self.now = 600
                    self.b.step()
                else:
                    self.call(1, 'release_slot')
                    self.add()
                self.assertFalse(self.b.only_agent_active())
                self.assertFalse(self.b.agent_mutes)
                self.assertTrue(all(not self.b.attention_muted(s) for s in self.b.slots.values()))

    def test_individual_release_does_not_mute_reused_slot(self):
        self.click('DELETE')
        self.call(0, 'release_slot')
        replacement = self.add()
        self.assertEqual(replacement.slot_id, 1)
        self.assertFalse(self.b.attention_muted(replacement))

    def test_controls_follow_next_action_and_disarm_with_navigation(self):
        colors = self.renderer.frame(self.b).payload.colors
        self.assertEqual(colors['DELETE'], Srgb8(128,0,0))
        self.assertEqual(colors['INSERT'], Srgb8(128,0,0))
        self.click('DELETE')
        self.assertEqual(self.renderer.frame(self.b).payload.colors['DELETE'], Srgb8(80,128,0))
        self.click('INSERT')
        colors = self.renderer.frame(self.b).payload.colors
        self.assertNotIn('DELETE', colors)
        self.assertNotIn(70, routes_for(self.b, self.profile))
        self.assertEqual(colors['INSERT'], Srgb8(80,128,0))
        self.click('INSERT')
        self.assertEqual(self.renderer.frame(self.b).payload.colors['DELETE'], Srgb8(128,0,0))
        self.b.disarm_navigation('test')
        self.assertFalse({70,71} & routes_for(self.b, self.profile).keys())
        self.assertFalse({'INSERT','DELETE'} & self.renderer.frame(self.b).payload.colors.keys())
        self.b.set_active(False)
        self.assertFalse(self.b.attention_controls())

    def test_muted_slots_are_dimmer_whiter_and_surrounding_lights_stay_unchanged(self):
        before = self.renderer.frame(self.b).payload
        self.click('DELETE')
        after = self.renderer.frame(self.b).payload
        old, muted = to_hsv8(before.colors['F1']), to_hsv8(after.colors['F1'])
        self.assertLess(muted.saturation, old.saturation)
        self.assertAlmostEqual(muted.value, old.value / 2, delta=1)
        self.assertEqual(before.background, after.background)
        for key in ('F2','F3','F4','PAUSE','UP','LEFT','HOME','ENTER'):
            self.assertEqual(before.colors[key], after.colors[key])
        self.b.set_active(False)
        self.assertLess(to_hsv8(self.renderer.frame(self.b).payload.colors['F1']).value,
                        to_hsv8(self.renderer.frame(self.b).payload.colors['F2']).value)

    def test_mute_parks_calls_and_other_agents_remain_independently_eligible(self):
        self.call(0, 'set_notification', enabled=True)
        started = self.b.slots[1].notification.started_at
        self.click('DELETE')
        self.assertIsNone(self.b.current_call)
        self.assertIsNone(self.b.pending_target())
        self.assertFalse(self.b.has_attention())
        self.assertNotIn('A', self.renderer.frame(self.b).payload.colors)
        self.call(1, 'set_notification', enabled=True)
        self.assertEqual(self.b.current_call, 2)
        self.b.act_on_call('mute', self.tokens[1], self.b.slots[2].notification.notification_id)
        self.now = 600
        self.b.step()
        self.assertEqual(self.b.current_call, 1)
        self.assertEqual(self.b.slots[1].notification.started_at, started)
        self.assertTrue(self.b.slots[2].notification.controls_dismissed)

    def test_agent_retries_and_native_questions_cannot_bypass_mute(self):
        self.click('DELETE')
        self.call(0, 'set_notification', enabled=True)
        self.call(0, 'set_notification', enabled=True)
        self.b.observe_codex(self.tokens[0], {'thread_id':self.owners[0].thread_id,
            'execution':'running', 'turn_id':'t', 'questions':[
                {'request_id':'q', 'turn_id':'t', 'stage':'accepted', 'tool':'request_user_input_async'}]})
        self.b.step()
        self.assertIsNone(self.b.current_call)
        self.assertFalse(self.b.has_attention())
        self.assertIn('q', self.b.slots[1].native_requests)
        self.b.select_slot(self.tokens[0])
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.assertIn('q', self.b.slots[1].native_requests)
        self.assertEqual(self.b.slots[1].notification.status, 'picked_up')

    def test_only_agent_suppresses_previously_focused_question_without_blocking_allowed_call(self):
        self.call(0, 'report_question', question_id='q', kind='free_text', options=[], allow_other=True)
        self.b.select_slot(self.tokens[0])
        self.preview(1)
        self.click('INSERT')
        self.call(1, 'set_notification', enabled=True)
        self.assertEqual(self.b.current_call, 2)
        self.assertIsNotNone(self.b.slots[1].question)

    def test_stale_policy_target_generation_and_arming_cannot_retarget(self):
        routes = routes_for(self.b, self.profile)
        route = routes[70]
        event = DeviceEvent(EventType.CONTROL_EDGE, 1, 1, 7, 70, route.control, int(Edge.UP), EventFlags(0), 0)
        self.preview(1)
        dispatch_event(self.b, event, routes, 7)
        self.assertFalse(self.b.agent_mutes)
        self.preview(0)
        dispatch_event(self.b, event, routes, 8)
        self.assertFalse(self.b.agent_mutes)
        dispatch_event(self.b, event, routes, 7)
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        dispatch_event(self.b, event, routes, 7)
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.b.disarm_navigation('test')
        self.b.toggle_navigation()
        self.assertFalse(self.b.change_attention_policy('DELETE', 'unmute_agent', self.tokens[0], 0))

    def driver(self):
        d = DeviceDriver(self.b, PROFILE, 'simulated')
        d.routes, d.generation = routes_for(self.b, self.profile), 7
        return d

    @staticmethod
    def edge(d, edge, *, generation=None):
        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation or d.generation, 70,
                           d.routes[70].control, int(edge), EventFlags(0), 0)

    def test_delete_short_tap_toggles_and_long_hold_clears_all_without_release_toggle(self):
        d = self.driver()
        d.handle_event(self.edge(d, Edge.UP))
        self.assertFalse(self.b.agent_mutes)
        d.handle_event(self.edge(d, Edge.DOWN))
        self.now = .1
        d.handle_event(self.edge(d, Edge.UP))
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.preview(1)
        self.click('DELETE')
        d.routes, d.generation = routes_for(self.b, self.profile), 8
        down = self.edge(d, Edge.DOWN)
        up = self.edge(d, Edge.UP)
        d.handle_event(down)
        for _ in range(13):
            self.now += .1
            d._service_delete_press(self.now)
        self.assertFalse(self.b.agent_mutes)
        d.handle_event(up)
        self.assertFalse(self.b.agent_mutes)

    def test_stall_target_change_or_only_agent_during_hold_does_not_restore_or_click(self):
        for interruption in ('stall', 'target', 'only_agent', 'inactive'):
            with self.subTest(interruption=interruption):
                self.setUp()
                self.click('DELETE')
                d = self.driver()
                d.handle_event(self.edge(d, Edge.DOWN))
                if interruption == 'target': self.preview(1)
                elif interruption == 'only_agent': self.click('INSERT')
                elif interruption == 'inactive': self.b.set_active(False)
                if interruption == 'stall':
                    self.now = 2
                    d._service_delete_press(self.now)
                else:
                    for _ in range(13):
                        self.now += .1
                        d._service_delete_press(self.now)
                d.handle_event(self.edge(d, Edge.UP))
                if interruption == 'only_agent':
                    self.assertTrue(self.b.only_agent_active())
                else:
                    self.assertTrue(self.b.attention_muted(self.b.slots[1]))
                    self.assertFalse(self.b.attention_muted(self.b.slots[2]))

    def test_matched_delete_release_survives_unrelated_call_binding_rebuild(self):
        d = self.driver()
        up = self.edge(d, Edge.UP)
        d.handle_event(self.edge(d, Edge.DOWN))
        self.call(1, 'set_notification', enabled=True)
        d.routes, d.generation = routes_for(self.b, self.profile), 8
        self.now = .1
        d.handle_event(up)
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))
        self.assertFalse(self.b.attention_muted(self.b.slots[2]))
        d.handle_event(up)
        self.assertTrue(self.b.attention_muted(self.b.slots[1]))

    def test_hardware_binding_requests_delete_down_and_up_only_for_delete(self):
        d = DeviceDriver(self.b, PROFILE, 'hardware')
        d.protocol, d.rgb, d.interaction = MagicMock(), MagicMock(), MagicMock()
        d.protocol.service.return_value = []
        d.interaction.replace_bindings.return_value.binding_generation = 7
        captured = []
        def replace(bindings):
            captured.extend(bindings)
            return type('Result', (), {'binding_generation':7})()
        d.interaction.replace_bindings.side_effect = replace
        self.b.delivery = 'ready'
        d.tick()
        policies = {binding.entry.binding_id: binding.policy for binding in captured}
        self.assertTrue(policies[70].emit_down)
        self.assertTrue(policies[70].emit_up)
        self.assertFalse(policies[71].emit_down)


if __name__ == '__main__':
    unittest.main()
