# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from abralia.backend.core import BrokerConfig, Caller
from abralia.backend.render import ATTENTION
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8
from knob_overview_demo import DEFAULT_ENTER_COLOR, OverviewDriver, OverviewTrace, main, prepare, run

PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


class OverviewDemoTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.broker = prepare(BrokerConfig(background_brightness_percent=25), clock=lambda: self.now[0])
        self.driver = OverviewDriver(self.broker, PROFILE, OverviewTrace())
        self.sequence = 0

    def event(self, binding=0, *, active=None, routes=None, generation=None):
        self.sequence += 1
        generation = self.sequence if generation is None else generation
        routes = self.driver.build_routes() if routes is None else routes
        self.driver.routes, self.driver.generation = routes, generation
        return (DeviceEvent(EventType.MODE_CHANGED, 1, self.sequence, generation, 0, ControlId(0), int(active), EventFlags(0), 0)
                if active is not None else DeviceEvent(EventType.CONTROL_EDGE, 1, self.sequence, generation, binding,
                                                      routes[binding].control, int(Edge.UP), EventFlags(0), 0))

    def send(self, binding=0, **kwargs):
        self.driver.handle_event(self.event(binding, **kwargs))

    def test_escape_leaves_confirmation_preview_and_restores_overview(self):
        self.send(active=True)
        self.send(22)
        self.send(32)
        self.assertEqual(self.broker.selected, 1)
        self.send(40)
        self.assertTrue(self.broker.active)
        self.assertIsNone(self.broker.selected)
        self.assertIsNone(self.broker.confirmed_at)
        self.assertEqual(self.broker.knob_mode, 'pages')
        self.assertNotIn('ENTER', self.driver.renderer.frame(self.broker).payload.colors)

    def test_two_functions_and_holes_with_no_commit_until_enter(self):
        self.send(active=True)
        self.send(21)
        self.assertEqual(self.broker.page, 1)
        self.send(20)
        self.send(22)
        self.assertEqual(self.broker.candidate().slot_id, 1)
        for _ in range(3):
            self.send(21)
        self.assertEqual(self.broker.candidate().slot_id, 5)  # Released F4 is skipped.
        self.assertIsNone(self.broker.selected)
        self.assertIsNone(self.broker.background_slot)
        self.assertEqual(self.broker.page, 0)
        self.send(32)
        self.assertEqual(self.broker.selected, 5)
        self.assertEqual(self.broker.background_slot, 5)
        self.assertNotIn(32, self.driver.build_routes())
        self.assertFalse(self.broker.focus_requests)

    def test_bounds_single_slot_page_and_direct_f_confirmation(self):
        self.send(active=True)
        for _ in range(5):
            self.send(21)
        self.assertEqual(self.broker.page, 2)
        self.send(22)
        for binding in (20, 21, 21, 20):
            self.send(binding)
            self.assertEqual(self.broker.candidate().slot_id, 25)
            self.assertEqual(self.broker.page, 2)
        self.send(1)
        self.assertEqual(self.broker.selected, 25)
        self.now[0] += 4
        self.broker.step()
        self.assertEqual(self.broker.knob_mode, "pages")
        self.assertIsNone(self.broker.selected)
        for _ in range(5):
            self.send(20)
        self.assertEqual(self.broker.page, 0)

    def test_confirm_is_bound_only_for_a_valid_active_candidate(self):
        self.assertNotIn(32, self.driver.build_routes())
        self.send(active=True)
        self.assertNotIn(32, self.driver.build_routes())
        self.send(22)
        self.assertIn(32, self.driver.build_routes())
        self.send(22)
        self.assertNotIn(32, self.driver.build_routes())
        self.send(22)
        for slot in list(self.broker.visible_slots()):
            self.broker._release(slot)
        self.assertNotIn(32, self.driver.build_routes())
        self.assertNotIn("ENTER", self.driver.renderer.frame(self.broker).payload.colors)
        self.send(active=False)
        self.assertNotIn(32, self.driver.build_routes())

    def test_stale_enter_cursor_and_reused_allocation_never_retarget(self):
        self.send(active=True)
        self.send(22)
        old_enter = self.event(32)
        self.broker.rotate(1)
        self.driver.handle_event(old_enter)
        self.assertIsNone(self.broker.selected)
        self.assertEqual(self.driver.trace.rows[-1]["action"], "stale_candidate")
        self.broker.rotate(-1)
        old_enter = self.event(32)
        self.broker._release(self.broker.candidate())
        caller = Caller("new-fixture")
        result = self.broker.call(caller, "acquire_slot", {"label": "new", "idempotency_key": "acquire"})
        self.assertEqual(result["allocation"]["slot_id"], 1)
        self.driver.handle_event(old_enter)
        self.assertIsNone(self.broker.selected)

    def test_delayed_f_release_after_page_change_and_generation_change_is_ignored(self):
        self.send(active=True)
        old_f = self.event(2)
        self.broker.rotate(1)
        self.driver.handle_event(old_f)
        self.assertIsNone(self.broker.selected)
        self.assertEqual(self.driver.trace.rows[-1]["action"], "stale_page")
        old_f = self.event(2)
        self.driver.generation += 1
        self.driver.handle_event(old_f)
        self.assertIsNone(self.broker.selected)
        self.assertEqual(self.driver.trace.rows[-1]["action"], "stale_generation")

    def test_burst_detents_and_centre_click_follow_received_order(self):
        self.send(active=True)
        routes = self.driver.build_routes()
        for binding in (21, 21, 20, 22, 21, 21, 20):
            self.send(binding, routes=routes, generation=1)
        self.assertEqual(self.broker.page, 1)
        self.assertEqual(self.broker.candidate().slot_id, 14)
        self.assertIsNone(self.broker.selected)

    def test_lighting_encodes_mode_candidate_enter_and_cleanup(self):
        self.send(active=True)
        page = self.driver.renderer.frame(self.broker).payload
        self.assertEqual(page.background, Srgb8(64, 64, 64))
        self.assertEqual(page.colors["F4"], Srgb8(32, 32, 32))
        self.assertNotIn("ENTER", page.colors)
        self.send(22)
        selection = self.driver.renderer.frame(self.broker).payload
        self.assertEqual(selection.background, page.background)
        self.assertEqual(selection.colors["F4"], Srgb8(18, 17, 20))
        self.assertEqual(selection.colors["ENTER"], Srgb8(40, 64, 0))
        # The shared renderer now adds the accepted selection whitening and
        # value emphasis, dimming neighbours when the frame has no headroom.
        peak = lambda color: max(color.red, color.green, color.blue)
        candidate = selection.colors['F1']
        self.assertGreater(candidate.green, candidate.red)
        self.assertGreater(candidate.red, candidate.blue)
        self.assertLessEqual(peak(candidate), 64)
        self.assertGreaterEqual(peak(candidate), peak(selection.colors['F2']) * 1.3)
        self.send(21)
        moved = self.driver.renderer.frame(self.broker).payload
        self.assertGreater(moved.colors['F1'].blue, moved.colors['F1'].green)
        self.assertLess(peak(moved.colors['F1']), peak(moved.colors['F2']))
        self.send(32)
        confirmed = self.driver.renderer.frame(self.broker).payload.colors
        self.assertEqual(confirmed["ENTER"], confirmed["A"])
        self.assertNotEqual(confirmed["ENTER"], ATTENTION)
        self.send(active=False)
        inactive = self.driver.renderer.frame(self.broker).payload
        self.assertEqual(inactive.background, page.background)
        self.assertNotIn("ENTER", inactive.colors)

    def test_device_worker_calls_experimental_routes_and_dispatch(self):
        self.broker.delivery = "ready"
        self.driver.interaction = MagicMock()
        self.driver.interaction.replace_bindings.return_value.binding_generation = 1
        self.driver.protocol = MagicMock()
        self.driver.rgb = MagicMock()
        mode = self.event(active=True)
        self.driver.routes = {}
        self.driver.protocol.service.return_value = [mode]
        self.driver.tick()
        self.assertTrue(self.broker.active)
        bound = list(self.driver.interaction.replace_bindings.call_args.args[0])
        self.assertIn(22, [binding.entry.binding_id for binding in bound])
        self.assertEqual(self.driver.trace.rows[-1]["action"], "mode")

    def test_dry_run_never_opens_hardware(self):
        with patch("abralia.backend.device.SharedRawHidSession.open_profile", side_effect=AssertionError("opened HID")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(["--profile", PROFILE, "--dry-run"])
        self.assertEqual(result, 0)
        self.assertIn('"enter_confirmed": true', output.getvalue())

    def test_interruption_still_restores_device(self):
        args = SimpleNamespace(profile=PROFILE, timeout=10, background_percent=25, dry_run=False, log=None,
                               selection_tint="DDD2FF", selection_background_percent=16, highlight_percent=85,
                               enter_color=DEFAULT_ENTER_COLOR, selection_background_scope="full")
        with patch("knob_overview_demo.OverviewDriver") as factory, contextlib.redirect_stdout(io.StringIO()):
            driver = factory.return_value
            driver.protocol = None
            driver.tick.side_effect = KeyboardInterrupt
            result = run(args)
        self.assertEqual(result, 130)
        driver.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
