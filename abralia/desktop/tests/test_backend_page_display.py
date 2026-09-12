# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from uuid import UUID

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.navigation import PAGE_KEYS
from abralia.backend.render import ATTENTION, Renderer
from abralia.interaction import DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8, load_profile
from abralia.rgb.colors import to_hsv8
from test_backend_brightness_reference import output_values

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class PageDisplayTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(clock=lambda:self.now)
        self.profile = load_profile(PROFILE)
        self.renderer = Renderer(self.profile)
        self.owners, self.tokens = [], []
        self.add(25)
        self.b.set_active(True)

    def add(self, count):
        for _ in range(count):
            caller = Caller('fixture:' + str(len(self.owners)), str(UUID(int=len(self.owners)+1)), surface='codex_desktop')
            self.owners.append(caller)
            result = self.b.call(caller,'acquire_slot',{'label':'Fixture','idempotency_key':'start'})
            self.tokens.append(result['allocation']['slot_token'])

    @staticmethod
    def event(routes, binding, generation=7):
        return DeviceEvent(EventType.CONTROL_EDGE,1,1,generation,binding,routes[binding].control,
                           int(Edge.UP),EventFlags(0),0)

    def test_passive_page_display_has_occupied_and_current_colors_then_expires(self):
        self.assertFalse(self.b.page_display()['visible'])
        self.b.turn_page(1)
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.colors['2'],Srgb8(128,72,0))
        self.assertEqual(frame.colors['1'],frame.colors['3'])
        self.assertLess(to_hsv8(frame.colors['1']).value,to_hsv8(frame.colors['2']).value)
        self.assertFalse(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))
        self.now = 4
        self.b.turn_page(1)
        self.now = 8.9
        self.assertTrue(self.b.page_display()['visible'])
        self.now = 9
        self.b.step()
        self.assertFalse(self.b.page_display()['visible'])
        self.assertTrue(set(PAGE_KEYS).isdisjoint(self.renderer.frame(self.b).payload.colors))

    def test_selection_keeps_page_display_and_numbers_jump_without_selecting_an_agent(self):
        self.b.cycle_knob()
        self.b.rotate_knob(3)
        self.now = 10
        routes = routes_for(self.b,self.profile)
        self.assertEqual({r.page_target for r in routes.values() if r.action == 'jump_page'},{0,1,2})
        self.assertTrue(self.b.page_display()['persistent'])
        dispatch_event(self.b,self.event(routes,101),routes,7)
        self.assertEqual((self.b.page,self.b.candidate().slot_id),(1,16))
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.focus_requests)
        self.assertEqual(self.b.knob_selection_deadline,70)
        self.b.cycle_knob()
        self.assertFalse(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))
        self.assertTrue(self.b.page_display()['visible'])
        self.now = 15
        self.assertFalse(self.b.page_display()['visible'])

    def test_sixty_second_idle_resets_on_input_but_not_model_updates(self):
        self.b.cycle_knob()
        self.now = 59
        self.b.rotate_knob(1)
        self.assertEqual(self.b.knob_selection_deadline,119)
        self.now = 110
        self.b.call(self.owners[0],'set_slot_state',{'slot_token':self.tokens[0],
                    'state':'progressing','idempotency_key':'work'})
        self.assertEqual(self.b.knob_selection_deadline,119)
        self.now = 118.99
        self.b.step()
        self.assertEqual(self.b.knob_mode,'agents')
        self.now = 119
        self.b.step()
        self.assertEqual(self.b.knob_mode,'pages')
        self.assertIsNone(self.b.candidate())
        self.assertFalse(self.b.page_display()['visible'])
        self.assertFalse(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))

    def test_navigation_keys_expire_at_fifteen_but_knob_and_numbers_last_sixty(self):
        self.b.toggle_navigation()
        self.b.select_slot(self.tokens[0])
        self.now = 15
        self.b.step()
        routes = routes_for(self.b,self.profile)
        self.assertFalse(self.b.navigation_active)
        self.assertEqual(self.b.knob_mode,'agents')
        self.assertFalse(any(r.action.startswith('navigate:') for r in routes.values()))
        self.assertTrue(any(r.action == 'jump_page' for r in routes.values()))
        self.assertIn(22,routes)
        self.now = 60
        self.b.step()
        self.assertEqual(self.b.knob_mode,'pages')
        self.b.cycle_knob()
        self.assertEqual(self.b.knob_mode,'agents')  # Can reenter while focused.
        self.b.set_active(False)
        self.assertFalse(self.b.page_display()['visible'])
        self.assertFalse(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))

    def test_number_bursts_are_safe_across_table_updates_and_expired_mode_is_rejected(self):
        self.b.cycle_knob()
        d = DeviceDriver(self.b,PROFILE,'simulated')
        old = routes_for(self.b,self.profile)
        d.routes, d.generation = old, 7
        d.handle_event(self.event(old,101))
        d.route_history[7] = old
        d.routes, d.generation = routes_for(self.b,self.profile), 8
        d.handle_event(self.event(old,102))
        self.assertEqual(self.b.page,2)
        self.b.cycle_knob(); self.b.cycle_knob()
        d.handle_event(self.event(old,100))
        self.assertEqual(self.b.page,2)

    def test_zero_is_tenth_page_and_old_bank_cannot_jump_after_bank_change(self):
        self.add(96)
        self.b.cycle_knob()
        old = routes_for(self.b,self.profile)
        dispatch_event(self.b,self.event(old,109),old,7)
        self.assertEqual(self.b.page,9)
        self.b.turn_page(1)
        self.assertEqual(self.b.page_display()['bank_start'],10)
        self.assertEqual([(x['key'],x['page']) for x in self.b.page_display()['keys']],[('1',11)])
        dispatch_event(self.b,self.event(old,100),old,7)
        self.assertEqual(self.b.page,10)

    def test_empty_page_has_no_jump_binding_and_released_destination_cannot_be_used(self):
        self.b.cycle_knob()
        old = routes_for(self.b,self.profile)
        for i in range(13,25): self.b._release(self.b.slots[i])
        self.assertNotIn(101,routes_for(self.b,self.profile))
        dispatch_event(self.b,self.event(old,101),old,7)
        self.assertEqual(self.b.page,0)
        self.assertNotIn('2',self.renderer.frame(self.b).payload.colors)

    def test_selection_emphasis_preserves_non_slot_lights_and_resets_after_exit(self):
        for percent in (0,25,50,100):
            with self.subTest(background=percent):
                self.b.set_background_brightness(percent)
                before = self.renderer.frame(self.b).payload
                self.b.cycle_knob()
                during = self.renderer.frame(self.b).payload
                a,b = to_hsv8(during.colors['F1']),to_hsv8(during.colors['F2'])
                self.assertGreaterEqual(a.value,b.value * 1.3)
                self.assertLess(a.saturation,to_hsv8(before.colors['F1']).saturation)
                for limit in (0,1,77,160,255):
                    old,new = output_values(before,self.profile,limit),output_values(during,self.profile,limit)
                    unchanged = set(old) - set(self.renderer.f_keys) - {'ENTER','1','2','3'}
                    self.assertEqual({k:old[k] for k in unchanged},{k:new[k] for k in unchanged})
                self.b.cycle_knob()
                after = self.renderer.frame(self.b).payload
                self.assertEqual(before.colors['F2'],after.colors['F2'])

    def test_muted_candidate_keeps_mute_dimming_on_top_of_emphasis(self):
        self.b.toggle_navigation()
        before = self.renderer.frame(self.b).payload
        action,token = self.b.attention_controls()['DELETE']
        self.b.change_attention_policy('DELETE',action,token,self.b.attention_policy_revision)
        muted = self.renderer.frame(self.b).payload
        self.assertAlmostEqual(to_hsv8(muted.colors['F1']).value,to_hsv8(before.colors['F1']).value / 2,delta=1)
        self.assertLess(to_hsv8(muted.colors['F1']).saturation,to_hsv8(before.colors['F1']).saturation)

    def test_page_capture_and_native_question_hints_do_not_compete(self):
        slot = self.b.slots[1]
        self.b.call(self.owners[0],'report_question',{'slot_token':slot.slot_token,'idempotency_key':'q',
                    'question_id':'q','kind':'single_choice', 'options':[{'id':str(i),'label':str(i)} for i in range(3)],'allow_other':True})
        self.b.select_slot(slot.slot_token)
        self.b.cycle_knob()
        self.assertTrue(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.colors['4'],frame.colors['A'])
        self.assertNotEqual(frame.colors['4'],ATTENTION)
        self.b.cycle_knob()
        self.assertFalse(any(r.action == 'jump_page' for r in routes_for(self.b,self.profile).values()))
        self.assertTrue(slot.question.picked_up)
        self.assertEqual(self.renderer.frame(self.b).payload.colors['4'],ATTENTION)


if __name__ == '__main__': unittest.main()
