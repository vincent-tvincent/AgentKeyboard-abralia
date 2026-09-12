# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from abralia.backend.core import BrokerConfig
from abralia.backend.device import Route, dispatch_event
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8, load_profile
from knob_paging_demo import FixtureRenderer, dry_run, prepare, run

PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"
COLORS = {"idle": "FFFFFF", "progressing": "2080FF", "completed": "20FF60",
          "error": "FFB020", "action_requested": "FFB020"}


class KnobDemoTests(unittest.TestCase):
    def test_three_pages_are_distinct_and_last_page_has_one_fixture(self):
        b, _ = prepare(BrokerConfig(colors=COLORS))
        renderer = FixtureRenderer(load_profile(PROFILE))
        b.set_active(True)
        first = renderer.frame(b).payload
        self.assertEqual(first.colors['F1'], Srgb8(16, 64, 128))
        self.assertEqual(first.colors['F12'], first.colors['F1'])
        b.turn_page(1)
        second = renderer.frame(b).payload
        self.assertEqual(second.colors['F1'], Srgb8(16, 128, 48))
        b.turn_page(1)
        third = renderer.frame(b).payload
        self.assertEqual(third.colors['F1'], Srgb8(128, 88, 16))
        self.assertEqual(third.colors['F2'], Srgb8(64, 64, 64))
        self.assertEqual(third.background, Srgb8(128, 128, 128))
        self.assertIsNone(b.selected)

    def test_dry_run_exercises_real_routing_without_opening_hardware(self):
        with patch('abralia.backend.device.SharedRawHidSession.open_profile', side_effect=AssertionError('HID opened')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = dry_run(PROFILE, BrokerConfig(colors=COLORS))
        self.assertEqual(result, 0)
        self.assertIn('SIMULATED ONLY', output.getvalue())
        self.assertIn('"first_page_boundary_received": true', output.getvalue())
        self.assertIn('"last_page_boundary_received": true', output.getvalue())

    def test_checker_detects_reversed_routing_and_unhandled_packets(self):
        b, trace = prepare()
        b.set_active(True)
        event = DeviceEvent(EventType.CONTROL_EDGE, 1, 1, 1, 21,
            ControlId.encoder_clockwise(0), int(Edge.UP), EventFlags(0), 0)
        trace.observe(event)
        self.assertFalse(trace.checks()['all_received_encoder_events_handled'])
        # Deliberately misroute a physical clockwise event counterclockwise.
        routes = {21: Route(event.control_id, 'previous_page')}
        dispatch_event(b, event, routes, 1)
        self.assertFalse(trace.checks()['one_page_per_received_detent'])
        trace.observe(event)
        self.assertFalse(trace.checks()['no_duplicate_event_sequences'])

    def test_interruption_closes_driver_and_does_not_claim_success(self):
        args = SimpleNamespace(profile=PROFILE, timeout=10, background_percent=50, dry_run=False, log=None)
        with patch('knob_paging_demo.DeviceDriver') as factory, contextlib.redirect_stdout(io.StringIO()) as output:
            driver = factory.return_value
            driver.profile = load_profile(PROFILE)
            driver.protocol = None
            driver.tick.side_effect = KeyboardInterrupt
            result = run(args)
        driver.close.assert_called_once()
        self.assertEqual(result, 130)
        self.assertIn('INCOMPLETE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
