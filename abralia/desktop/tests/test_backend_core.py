# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import dispatch_event, routes_for
from abralia.backend.render import ATTENTION, WHITE, Renderer, hex_color, saturation
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import load_profile, Srgb8
from abralia.rgb.colors import to_hsv8

PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class BrokerTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.b = Broker(clock=self.clock)
        self.counter = 0
        self.a = Caller("a", str(UUID(int=1)), surface="codex_desktop")
        self.c = Caller("c", str(UUID(int=2)), surface="codex_desktop")

    def call(self, owner, op, **args):
        self.counter += 1
        if op != "get_status": args.setdefault("idempotency_key", str(self.counter))
        result = self.b.call(owner, op, args)
        self.assertNotEqual(result["status"], "rejected", result)
        return result

    def acquire(self, owner):
        return self.call(owner, "acquire_slot", label=owner.caller_id)["allocation"]["slot_token"]

    def notify(self, owner, token):
        return self.call(owner, "set_notification", slot_token=token, enabled=True)

    def physical(self, action):
        slot = self.b.pending_target()
        self.b.act_on_call(action, slot.slot_token, slot.notification.notification_id)

    def question(self, owner, token, name="q1"):
        return self.call(owner, "report_question", slot_token=token, question_id=name,
                         kind="single_choice", options=[{"id": x, "label": x.upper()} for x in "abc"], allow_other=True)


