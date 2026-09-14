# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from abralia.backend.render import Renderer, within_frame_peak
from abralia.interaction import DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import Srgb8
from keyboard_demo import (Commands, SORT_BINDING, SORT_COLOR, SortBroker, SortDriver,
                           apply_command, private_file, run)

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'


class KeyboardSortTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = SortBroker(clock=lambda: self.now)
        self.d = SortDriver(self.b, PROFILE, 'simulated')

    def update_routes(self):
        if self.d.generation:
            self.d.route_history[self.d.generation] = self.d.routes
        self.d.generation += 1
        self.d.routes = self.d.build_routes()

    def edge(self, binding, routes=None, generation=None, edge=Edge.UP):
        routes = self.d.routes if routes is None else routes
        generation = self.d.generation if generation is None else generation
        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation, binding,
                           routes[binding].control, int(edge), EventFlags(0), 0)

    def arm(self):
        self.b.set_active(True)
        self.b.toggle_navigation()
        self.update_routes()

    def test_initial_fixture_is_interleaved_and_has_no_native_focus_target(self):
        self.assertEqual([s['label'] for s in self.b.demo_snapshot()['slots']],
                         [f'{project}{i}' for i in range(1, 5) for project in 'ABC'])
        self.assertTrue(all(s.caller.surface == 'simulated' for s in self.b.slots.values()))
        self.b.set_active(True)
        self.b.select_slot(self.b.slots[2].slot_token)
        self.assertFalse(self.b.focus_requests)
        self.assertEqual(self.d.renderer.frame(self.b).payload.background, Srgb8(128, 128, 128))

    def test_escape_sort_roundtrip_preserves_ownership_selection_and_notice(self):
        self.b.set_active(True)
        self.b.select_slot(self.b.slots[2].slot_token)
        caller = self.b.slots[3]
        self.b.call(caller.caller, 'set_notification', {'slot_token': caller.slot_token,
                    'idempotency_key': 'notice', 'enabled': True})
        notice = caller.notification
        before = {s.slot_id: (s.slot_token, s.caller, s.identity_color) for s in self.b.slots.values()}
        self.b.toggle_navigation(); self.update_routes()
        self.d.handle_event(self.edge(SORT_BINDING))
        self.assertEqual([s.slot_id for s in self.b.visible_slots()], [1,4,7,10,2,5,8,11,3,6,9,12])
        self.assertEqual(self.b.sort_model.mode, 'project')
        self.assertEqual(self.b.selected, 2)
        self.assertIs(caller.notification, notice)
        self.assertEqual(notice.status, 'active')
        self.assertEqual(self.b.current_call, 3)
        for s in self.b.slots.values():
            self.assertEqual((s.slot_token, s.caller), before[s.slot_id][:2])
        self.update_routes()
        self.d.handle_event(self.edge(SORT_BINDING))
        self.assertEqual([s.slot_id for s in self.b.visible_slots()], list(range(1,13)))
        self.assertEqual({s.slot_id: s.identity_color for s in self.b.slots.values()},
                         {number: value[2] for number, value in before.items()})

    def test_escape_binding_is_physical_navigation_only_and_restores_focus_escape(self):
        self.b.set_active(True)
        self.b.select_slot(self.b.slots[1].slot_token)
        self.update_routes()
        self.assertEqual(self.d.routes[40].action, 'exit_focus')
        self.assertNotIn(SORT_BINDING, self.d.routes)
        self.b.toggle_navigation(); self.update_routes()
        self.assertNotIn(40, self.d.routes)
        self.assertEqual(self.d.routes[SORT_BINDING].control, self.d.sort_control)
        frame = self.d.renderer.frame(self.b).payload
        production = Renderer(self.d.profile).frame(self.b).payload
        peak = max(max(c.red,c.green,c.blue) for c in (production.background,*production.colors.values()))
        self.assertEqual(frame.colors['ESC'], within_frame_peak(SORT_COLOR, peak))
        self.assertEqual({k:v for k,v in frame.colors.items() if k != 'ESC'},
                         {k:v for k,v in production.colors.items() if k != 'ESC'})
        self.b.toggle_navigation(); self.update_routes()
        self.assertEqual(self.d.routes[40].action, 'exit_focus')
        self.assertEqual(self.d.renderer.frame(self.b).payload, Renderer(self.d.profile).frame(self.b).payload)

    def test_stale_escape_and_f_key_releases_cannot_apply_after_layout_or_arming_change(self):
        self.arm()
        old, generation = self.d.routes, self.d.generation
        self.d.handle_event(self.edge(SORT_BINDING, edge=Edge.DOWN))
        self.assertEqual(self.b.sort_model.mode, 'incoming')
        self.d.handle_event(self.edge(SORT_BINDING))
        self.d.handle_event(self.edge(SORT_BINDING, old, generation))
        self.assertEqual(self.b.sort_model.mode, 'project')
        self.d.handle_event(self.edge(2, old, generation))
        self.assertIsNone(self.b.selected)
        self.update_routes()
        self.d.handle_event(self.edge(2, old, generation))
        self.assertIsNone(self.b.selected)
        old, generation = self.d.routes, self.d.generation
        self.now = 15; self.b.step(); self.b.toggle_navigation()
        self.d.handle_event(self.edge(SORT_BINDING, old, generation))
        self.assertEqual(self.b.sort_model.mode, 'project')

    def test_grouped_new_arrival_moves_across_page_without_changing_id_or_cached_colors(self):
        self.arm(); self.b.toggle_sort()
        before = {s.slot_id: (s.slot_token, s.identity_color) for s in self.b.slots.values()}
        self.b.select_slot(self.b.slots[12].slot_token)
        selected = self.b.selected
        added = self.b.add_agent('A')
        self.assertEqual((added.label, self.b.slots[13].position), ('A5', 5))
        self.assertEqual((self.b.slots[12].position, self.b.page_count, self.b.page), (13, 2, 0))
        self.assertEqual(self.b.selected, selected)
        for number, value in before.items():
            self.assertEqual((self.b.slots[number].slot_token, self.b.slots[number].identity_color), value)
        self.b.cycle_knob(); self.b.rotate_knob(1)
        self.assertEqual(self.b.page, 1)
        self.b.toggle_sort()
        self.assertEqual(self.b.page, 1)
        self.assertEqual(self.b.slots[13].position, 13)

    def test_timeout_returns_knob_to_pages_and_disarms_sort_escape(self):
        self.b.add_agent('A'); self.arm()
        self.b.select_slot(self.b.slots[2].slot_token)
        self.now = 15; self.b.step(); self.update_routes()
        self.assertNotIn(SORT_BINDING, self.d.routes)
        self.assertFalse(self.b.navigation_active)
        self.assertEqual(self.b.knob_mode, 'pages')
        self.assertIsNone(self.b.cursor_token)
        self.assertIsNone(self.b.knob_selection_deadline)
        self.b.rotate_knob(1)
        self.assertEqual((self.b.page, self.b.selected, self.b.active), (1, 2, True))


