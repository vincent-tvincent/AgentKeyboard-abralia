# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from unittest.mock import Mock

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.render import Renderer, SORT_HIGHLIGHT, within_frame_peak
from abralia.interaction import DeviceEvent, EventType, EventFlags, Edge, ControlId
from abralia.rgb import load_profile

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class SortInputTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.b = Broker(clock=lambda: self.now)
        self.profile = load_profile(PROFILE)
        for i, project in enumerate(('A', 'B', 'A', 'B')):
            self.b.call(Caller(f'agent:{i}', project_id=project), 'acquire_slot',
                        {'label': f'{project}{i}', 'idempotency_key': 'acquire'})
        self.b.set_active(True)

    def edge(self, route, generation=7, binding=90, edge=Edge.UP):
        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation, binding,
                           route.control, int(edge), EventFlags(0), 0)

    def test_sort_uses_physical_escape_only_in_navigation(self):
        self.assertNotIn(90, routes_for(self.b, self.profile))
        self.b.toggle_navigation()
        routes = routes_for(self.b, self.profile)
        route = routes[90]
        self.assertEqual(route.control, ControlId.key(*self.profile.element_by_id['ESC'].matrix))
        self.assertNotIn(40, routes)
        dispatch_event(self.b, self.edge(route), routes, 7)
        self.assertEqual(self.b.sort_policy, 'project')
        # A second release captured before the rearrangement is obsolete.
        dispatch_event(self.b, self.edge(route), routes, 7)
        self.assertEqual(self.b.sort_policy, 'project')
        fresh = routes_for(self.b, self.profile)
        dispatch_event(self.b, self.edge(fresh[90], 6), fresh, 7)
        self.assertEqual(self.b.sort_policy, 'project')
        dispatch_event(self.b, self.edge(fresh[90]), fresh, 7)
        self.assertEqual(self.b.sort_policy, 'incoming')
        self.b.set_active(False)
        self.assertNotIn(90, routes_for(self.b, self.profile))

    def test_driver_invokes_durable_commit_before_any_rearrangement(self):
        self.b.toggle_navigation()
        commit = Mock(return_value=False)
        driver = DeviceDriver(self.b, PROFILE, 'simulated', sort_committer=commit)
        driver.routes = driver.build_routes(); driver.generation = 7
        route = driver.routes[90]
        driver.handle_event(self.edge(route, edge=Edge.DOWN))
        driver.handle_event(self.edge(route, generation=6))
        commit.assert_not_called()
        driver.handle_event(self.edge(route))
        commit.assert_called_once_with()
        self.assertEqual(self.b.sort_policy, 'incoming')

    def test_violet_sort_hint_does_not_fade_with_selected_background(self):
        slot = next(iter(self.b.slots.values()))
        self.b.select_slot(slot.slot_token)
        self.b.toggle_navigation()
        self.now = 12
        self.b._navigation_activity()
        self.now = 21  # Main-area fade is over; navigation is still active.
        self.b.step()
        frame = Renderer(self.profile).frame(self.b).payload
        self.assertEqual(frame.colors['ESC'], within_frame_peak(SORT_HIGHLIGHT, frame.background.red))
        self.now = 22
        self.assertEqual(Renderer(self.profile).frame(self.b).payload.colors['ESC'], frame.colors['ESC'])
        self.b.disarm_navigation('hold')
        self.assertNotIn(90, routes_for(self.b, self.profile))
        self.assertEqual(routes_for(self.b, self.profile)[40].action, 'exit_focus')

    def test_timeout_discards_old_sort_binding_and_restores_page_knob(self):
        self.b.toggle_navigation()
        routes = routes_for(self.b, self.profile)
        self.now = 16
        self.b.step()
        dispatch_event(self.b, self.edge(routes[90]), routes, 7)
        self.assertEqual(self.b.sort_policy, 'incoming')
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertNotIn(90, routes_for(self.b, self.profile))


if __name__ == '__main__':
    unittest.main()