class BrokerTests(BrokerTestCase):
    def test_task_colors_survive_state_changes_paging_and_reacquisition(self):
        first = self.acquire(self.a)
        original = self.b.slots[1].identity_color
        colors = {original}
        for i in range(1, 25):
            result = self.call(Caller(f'color:{i}'), 'acquire_slot', label='same state')
            colors.add(result['allocation']['identity_color'])
        self.assertEqual(len(colors), 25)
        for state in ('progressing', 'error', 'action_requested', 'completed', 'idle'):
            result = self.call(self.a, 'set_slot_state', slot_token=first, state=state)
            self.assertEqual(result['allocation']['identity_color'], original)
        self.b.set_active(True)
        self.b.turn_page(2)
        self.assertEqual(self.b.slots[1].identity_color, original)
        self.call(self.a, 'release_slot', slot_token=first)
        replacement = self.call(Caller('replacement-color'), 'acquire_slot', label='replacement')
        self.assertNotEqual(replacement['allocation']['identity_color'], original)
        rejoined = self.call(self.a, 'acquire_slot', label='rejoined')
        self.assertEqual(rejoined['allocation']['identity_color'], original)
        self.assertNotEqual(rejoined['allocation']['slot_id'], 1)

    def test_direct_selection_opens_owner_without_consuming_another_call(self):
        one, two = self.acquire(self.a), self.acquire(self.c)
        self.notify(self.a, one)
        self.b.select_slot(two)
        self.assertFalse(self.b.focus_requests)  # Inactive input cannot focus.
        self.b.set_active(True)
        self.b.select_slot(two)
        self.assertEqual(list(self.b.focus_requests), [(two, self.c.thread_id)])
        self.assertEqual(self.b.current_call, 1)
        self.assertEqual(self.b.pending_target().slot_token, one)
        self.physical('mute')
        self.b.select_slot(one)
        self.assertEqual(list(self.b.focus_requests), [(one, self.a.thread_id)])
        self.assertEqual(self.b.slots[1].notification.status, 'muted')

    def test_rapid_selection_keeps_only_latest_valid_destination(self):
        one, two = self.acquire(self.a), self.acquire(self.c)
        unknown = self.acquire(Caller('unknown'))
        self.b.set_active(True)
        self.b.select_slot(one)
        self.b.select_slot(two)
        self.b.select_slot(one)
        self.assertEqual(list(self.b.focus_requests), [(one, self.a.thread_id)])
        self.b.select_slot(unknown)
        self.assertFalse(self.b.focus_requests)

    def test_color_collisions_do_not_reject_distinct_agent_allocations(self):
        self.acquire(self.a)
        with patch('abralia.backend.core.colorsys.hsv_to_rgb', return_value=(32/255, 128/255, 1)):
            result = self.b.call(self.c, 'acquire_slot', {'label':'collision', 'idempotency_key':'collision'})
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(self.b.slots), 2)
        self.assertNotEqual(self.b.owners[self.a.caller_id], self.b.owners[self.c.caller_id])
        self.assertEqual(self.b.identity_colors[self.a.caller_id], self.b.identity_colors[self.c.caller_id])

    def test_self_registration_uses_own_identity_and_enables_desktop_flow(self):
        owner = Caller("codex:self", str(UUID(int=10)), "codex_metadata")
        result = self.call(owner, "acquire_slot", label="Self registration", harness="codex_desktop")
        token = result["allocation"]["slot_token"]
        self.assertEqual(result["caller"]["thread_id"], owner.thread_id)
        self.assertEqual(result["caller"]["identity_source"], "codex_metadata")
        self.assertEqual(result["caller"]["surface_source"], "agent_reported")
        self.assertEqual(self.question(owner, token)["status"], "accepted")
        self.b.set_active(True)
        self.physical("pickup")
        self.assertEqual(list(self.b.focus_requests), [(token, owner.thread_id)])
        self.assertEqual(self.call(owner, "get_status")["caller"]["surface"], "codex_desktop")
        self.call(owner, "release_slot", slot_token=token)
        self.assertEqual(self.call(owner, "get_status")["caller"]["surface"], "unknown")

    def test_registration_upgrade_retries_and_conflicts_preserve_allocation(self):
        owner = Caller("codex:self", str(UUID(int=10)), "codex_metadata")
        token = self.acquire(owner)
        args = {"label": "Self", "harness": "codex_desktop", "idempotency_key": "register"}
        first = self.b.call(owner, "acquire_slot", args)
        self.assertEqual(first["allocation"]["slot_token"], token)
        self.assertTrue(self.b.call(owner, "acquire_slot", args)["replayed"])
        result = self.b.call(owner, "acquire_slot", {**args, "harness": "codex_cli"})
        self.assertEqual(result["reason"], "idempotency_conflict")
        result = self.b.call(owner, "acquire_slot", {**args, "harness": "codex_cli", "idempotency_key": "switch"})
        self.assertEqual(result["reason"], "harness_conflict_release_and_reacquire")
        self.assertEqual(self.b.slots[1].caller.surface, "codex_desktop")
        self.assertEqual(self.acquire(owner), token)

    def test_registration_cannot_supply_another_identity_or_unavailable_harness(self):
        owner = Caller("codex:self", str(UUID(int=10)), "codex_metadata")
        for extra in ({"thread_id": str(UUID(int=11)), "harness": "codex_desktop"},
                      {"caller_id": "other", "harness": "codex_desktop"},
                      {"harness": "unsupported"}, {"harness": True}):
            result = self.b.call(owner, "acquire_slot", {"label": "Self", "idempotency_key": "bad", **extra})
            self.assertEqual(result["status"], "rejected")
            self.assertFalse(self.b.slots)
        result = self.b.call(Caller("no-identity"), "acquire_slot",
                             {"label": "Self", "harness": "codex_desktop", "idempotency_key": "missing"})
        self.assertEqual(result["reason"], "harness_requires_task_identity")
        token = self.call(owner, "acquire_slot", label="CLI", harness="codex_cli")["allocation"]["slot_token"]
        self.assertEqual(self.question(owner, token)["reason"], "unsupported_question_surface")
        self.notify(owner, token)
        self.b.set_active(True)
        self.physical("pickup")
        self.assertFalse(self.b.focus_requests)

    def test_stable_25_slots_holes_reuse_and_page_clamping(self):
        owners = [Caller(str(i)) for i in range(25)]
        tokens = [self.acquire(o) for o in owners]
        self.assertEqual(self.b.page_count, 3)
        self.call(owners[3], "release_slot", slot_token=tokens[3])
        self.assertEqual(self.b.owners["24"], 25)
        token = self.acquire(Caller("replacement"))
        self.assertEqual(self.b.owners["replacement"], 4)
        self.assertNotEqual(token, tokens[3])
        self.assertEqual(self.b.slots[4].position, 26)  # Incoming arrivals append; released positions stay empty.
        self.b.turn_page(1)
        self.assertEqual(self.b.page, 0)  # Inactive knob input is not a page command.
        self.b.set_active(True)
        self.b.turn_page(10)
        self.assertEqual([s.slot_id for s in self.b.visible_slots()], [25, 4])
        self.call(owners[24], "release_slot", slot_token=tokens[24])
        self.assertEqual((self.b.page, self.b.page_count), (2, 3))
        self.call(Caller('replacement'), 'release_slot', slot_token=token)
        self.assertEqual((self.b.page, self.b.page_count), (1, 2))
        self.b.turn_page(-10)
        self.assertEqual(self.b.page, 0)

    def test_owner_and_token_both_required_and_restart_invalidates(self):
        token = self.acquire(self.a)
        stolen = self.b.call(self.c, "release_slot", {"slot_token": token, "idempotency_key": "steal"})
        self.assertEqual(stolen["status"], "rejected")
        self.call(self.a, "release_slot", slot_token=token)
        new = self.acquire(self.a)
        self.assertNotEqual(token, new)
        r = self.b.call(self.a, "set_slot_state", {"slot_token": token, "state": "error", "idempotency_key": "stale"})
        self.assertEqual(r["status"], "rejected")
        fresh = Broker(clock=self.clock)
        self.assertNotEqual(fresh.epoch, self.b.epoch)
        self.assertEqual(fresh.call(self.a, "get_status", {"slot_token": new})["status"], "rejected")

    def test_idempotency_replay_does_not_rollback_new_state(self):
        token = self.acquire(self.a)
        a = {"slot_token": token, "state": "progressing", "idempotency_key": "working"}
        self.b.call(self.a, "set_slot_state", a)
        self.call(self.a, "set_slot_state", slot_token=token, state="completed")
        result = self.b.call(self.a, "set_slot_state", a)
        self.assertTrue(result["replayed"])
        self.assertEqual(self.b.slots[1].state, "completed")
        self.assertEqual(self.b.call(self.a, "set_slot_state", {**a, "state": "error"})["reason"], "idempotency_conflict")
        self.assertEqual(self.acquire(self.a), token)

    def test_notification_retry_preserves_mute_and_deadline(self):
        token = self.acquire(self.a)
        self.notify(self.a, token)
        n = self.b.slots[1].notification
        original = (n.notification_id, n.expires_at, n.started_at)
        self.b.set_active(True)
        self.physical("mute")
        self.clock.advance(10)
        self.notify(self.a, token)
        self.assertEqual(n.status, "muted")
        self.assertEqual(original, (n.notification_id, n.expires_at, n.started_at))
        self.b.select_slot(token)
        self.assertEqual(n.status, "muted")
        self.assertIsNone(self.b.pending_target())
        self.assertTrue(n.controls_dismissed)

    def test_fifo_and_picked_question_not_preempted(self):
        a, c = self.acquire(self.a), self.acquire(self.c)
        self.question(self.a, a)
        self.notify(self.c, c)
        self.assertEqual(self.b.current_call, 1)
        self.assertEqual(self.b.slots[2].notification.status, "queued")
        self.b.set_active(True)
        self.physical("pickup")
        self.clock.advance(20)
        self.b.step()
        self.assertEqual(self.b.selected, 1)
        self.assertIsNone(self.b.current_call)
        self.call(self.a, "clear_question", slot_token=a, question_id="q1", outcome="answered")
        self.assertEqual(self.b.current_call, 2)

    def test_muting_parks_current_and_promotes_other_call(self):
        a, c = self.acquire(self.a), self.acquire(self.c)
        self.notify(self.a, a)
        self.notify(self.c, c)
        self.b.set_active(True)
        self.physical("mute")
        self.assertEqual(self.b.current_call, 2)
        self.assertEqual(self.b.slots[1].notification.status, "muted")
        self.b.select_slot(a)
        self.assertEqual(self.b.pending_target().slot_id, 2)
        self.physical("pickup")
        self.assertEqual(self.b.slots[1].notification.status, "muted")
        self.assertEqual(self.b.slots[2].notification.status, "picked_up")

    def test_off_page_pickup_switches_page_without_preempting_on_notification(self):
        for i in range(12): self.acquire(Caller(f"fixture:{i}"))
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.assertEqual(self.b.page, 0)
        self.b.set_active(True)
        self.physical("pickup")
        self.assertEqual((self.b.page, self.b.selected), (1, 13))
        self.assertEqual(self.b.focus_requests.popleft(), (token, self.a.thread_id))

    def test_release_clears_only_owner_and_repeated_release_is_harmless(self):
        a, c = self.acquire(self.a), self.acquire(self.c)
        self.question(self.a, a)
        self.notify(self.c, c)
        self.call(self.a, "release_slot", slot_token=a)
        self.assertEqual(self.b.current_call, 2)
        self.assertEqual(self.call(self.a, "release_slot", slot_token=a)["reason"], "already_released")

    def test_disconnect_grace_and_long_silent_connected_work(self):
        token = self.acquire(self.a)
        self.clock.advance(100000)
        self.b.step()
        self.assertIn(1, self.b.slots)
        self.b.disconnected_at("a")
        self.clock.advance(29)
        self.b.step()
        self.assertIn(1, self.b.slots)
        self.b.connected("a")
        self.clock.advance(2)
        self.b.step()
        self.assertIn(1, self.b.slots)
        self.b.disconnected_at("a")
        self.clock.advance(31)
        self.b.step()
        self.assertNotIn(1, self.b.slots)

    def test_question_identity_and_expiry_are_distinct_from_background(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.b.set_active(True)
        self.physical("pickup")
        self.clock.advance(self.b.config.background_seconds + self.b.config.fade_seconds + 1)
        self.b.step()
        self.assertTrue(self.b.active)
        self.assertIsNotNone(self.b.slots[1].question)
        result = self.b.call(self.a, "clear_question", {"slot_token": token, "question_id": "wrong", "outcome": "answered", "idempotency_key": "bad"})
        self.assertEqual(result["reason"], "stale_question")
        self.clock.advance(600)
        self.b.step()
        self.assertIsNone(self.b.slots[1].question)

    def test_question_validation_does_not_mutate_existing_question(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        before = self.b.slots[1].question
        for kind, options in (("native_approval", []), ("single_choice", []),
                              ("single_choice", [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}])):
            r = self.b.call(self.a, "report_question", {"slot_token": token, "question_id": "bad", "kind": kind,
                                                       "options": options, "idempotency_key": f"{kind}{len(options)}"})
            self.assertEqual(r["status"], "rejected")
            self.assertIs(self.b.slots[1].question, before)

    def test_question_remains_pickup_able_after_notification_expiry(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.clock.advance(301)
        self.b.step()
        self.assertEqual(self.b.slots[1].notification.status, "expired")
        self.assertIsNotNone(self.b.pending_target())
        self.assertFalse(self.b.mute_available())
        self.b.set_active(True)
        self.b.select_slot(token)
        self.assertTrue(self.b.slots[1].question.picked_up)
        self.assertIsNone(self.b.pending_target())

    def test_unknown_surface_can_report_status_but_not_desktop_question_or_focus(self):
        owner = Caller("unknown", str(UUID(int=4)))
        token = self.acquire(owner)
        self.assertEqual(self.question(owner, token)["reason"], "unsupported_question_surface")
        self.notify(owner, token)
        self.b.set_active(True)
        self.physical("pickup")
        self.assertFalse(self.b.focus_requests)

    def test_read_status_does_not_allocate_or_disclose_other_owner(self):
        token = self.acquire(self.a)
        self.call(self.a, "set_slot_state", slot_token=token, state="error", summary="private detail")
        result = self.call(self.c, "get_status")
        self.assertNotIn("allocation", result)
        self.assertNotIn("private detail", str(result))
        self.assertEqual(len(self.b.slots), 1)

    def test_queue_limit_and_progress_validation(self):
        self.b = Broker(BrokerConfig(max_pending_calls=1), clock=self.clock)
        a, c = self.acquire(self.a), self.acquire(self.c)
        self.notify(self.a, a)
        self.assertEqual(self.notify(self.c, c)["reason"], "notification_queue_full")
        for value in (-1, 2, float('nan'), True):
            r = self.b.call(self.a, "set_slot_state", {"slot_token": a, "state": "progressing", "progress": value, "idempotency_key": str(value)})
            self.assertEqual(r["status"], "rejected")

    def test_new_mutation_does_not_claim_previous_hid_write_as_its_delivery(self):
        self.b.delivery = "written"
        result = self.call(self.a, "acquire_slot", label="New report")
        self.assertEqual(result["delivery"], "queued")
        self.assertEqual(result["device_delivery"], "written")


class RendererAndRoutingTests(BrokerTestCase):
    def setUp(self):
        super().setUp()
        self.profile = load_profile(PROFILE)
        self.renderer = Renderer(self.profile)

    def test_task_hue_is_stable_across_statuses_and_attention_is_explicit(self):
        token = self.acquire(self.a)
        self.acquire(self.c)
        self.b.set_active(True)
        original = self.renderer.frame(self.b).payload.colors['F1']
        for state in ('idle', 'progressing', 'error', 'action_requested', 'completed'):
            self.call(self.a, 'set_slot_state', slot_token=token, state=state)
            self.clock.advance(.4)
            frame = self.renderer.frame(self.b).payload
            self.assertEqual(frame.colors['F1'], original)
            self.assertNotEqual(frame.colors['F2'], original)
            self.assertIsNone(self.b.current_call)
            self.assertNotIn('A', frame.colors)
        self.b.select_slot(token)
        selected = self.renderer.frame(self.b).payload
        self.assertEqual(selected.colors['A'], selected.colors['F1'])
        self.assertEqual(selected.colors['A'], original)
        self.b.exit_focus(token)
        self.notify(self.a, token)
        self.clock.advance(.4)
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.colors['F1'], hex_color(self.b.slots[1].identity_color))
        self.assertIn('A', frame.colors)

    def test_slot_background_dims_from_first_allocation_until_last_release(self):
        def empty_background():
            frame = self.renderer.frame(self.b).payload
            return frame.colors.get('F12', frame.background)

        self.assertEqual(empty_background(), Srgb8(128, 128, 128))
        first = self.acquire(self.a)
        self.assertEqual(empty_background(), Srgb8(64, 64, 64))
        second = self.acquire(self.c)
        for percent, normal in ((0, 0), (25, 64), (50, 128), (100, 255)):
            self.b.set_background_brightness(percent)
            frame = self.renderer.frame(self.b).payload
            expected = round(normal * .5)
            self.assertEqual(frame.background, Srgb8(normal, normal, normal))
            self.assertEqual(empty_background(), Srgb8(expected, expected, expected))
            for key, slot in (('F1', self.b.slots[1]), ('F2', self.b.slots[2])):
                actual = to_hsv8(frame.colors[key])
                identity = to_hsv8(saturation(hex_color(slot.identity_color), .75))
                self.assertEqual(actual.value, normal or 255)
                self.assertLessEqual(abs(actual.hue - identity.hue), 1)
                self.assertLessEqual(abs(actual.saturation - identity.saturation), 2)
        self.call(self.c, 'release_slot', slot_token=second)
        self.assertEqual(empty_background(), Srgb8(128, 128, 128))
        self.assertEqual(self.b.slots[1].slot_token, first)
        self.call(self.a, 'release_slot', slot_token=first)
        self.assertEqual(empty_background(), WHITE)

    def test_incoming_call_exposes_controls_and_animation_without_selecting_agent(self):
        token = self.acquire(self.a)
        self.notify(self.a, token)
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.active)
        self.assertEqual(self.b.current_call, 1)
        self.assertIs(self.b.pending_target(), self.b.slots[1])
        self.assertIn('A', self.renderer.frame(self.b).payload.colors)
        self.b.set_active(True)
        routes = routes_for(self.b, self.profile)
        self.assertEqual(routes[30].token, token)
        self.assertEqual(routes[31].token, token)
        self.assertNotIn(40, routes)
        self.assertEqual(self.renderer.frame(self.b).payload.colors['SCREENSHOT'], Srgb8(0, 255, 0))
        self.physical('mute')
        self.assertIsNone(self.b.selected)
        self.assertEqual(self.b.slots[1].notification.status, 'muted')
        self.assertIsNone(self.b.pending_target())
        self.assertFalse({30, 31} & routes_for(self.b, self.profile).keys())
        self.b.select_slot(token)
        self.assertEqual(self.b.selected, 1)
        self.assertIsNone(self.b.pending_target())

    def test_new_call_bypasses_recent_slot_selection_and_shows_onset(self):
        for same_slot in (True, False):
            with self.subTest(same_slot=same_slot):
                self.setUp()
                first, second = self.acquire(self.a), self.acquire(self.c)
                self.b.set_active(True)
                self.b.select_slot(first)
                self.assertGreater(self.b.hold_until, self.clock())
                caller, token, number = (self.a, first, 1) if same_slot else (self.c, second, 2)
                self.notify(caller, token)
                self.assertEqual(self.b.current_call, number)
                self.assertEqual(self.b.slots[number].notification.status, 'active')
                self.assertIsNone(self.b.background_slot)
                self.assertEqual(self.b.pending_target().slot_token, token)
                self.assertIn('A', self.renderer.frame(self.b).payload.colors)
                self.assertEqual(routes_for(self.b, self.profile)[31].token, token)

    def test_slot_browsing_keeps_pending_call_controls_available(self):
        ringing = self.acquire(self.a)
        other = self.acquire(self.c)
        self.notify(self.a, ringing)
        self.b.set_active(True)
        self.b.select_slot(other)
        self.assertEqual(self.b.selected, 2)
        self.assertEqual(self.b.background_slot, 2)
        self.assertEqual(self.b.current_call, 1)
        self.assertEqual(self.b.slots[1].notification.status, 'active')
        routes = routes_for(self.b, self.profile)
        self.assertTrue({1, 2, 30, 31}.issubset(routes))
        self.assertEqual(routes[30].token, ringing)
        self.assertEqual(routes[31].token, ringing)
        self.assertEqual(self.renderer.frame(self.b).payload.colors['SCREENSHOT'], Srgb8(0, 255, 0))
        self.b.exit_focus(other)
        self.assertEqual(self.b.pending_target().slot_token, ringing)
        self.assertEqual(routes_for(self.b, self.profile)[31].token, ringing)
        self.b.select_slot(other)
        self.physical('mute')
        self.b.select_slot(other)
        self.assertIsNone(self.b.pending_target())
        self.assertEqual(self.b.selected, 2)

    def test_selecting_another_pending_call_picks_it_up_and_releases_controls(self):
        first, second = self.acquire(self.a), self.acquire(self.c)
        self.notify(self.a, first)
        self.notify(self.c, second)
        self.b.set_active(True)
        self.b.select_slot(second)
        routes = routes_for(self.b, self.profile)
        self.assertTrue({1, 2, 40}.issubset(routes))
        self.assertFalse({30, 31} & routes.keys())
        self.assertEqual(self.b.slots[2].notification.status, 'picked_up')
        self.assertTrue(self.b.slots[2].notification.controls_dismissed)
        self.assertEqual(self.b.slots[1].notification.status, 'queued')

    def test_direct_f_pickup_holds_question_until_answer_and_preserves_other_call(self):
        first, second = self.acquire(self.a), self.acquire(self.c)
        self.question(self.a, first)
        self.notify(self.c, second)
        self.b.set_active(True)
        routes = routes_for(self.b, self.profile)
        dispatch_event(self.b, self.edge(routes[1], 1, 7), routes, 7)
        self.assertTrue(self.b.slots[1].question.picked_up)
        self.assertEqual(self.b.slots[1].notification.status, 'picked_up')
        self.assertFalse({30, 31} & routes_for(self.b, self.profile).keys())
        self.assertEqual(list(self.b.focus_requests), [(first, self.a.thread_id)])
        self.clock.advance(20); self.b.step()
        self.assertIsNone(self.b.current_call)
        self.assertEqual(self.b.slots[2].notification.status, 'queued')
        self.call(self.a, 'clear_question', slot_token=first, question_id='q1', outcome='answered')
        self.assertEqual(self.b.current_call, 2)

    def test_escape_starts_red_only_with_focus_exit_and_clears_on_exit_or_disable(self):
        token = self.acquire(self.a)
        self.b.set_active(True)
        self.assertNotIn('ESC', self.renderer.frame(self.b).payload.colors)
        self.b.select_slot(token)
        for elapsed in (0, 14):
            self.clock.now = elapsed
            self.assertIn(40, routes_for(self.b, self.profile))
            color = self.renderer.frame(self.b).payload.colors['ESC']
            self.assertGreater(color.red, 0)
            self.assertEqual((color.green, color.blue), (0, 0))
        self.b.config = BrokerConfig(escape_exits_focus=False)
        self.assertNotIn(40, routes_for(self.b, self.profile))
        self.assertNotIn('ESC', self.renderer.frame(self.b).payload.colors)
        self.b.config = BrokerConfig()
        self.b.exit_focus(token)
        self.assertNotIn('ESC', self.renderer.frame(self.b).payload.colors)

    def test_incoming_off_page_call_after_escape_needs_no_slot_selection(self):
        first = self.acquire(self.a)
        for number in range(11):
            self.acquire(Caller(f'background-{number}'))
        other = self.acquire(self.c)
        self.b.set_active(True)
        self.b.select_slot(first)
        self.b.exit_focus(first)
        self.notify(self.c, other)
        self.assertIsNone(self.b.selected)
        self.assertEqual(self.b.current_call, 13)
        self.assertEqual(self.b.page, 0)
        self.assertEqual(routes_for(self.b, self.profile)[31].token, other)
        self.physical('pickup')
        self.assertEqual((self.b.page, self.b.selected), (1, 13))

    def test_pause_breathes_in_saturation_at_fixed_value_without_modulating_background(self):
        self.b.set_active(True)
        for percent in (0, 25, 50, 100):
            self.b.set_background_brightness(percent)
            backgrounds, pause_values, pause_saturations = set(), set(), set()
            for i in range(16):
                self.clock.now = i / 8
                frame = self.renderer.frame(self.b).payload
                peak = max(channel for color in (frame.background, *frame.colors.values())
                           for channel in (color.red, color.green, color.blue))
                # The legacy firmware applies this frame-peak normalization.
                backgrounds.add((frame.background.red * 160 + peak // 2) // peak)
                hsv = to_hsv8(frame.colors['PAUSE'])
                pause_values.add(hsv.value)
                pause_saturations.add(hsv.saturation)
            self.assertEqual(len(backgrounds), 1)
            self.assertEqual(len(pause_values), 1)
            self.assertGreater(len(pause_saturations), 1)
            self.assertIn(0, pause_saturations)
            self.assertIn(255, pause_saturations)

    def test_routine_slots_are_static_while_notification_animation_remains(self):
        token = self.acquire(self.a)
        for state in ('idle', 'progressing', 'error', 'action_requested', 'completed'):
            self.call(self.a, 'set_slot_state', slot_token=token, state=state)
            first = self.renderer.frame(self.b).payload
            self.clock.advance(.73)
            second = self.renderer.frame(self.b).payload
            self.assertEqual(first, second)
        self.notify(self.a, token)
        self.clock.advance(2)
        first = self.renderer.frame(self.b).payload.colors['A']
        self.clock.advance(.73)
        self.assertNotEqual(first, self.renderer.frame(self.b).payload.colors['A'])

    def test_escape_restores_background_and_preserves_pending_question_and_mode(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.b.set_active(True)
        self.physical('pickup')
        routes = routes_for(self.b, self.profile)
        self.assertEqual(routes[40].control, ControlId.key(*self.profile.element_by_id['ESC'].matrix))
        dispatch_event(self.b, self.edge(routes[40], 40, 7), routes, 7)
        self.assertTrue(self.b.active)
        self.assertIsNone(self.b.focused_slot())
        self.assertIsNone(self.b.background_slot)
        self.assertIsNotNone(self.b.slots[1].question)
        self.assertFalse(self.b.slots[1].question.picked_up)
        self.assertNotIn(40, routes_for(self.b, self.profile))
        frame = self.renderer.frame(self.b).payload
        for key in ('A', '1', '2', '3', '4', 'ENTER'):
            self.assertNotIn(key, frame.colors)
        self.assertFalse(self.b.focus_requests)
        self.b.select_slot(token)
        self.assertIsNone(self.b.pending_target())
        self.assertTrue(self.b.focus_requests)
        self.assertIsNotNone(self.b.slots[1].question)

    def test_old_escape_cannot_leave_new_focus_and_setting_can_disable_capture(self):
        one, two = self.acquire(self.a), self.acquire(self.c)
        self.b.set_active(True)
        self.b.select_slot(one)
        old = routes_for(self.b, self.profile)
        self.b.select_slot(two)
        dispatch_event(self.b, self.edge(old[40], 40, 7), old, 7)
        self.assertEqual(self.b.selected, 2)
        self.b.config = BrokerConfig(escape_exits_focus=False)
        self.assertNotIn(40, routes_for(self.b, self.profile))
        with self.assertRaises(ValueError):
            BrokerConfig(escape_exits_focus='yes')

    def test_skipped_or_expired_question_restores_status_lighting_and_bindings(self):
        for outcome in ('skipped', 'expired', 'timer'):
            with self.subTest(outcome=outcome):
                self.setUp()
                token = self.acquire(self.a)
                self.call(self.a, 'set_slot_state', slot_token=token, state='progressing', progress=.4)
                self.question(self.a, token)
                self.b.set_active(True)
                self.physical('pickup')
                if outcome == 'timer':
                    self.clock.advance(self.b.config.question_seconds + 1)
                    self.b.step()
                else:
                    self.call(self.a, 'clear_question', slot_token=token, question_id='q1', outcome=outcome)
                slot = self.b.slots[1]
                self.assertIsNone(slot.question)
                self.assertEqual((slot.state, slot.progress), ('progressing', .4))
                self.assertIsNone(self.b.selected)
                self.assertIsNone(self.b.background_slot)
                self.assertFalse(self.b.focus_requests)
                self.assertNotIn(40, routes_for(self.b, self.profile))
                frame = self.renderer.frame(self.b).payload
                for key in ('A', '1', '2', '3', '4', 'ENTER'):
                    self.assertNotIn(key, frame.colors)
                self.assertTrue(self.b.active)

    def test_initial_white_and_routine_saturation_notification_exemption(self):
        self.assertEqual(self.renderer.frame(self.b).payload.background, Srgb8(128, 128, 128))
        self.assertEqual(dict(self.renderer.frame(self.b).payload.colors), {})
        token = self.acquire(self.a)
        self.call(self.a, "set_slot_state", slot_token=token, state="progressing")
        inactive = self.renderer.frame(self.b).payload.colors["F1"]
        self.b.set_active(True)
        active = self.renderer.frame(self.b).payload.colors["F1"]
        self.assertEqual(inactive, saturation(active, .75))
        self.notify(self.a, token)
        self.clock.advance(.5)
        active = self.renderer.frame(self.b).payload.colors["A"]
        self.b.set_active(False)
        self.assertEqual(active, self.renderer.frame(self.b).payload.colors["A"])

    def test_question_keys_only_after_pickup_and_outlive_background(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.b.set_active(True)
        self.assertFalse(self.b.slots[1].question.picked_up)
        self.physical("pickup")
        self.clock.advance(self.b.config.background_seconds + self.b.config.fade_seconds + 1)
        self.b.step()
        frame = self.renderer.frame(self.b).payload
        self.assertNotIn("A", frame.colors)
        self.assertEqual(frame.colors["4"], ATTENTION)
        self.assertEqual(frame.colors["ENTER"], ATTENTION)
        self.assertEqual({x["key"] for x in self.b.question_keys(self.b.slots[1])}, {"1", "2", "3", "4", "ENTER"})
        controls = [r.control for r in routes_for(self.b, self.profile).values()]
        for key in ("1", "2", "3", "4", "ENTER", "SCREENSHOT"):
            self.assertNotIn(ControlId.key(*self.profile.element_by_id[key].matrix), controls)
        self.b.set_active(False)
        frame = self.renderer.frame(self.b).payload
        self.assertNotIn("4", frame.colors)

    def test_pause_keeps_saturation_breath_while_scroll_mute_is_solid_red(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        peak = self.renderer.frame(self.b).payload.colors["PAUSE"]
        self.clock.advance(1)
        trough = self.renderer.frame(self.b).payload.colors["PAUSE"]
        self.assertEqual((peak, trough), (ATTENTION, WHITE))
        self.b.set_active(True)
        red_samples, pause_samples = [], []
        for _ in range(3):
            frame = self.renderer.frame(self.b).payload
            red_samples.append(frame.colors['SCROLL_LOCK'])
            pause_samples.append(frame.colors['PAUSE'])
            self.clock.advance(.5)
        self.assertEqual(len(set(red_samples)), 1)
        self.assertGreater(len(set(pause_samples)), 1)
        self.assertEqual({to_hsv8(c).value for c in pause_samples}, {255})
        self.assertTrue(all(c.green >= c.red for c in pause_samples))
        self.assertGreater(red_samples[0].red, 0)
        self.assertEqual((red_samples[0].green, red_samples[0].blue), (0, 0))
        self.physical('mute')
        color = self.renderer.frame(self.b).payload.colors["PAUSE"]
        self.assertNotEqual(color, red_samples[0])
        self.assertNotIn('SCROLL_LOCK', self.renderer.frame(self.b).payload.colors)
        self.assertNotIn('SCREENSHOT', self.renderer.frame(self.b).payload.colors)
        self.assertGreater(color.green, color.red)
        self.assertEqual(to_hsv8(color).value, 128)
        self.clock.advance(.5)
        later = self.renderer.frame(self.b).payload.colors['PAUSE']
        self.assertNotEqual(later, color)
        self.assertEqual(to_hsv8(later).value, to_hsv8(color).value)

    def test_host_background_brightness_does_not_dim_status_notifications_or_question_hints(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.clock.advance(8)
        baseline = self.renderer.frame(self.b).payload
        for percent, value in ((0, 0), (25, 64), (50, 128), (100, 255)):
            self.b.set_background_brightness(percent)
            frame = self.renderer.frame(self.b).payload
            self.assertEqual(frame.background, Srgb8(value, value, value))
            for key in ('F1', 'PAUSE', 'A'):
                self.assertEqual(frame.colors[key], baseline.colors[key])
        self.b.set_active(True)
        self.physical('pickup')
        self.clock.advance(self.b.config.background_seconds + self.b.config.fade_seconds + 1)
        self.b.set_background_brightness(0)
        frame = self.renderer.frame(self.b).payload
        self.assertEqual(frame.background, Srgb8(0, 0, 0))
        self.assertNotIn('A', frame.colors)
        self.assertEqual(frame.colors['4'], ATTENTION)
        self.assertNotEqual(frame.colors['1'], Srgb8(0, 0, 0))
        for bad in (-1, 101, True, float('nan')):
            with self.assertRaises(ValueError): self.b.set_background_brightness(bad)

    def edge(self, route, binding, generation):
        return DeviceEvent(event_type=EventType.CONTROL_EDGE, session_token=1, sequence=1,
            binding_generation=generation, binding_id=binding, control_id=route.control,
            edge_or_state=int(Edge.UP), flags=EventFlags(0), timestamp_ms=0)

    def test_page_flip_invalidates_old_release_but_preserves_all_knob_detents(self):
        for i in range(25): self.acquire(Caller(str(i)))
        self.b.set_active(True)
        routes = routes_for(self.b, self.profile)
        first = routes[1]
        for _ in range(2):
            dispatch_event(self.b, self.edge(routes[21], 21, 8), routes, 8)
        self.assertEqual(self.b.page, 2)
        dispatch_event(self.b, self.edge(first, 1, 8), routes, 8)
        self.assertIsNone(self.b.selected)
        new_routes = routes_for(self.b, self.profile)
        dispatch_event(self.b, self.edge(new_routes[1], 1, 8), new_routes, 9)
        self.assertIsNone(self.b.selected)
        dispatch_event(self.b, self.edge(new_routes[1], 1, 9), new_routes, 9)
        self.assertEqual(self.b.selected, 25)

    def test_stale_call_action_cannot_target_replacement(self):
        token = self.acquire(self.a)
        self.question(self.a, token)
        self.b.set_active(True)
        old_id = self.b.slots[1].notification.notification_id
        self.question(self.a, token, "q2")
        self.b.act_on_call("pickup", token, old_id)
        self.assertFalse(self.b.slots[1].question.picked_up)


if __name__ == "__main__":
    unittest.main()