class CommandAndCleanupTests(unittest.TestCase):
    def test_hardware_commands_only_add_or_stop_never_force_sort_or_activation(self):
        b = SortBroker()
        self.assertTrue(apply_command(b, {'command':'add','project':'B'}, 'hardware'))
        self.assertEqual(b.demo_snapshot()['slots'][-1]['label'], 'B5')
        for bad in ({'command':'toggle_sort'}, {'command':'activate'}, {'command':'add','project':'D'},
                    {'command':'stop','extra':True}, []):
            with self.assertRaises(ValueError): apply_command(b, bad, 'hardware')
        self.assertFalse(b.active)
        self.assertFalse(apply_command(b, {'command':'stop'}, 'hardware'))
        self.assertTrue(apply_command(b, {'command':'toggle_sort'}, 'simulated'))

    def test_private_append_commands_partial_lines_and_no_duplicate_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'commands.jsonl'
            with private_file(path) as stream:
                reader = Commands(stream)
                with path.open('ab') as writer:
                    writer.write(b'{"command":"add",'); writer.flush()
                    self.assertEqual(reader.poll(), [])
                    writer.write(b'"project":"A"}\n'); writer.flush()
                    self.assertEqual(reader.poll(), [{'command':'add','project':'A'}])
                    self.assertEqual(reader.poll(), [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.chmod(0o644)
            with self.assertRaises(ValueError): private_file(path)
            link = Path(directory) / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError): private_file(link)

    def test_simulated_loop_writes_safe_evidence_and_close_runs_on_start_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            args = argparse.Namespace(profile=PROFILE, mode='simulated', duration=.01,
                                      command_file=path/'commands', events=path/'events')
            with private_file(args.command_file) as stream:
                pass
            args.command_file.write_text('{"command":"add","project":"C"}\n')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run(args), 0)
            records = [json.loads(line) for line in args.events.read_text().splitlines()]
            self.assertEqual(records[-1]['kind'], 'closed')
            self.assertIsNone(records[-1]['cleanup_error'])
            layout = next(r for r in records if r['kind'] == 'layout')
            self.assertEqual(layout['slots'][-1]['label'], 'C5')
            self.assertNotIn('slot_token', args.events.read_text())
            self.assertEqual(args.events.stat().st_mode & 0o777, 0o600)
            seen = []
            class BrokenDriver(SortDriver):
                def start(self):
                    seen.append('start'); raise RuntimeError('busy')
                def close(self):
                    seen.append('close')
            args.events = path/'failed-events'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run(args, driver_factory=BrokenDriver), 1)
            self.assertEqual(seen, ['start', 'close'])


if __name__ == '__main__':
    unittest.main()
