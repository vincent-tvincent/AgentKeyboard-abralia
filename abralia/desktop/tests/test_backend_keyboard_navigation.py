# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.navigation import ModeKeyHold, NAVIGATION_KEYS
from abralia.backend.render import Renderer
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.interaction.matrix_state import ViaMatrixReader
from abralia.interaction.errors import ProtocolError
from abralia.rgb import Srgb8, load_profile

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class MatrixReaderTests(unittest.TestCase):
    def test_big_endian_rows_offsets_and_unrelated_reports_are_not_claimed(self):
        requests = []
        class Transport:
            def transact(self, request, matcher, timeout_ms):
                requests.append(request)
                self.assertion = matcher(bytes([0xA8, 0x05]) + bytes(30))
                assert not self.assertion
                offset = request[2]
                reply = bytearray(32); reply[:3] = bytes(request)
                for i in range(min(9, 12-offset)):
                    reply[3+i*3:6+i*3] = (1 << ((i+offset) % 17)).to_bytes(3, 'big')
                assert matcher(bytes(reply))
                return bytes(reply)
        rows = ViaMatrixReader(Transport(), 12, 17).read()
        self.assertEqual(rows, tuple(1 << i for i in range(12)))
        self.assertEqual(requests, [[2,3,0], [2,3,9]])

    def test_unavailable_matrix_reply_is_rejected(self):
        class Transport:
            def transact(self, *args, **kwargs): return bytes([255]) + bytes(31)
        with self.assertRaisesRegex(ProtocolError, 'unavailable'):
            ViaMatrixReader(Transport(), 6, 17).read()


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.h = ModeKeyHold((0, 16))
        self.h.sample((0, 0), 0)

    def sample(self, down, when, other=0):
        return self.h.sample((1 << 16 if down else 0, other), when)

    def test_hold_fires_once_and_its_release_cannot_mute(self):
        hits = [self.sample(True, i/10) for i in range(1, 15)]
        self.assertEqual(hits.count(True), 1)
        self.assertEqual(self.h.release_event(1.41), (False, True))
        self.assertFalse(self.sample(False, 1.42))
        self.assertEqual(self.h.release_event(1.43), (False, True))

    def test_short_tap_and_long_second_press_of_double_tap_do_not_fire(self):
        self.sample(True, .1); self.sample(False, .2)
        self.assertEqual(self.h.release_event(.4), (False, False))
        self.sample(True, .45)
        hits = [self.sample(True, .45+i/10) for i in range(1, 13)]
        self.assertFalse(any(hits))
        self.assertFalse(self.sample(False, 1.7))

    def test_intervening_input_ends_double_tap_window(self):
        self.sample(True, .1); self.sample(False, .2)
        self.sample(False, .25, other=1)
        hits = [self.sample(True, .3+i/10, other=1) for i in range(11)]
        self.assertEqual(hits.count(True), 1)

    def test_attach_mid_hold_and_stalled_matrix_read_do_not_invent_holds(self):
        self.h.reset()
        self.sample(True, 0)
        self.assertFalse(any(self.sample(True, i/10) for i in range(1, 12)))
        self.sample(False, 1.2)
        self.sample(True, 1.3)
        self.assertFalse(self.sample(False, 3))
        self.assertEqual(self.h.release_event(3.01), (False, True))

    def test_up_event_can_resolve_hold_before_next_poll(self):
        for i in range(1, 9): self.sample(True, i/10)
        self.assertEqual(self.h.release_event(.91), (True, True))
        self.assertFalse(self.sample(False, .92))


class KeyboardNavigationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(clock=lambda: self.now)
        self.profile = load_profile(PROFILE)
        self.r = Renderer(self.profile)
        self.tokens = []
        for i in range(25):
            result = self.b.call(Caller(f'fixture:{i}'), 'acquire_slot', {'label':'Fixture','idempotency_key':'start'})
            self.tokens.append(result['allocation']['slot_token'])

    def edge(self, binding, routes, generation=7):
        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation, binding,
                           routes[binding].control, int(Edge.UP), EventFlags(0), 0)

    def arm(self):
        self.b.set_active(True); self.b.toggle_navigation()

    def test_inactive_hold_is_ignored_and_home_end_span_all_pages(self):
        self.b.toggle_navigation(); self.assertFalse(self.b.navigation_active)
        self.arm(); self.b.navigate('last_agent')
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (2,25))
        self.b.navigate('first_agent')
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (0,1))
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.focus_requests)

    def test_pages_preserve_position_and_horizontal_keys_skip_holes_and_clamp(self):
        self.b._release(self.b.slots[2])
        self.arm(); self.b.navigate('slot_next')
        self.assertEqual(self.b.candidate().slot_id, 3)
        self.b.navigate('page_next')
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (1,15))
        self.b.navigate('page_next')
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (2,25))
        for _ in range(4): self.b.navigate('slot_next'); self.b.navigate('page_next')
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (2,25))
        self.b.navigate('first_agent')
        self.b.navigate('slot_previous')
        self.assertEqual(self.b.candidate().slot_id, 1)

    def test_timeout_tracks_navigation_activity_and_keeps_committed_focus(self):
        self.arm(); self.now = 14
        self.b.navigate('slot_next')
        self.b.confirm_candidate(self.b.candidate().slot_token, self.b.cursor_revision)
        self.assertEqual(self.b.selected, 2)
        self.assertTrue(self.b.navigation_active)
        self.now = 28.9; self.b.step(); self.assertTrue(self.b.navigation_active)
        self.now = 29; self.b.step(); self.assertFalse(self.b.navigation_active)
        self.assertEqual(self.b.selected, 2)
        self.assertTrue(self.b.active)
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertIsNone(self.b.knob_selection_deadline)
        self.assertIsNone(self.b.cursor_token)
        self.assertIsNone(self.b.overview_candidate())
        routes = routes_for(self.b, self.profile)
        self.assertFalse(set(range(60,68)) & routes.keys())
        self.assertNotIn(32, routes)
        self.assertFalse(any(r.action == 'jump_page' for r in routes.values()))
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page, self.b.selected), (1, 2))
        self.assertIsNone(self.b.candidate())

    def test_second_hold_and_inactive_mode_disarm_immediately(self):
        self.arm(); self.b.select_slot(self.tokens[1])
        focus_revision = self.b.focus_revision
        self.b.toggle_navigation()
        self.assertFalse(self.b.navigation_active)
        self.assertTrue(self.b.active)
        self.assertEqual(self.b.selected, 2)
        self.assertEqual(self.b.focus_revision, focus_revision)
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertIsNone(self.b.knob_selection_deadline)
        self.assertIsNone(self.b.cursor_token)
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page, self.b.selected), (1, 2))
        self.b.toggle_navigation(); self.b.set_active(False)
        self.assertFalse(self.b.navigation_active)
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertIsNone(self.b.knob_selection_deadline)
        self.assertIsNone(self.b.cursor_token)

    def test_timeout_invalidates_old_enter_and_number_routes_after_rearming(self):
        self.arm(); old = routes_for(self.b, self.profile)
        self.now = 15; self.b.step()
        for binding in (32, 101):
            dispatch_event(self.b, self.edge(binding, old), old, 7)
        self.assertIsNone(self.b.selected)
        self.assertEqual(self.b.page, 0)
        self.b.toggle_navigation()
        for binding in (32, 101):
            dispatch_event(self.b, self.edge(binding, old), old, 7)
        self.assertIsNone(self.b.selected)
        self.assertEqual((self.b.page, self.b.candidate().slot_id), (0, 1))

    def test_navigation_knob_starts_with_agents_and_cycles_even_after_focusing(self):
        self.arm()
        self.assertEqual(self.b.knob_mode, 'agents')
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page,self.b.candidate().slot_id),(0,2))
        self.b.confirm_candidate(self.tokens[1], self.b.cursor_revision)
        self.assertIsNotNone(self.b.focused_slot())
        self.assertEqual(self.b.knob_mode, 'agents')
        self.assertIn(22,routes_for(self.b,self.profile))
        routes = routes_for(self.b,self.profile)
        dispatch_event(self.b,self.edge(22,routes),routes,7)
        self.assertEqual(self.b.knob_mode,'pages')
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page,self.b.candidate().slot_id),(1,14))
        self.b.cycle_knob()
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page,self.b.candidate().slot_id),(1,15))
        self.assertEqual(self.b.selected,2)
        # Keyboard directions retain their own meaning in either knob function.
        self.b.navigate('page_next')
        self.assertEqual(self.b.page,2)
        self.b.disarm_navigation('test')
        self.assertIn(22,routes_for(self.b,self.profile))
        self.b.set_active(False)
        self.b.cycle_knob(); self.b.rotate_knob(-1)
        self.assertEqual(self.b.page,2)

    def test_routes_and_highlights_include_available_insert_delete(self):
        self.arm()
        routes = routes_for(self.b, self.profile, hold_enabled=True)
        frame = self.r.frame(self.b).payload
        controls = {route.control for route in routes.values()}
        for key in NAVIGATION_KEYS:
            self.assertIn(ControlId.key(*self.profile.element_by_id[key].matrix), controls)
            self.assertIn(key, frame.colors)
            self.assertNotEqual(frame.colors[key], frame.background)
        for key in ('INSERT','DELETE'):
            self.assertIn(ControlId.key(*self.profile.element_by_id[key].matrix), controls)
            self.assertIn(key, frame.colors)
        self.assertEqual(frame.background.red, 128)
        self.b.disarm_navigation('test')
        for key in (*NAVIGATION_KEYS, 'INSERT', 'DELETE'):
            self.assertNotIn(key, self.r.frame(self.b).payload.colors)

    def test_navigation_groups_have_distinct_semantic_colors_and_keep_call_codes(self):
        self.arm()
        frame = self.r.frame(self.b).payload
        for key in ('PAGE_UP','PAGE_DOWN','UP','DOWN'):
            self.assertEqual(frame.colors[key], Srgb8(0,96,128))
        for key in ('LEFT','RIGHT'):
            self.assertEqual(frame.colors[key], Srgb8(128,72,0))
        for key in ('HOME','END'):
            self.assertEqual(frame.colors[key], Srgb8(80,48,128))
        self.assertEqual(frame.colors['ENTER'], Srgb8(80,128,0))
        owner = Caller('call-colors')
        token = self.b.call(owner,'acquire_slot',{'label':'Call','idempotency_key':'start'})['allocation']['slot_token']
        self.b.call(owner,'set_notification',{'slot_token':token,'enabled':True,'idempotency_key':'notice'})
        ringing = self.r.frame(self.b).payload
        self.assertEqual(ringing.colors['SCREENSHOT'], Srgb8(0,255,0))
        self.assertGreater(ringing.colors['SCROLL_LOCK'].red, 0)
        self.assertEqual((ringing.colors['SCROLL_LOCK'].green,ringing.colors['SCROLL_LOCK'].blue), (0,0))

    def test_old_navigation_arming_cannot_act_after_timeout_and_rearm(self):
        self.arm(); old = routes_for(self.b, self.profile)
        event = self.edge(61, old)
        self.b.disarm_navigation('test'); self.b.toggle_navigation()
        dispatch_event(self.b, event, old, 7)
        self.assertEqual(self.b.page, 0)

    def test_driver_preserves_burst_commands_across_page_tables_but_not_armings(self):
        self.arm(); driver = DeviceDriver(self.b, PROFILE, 'simulated')
        old = routes_for(self.b, self.profile)
        driver.route_history[7] = old
        driver.routes, driver.generation = routes_for(self.b, self.profile), 8
        driver.handle_event(self.edge(61, old))
        driver.handle_event(self.edge(61, old))
        self.assertEqual(self.b.page, 2)
        self.b.disarm_navigation('test'); self.b.toggle_navigation()
        driver.handle_event(self.edge(60, old))
        self.assertEqual(self.b.page, 2)

    def test_pending_question_and_navigation_survive_selection_until_idle_timeout(self):
        owner = Caller('question', '00000000-0000-0000-0000-000000000077', surface='codex_desktop')
        token = self.b.call(owner,'acquire_slot',{'label':'Question','idempotency_key':'start'})['allocation']['slot_token']
        self.b.observe_codex(token, {'thread_id':owner.thread_id,'execution':'running','turn_id':'t',
            'questions':[{'request_id':'q','turn_id':'t','stage':'accepted','tool':'request_user_input_async'}]})
        self.arm(); self.b.navigate('last_agent')
        self.b.confirm_candidate(token, self.b.cursor_revision)
        self.assertTrue(self.b.navigation_active)
        self.assertEqual(self.b.slots[26].notification.status, 'picked_up')
        self.assertIn(32, routes_for(self.b,self.profile))
        self.now = 16; self.b.step()
        self.assertFalse(self.b.navigation_active)
        self.assertIn('q', self.b.slots[26].native_requests)
        self.assertEqual(self.b.selected, 26)
        self.assertTrue(self.b.active)
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertNotIn(32, routes_for(self.b,self.profile))

    def test_pause_gestures_leave_call_ringing_and_separate_scroll_tap_mutes(self):
        owner = Caller('call')
        token = self.b.call(owner,'acquire_slot',{'label':'Call','idempotency_key':'start'})['allocation']['slot_token']
        self.b.call(owner,'set_notification',{'slot_token':token,'enabled':True,'idempotency_key':'call'})
        self.b.set_active(True)
        driver = DeviceDriver(self.b, PROFILE, 'simulated')
        driver.matrix_reader = object()
        driver.routes, driver.generation = routes_for(self.b,self.profile,hold_enabled=True), 7
        h = driver.hold_tracker
        h.sample((0,)*6, 0)
        for i in range(1,10):
            self.now=i/10
            if h.sample((1<<16,0,0,0,0,0), self.now): self.b.toggle_navigation()
        self.now=.91
        driver.handle_event(self.edge(50, driver.routes))
        self.assertTrue(self.b.navigation_active)
        self.assertEqual(self.b.slots[26].notification.status, 'active')
        h.sample((0,)*6,.92)
        h.sample((1<<16,0,0,0,0,0),1.0)
        h.sample((0,)*6,1.1)
        self.now=1.3
        driver.handle_event(self.edge(50,driver.routes))
        self.assertEqual(self.b.slots[26].notification.status, 'active')
        driver.handle_event(self.edge(30,driver.routes))
        self.assertEqual(self.b.slots[26].notification.status, 'muted')

    def test_matrix_failure_disarms_navigation_without_suspending_other_features(self):
        self.arm(); driver = DeviceDriver(self.b,PROFILE,'simulated')
        class Broken:
            def read(self): raise RuntimeError('unavailable')
        driver.matrix_reader = Broken()
        driver.poll_mode_key(0)
        self.assertFalse(self.b.navigation_active)
        self.assertEqual(driver.navigation_input_error,'matrix_readback_unavailable')
        self.assertNotIn(50,driver.build_routes())


if __name__ == '__main__': unittest.main()
