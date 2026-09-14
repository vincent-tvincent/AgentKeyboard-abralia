# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abralia_simulator.engine import Simulator
from abralia_simulator.export import capture_scenario, export_scenario, export_design, render_snapshot, _palette, _quantize
from abralia_simulator.scenarios import SCENARIOS, get_scenario


class ScenarioTests(unittest.TestCase):
    def test_gap_story_uses_production_compaction_and_preserves_remaining_ids(self):
        result = capture_scenario('gap-close', fps=3)
        final = result['frames'][-1]['snapshot']
        self.assertFalse(result['truncated'])
        self.assertEqual(final['page_count'], 1)
        self.assertEqual(len(final['agents']), 12)
        expected = {'a1', *(f'a{i}' for i in range(6,17))}
        self.assertEqual({agent['id'] for agent in final['agents']}, expected)
        self.assertEqual(sorted(agent['display_position'] for agent in final['agents']), list(range(1,13)))

    def test_frame_limit_is_explicit_and_does_not_look_like_a_completed_scenario(self):
        result = capture_scenario('mode-navigation', fps=12, max_frames=3)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['frames']), 3)
        self.assertIn('incomplete', result['frames'][-1]['caption'])
        self.assertLess(result['frames'][-1]['progress'], 1)

    def test_same_seed_and_clock_give_the_same_led_frames(self):
        first = capture_scenario('keyboard-guide', fps=2)
        second = capture_scenario('keyboard-guide', fps=2)
        self.assertEqual(first['frames'], second['frames'])

    def test_all_builtin_designs_load_and_unknown_scenarios_are_rejected(self):
        simulator = Simulator()
        for name in SCENARIOS:
            with self.subTest(name=name):
                simulator.load_design(get_scenario(name)['design'])
        with self.assertRaises(ValueError):
            capture_scenario('not-a-scenario', max_frames=1)

    def test_nonencoder_profiles_get_real_navigation_keys_and_correct_mode_label(self):
        simulator = Simulator(profile='builtin:keychron-v3-ansi-effect25')
        for name in ('mode-navigation', 'paging-sorting'):
            scenario = get_scenario(name, simulator.profile)
            for entry in scenario['design']['timeline']:
                self.assertFalse(entry['action'].get('key', '').startswith('KNOB'))
            self.assertFalse(any('Pause' in segment['caption'] for segment in scenario['segments']))
            simulator.load_design(scenario['design'])
        result = capture_scenario('mode-navigation', profile='builtin:keychron-v3-ansi-effect25', fps=1, max_frames=10)
        self.assertFalse(result['frames'][-1]['snapshot']['active'])

    def test_long_intervals_are_explicit_time_jumps(self):
        for name in ('mode-navigation','notifications','attention'):
            for segment in get_scenario(name)['segments']:
                if segment['jump']:
                    self.assertIn('later', segment['caption'])

    def test_json_examples_are_valid_engine_designs(self):
        simulator = Simulator()
        for path in (Path(__file__).resolve().parents[1] / 'examples').glob('*.json'):
            with self.subTest(path=path.name):
                simulator.load_design(json.loads(path.read_text()))
                simulator.advance(2)


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is an optional exporter dependency')
class ImageExportTests(unittest.TestCase):
    def setUp(self):
        self.simulator = Simulator()
        self.snapshot = self.simulator.snapshot()

    def test_png_contains_actual_led_rgb_and_monochrome_header(self):
        self.simulator.load_design({'version':1,'name':'Test RGB','frames':[{'at':0,'colors':{'A':'1435CC'}}]})
        snapshot = self.simulator.snapshot()
        image = render_snapshot(snapshot, caption='A is the supplied LED value.', width=960)
        key = next(key for key in snapshot['keys'] if key['id']=='A')
        span = max(item['x']+item['w'] for item in snapshot['keys'])
        scale = (960-54)/span
        x, y = round(27+key['x']*scale+10), round(116+key['y']*scale+10)
        self.assertEqual(image.getpixel((x,y)), (0x14,0x35,0xCC))
        red, green, blue = image.crop((0,0,image.width,110)).split()
        self.assertEqual(red.tobytes(), green.tobytes())
        self.assertEqual(green.tobytes(), blue.tobytes())

    def test_palette_is_global_and_keeps_non_led_ui_monochrome(self):
        frames = capture_scenario('keyboard-guide', fps=2)['frames']
        palette = _palette(frames)
        for frame in (frames[0], frames[-1]):
            indexed = _quantize(render_snapshot(**frame), palette)
            self.assertEqual(indexed.getpalette(), palette.getpalette())
            red, green, blue = indexed.convert('RGB').crop((0,0,indexed.width,110)).split()
            self.assertEqual(red.tobytes(), green.tobytes())
            self.assertEqual(green.tobytes(), blue.tobytes())

    def test_unknown_action_key_and_invalid_nonled_color_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown physical'):
            render_snapshot(self.snapshot, action_keys=('imaginary-key',))
        snapshot = deepcopy(self.snapshot)
        next(key for key in snapshot['keys'] if key['id']=='KNOB_PRESS')['led'] = 'FF0000'
        with self.assertRaisesRegex(ValueError, 'Non-RGB'):
            render_snapshot(snapshot)
        with self.assertRaises(ValueError):
            render_snapshot(self.snapshot, width=100000)

    def test_gif_and_still_export_are_bounded_and_readable(self):
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix='abralia-export-') as root:
            gif = Path(root) / 'preview.gif'
            result = export_scenario('keyboard-guide', gif, fps=3, width=800, max_frames=6)
            self.assertTrue(result['truncated'])
            self.assertEqual(result['frames'], 6)
            with Image.open(gif) as image:
                self.assertEqual(image.width,800)
                self.assertGreater(image.n_frames,1)
                self.assertLessEqual(image.n_frames,6)
            png = Path(root) / 'still.png'
            export_scenario('keyboard-guide', png, fps=2, max_frames=3)
            with Image.open(png) as image:
                self.assertEqual(image.mode,'RGB')

    def test_user_design_uses_callback_and_preserves_gif_duration(self):
        from PIL import Image
        calls = []
        def frame(elapsed, profile, state):
            calls.append((elapsed, profile['id'], len(state['keys'])))
            return {'colors': {'A': '1435CC'}}
        with tempfile.TemporaryDirectory(prefix='abralia-design-export-') as root:
            path = Path(root)/'design.gif'
            result = export_design({'version':1,'name':'Callback test'}, path,
                                   duration=1, fps=12, frame_callback=frame)
            self.assertFalse(result['truncated'])
            self.assertEqual(result['playback_seconds'],1)
            self.assertTrue(calls)
            self.assertGreater(calls[-1][0], calls[0][0])
            with Image.open(path) as image:
                duration = 0
                for index in range(image.n_frames):
                    image.seek(index)
                    duration += image.info['duration']
                    red, green, blue = image.convert('RGB').crop((0,0,image.width,110)).split()
                    self.assertEqual(red.tobytes(),green.tobytes())
                    self.assertEqual(green.tobytes(),blue.tobytes())
                self.assertEqual(duration,1000)
                colors = image.convert('RGB').getcolors(maxcolors=image.width*image.height)
                self.assertIn((0x14,0x35,0xCC), [color for _, color in colors])

    def test_user_design_rejects_unknown_keys_and_bounds_long_exports(self):
        with tempfile.TemporaryDirectory(prefix='abralia-design-export-') as root:
            path = Path(root)/'design.png'
            with self.assertRaises(ValueError):
                export_design({'version':1,'frames':[{'at':0,'colors':{'UNKNOWN':'FFFFFF'}}]},path)
            with self.assertRaises(ValueError):
                export_design({'version':1},path,duration=float('inf'))
            result = export_design({'version':1},path,duration=60,fps=12,max_frames=2)
            self.assertTrue(result['truncated'])
            self.assertEqual(result['frames'],2)
            self.assertLess(result['simulation_seconds'],1)


if __name__ == '__main__':
    unittest.main()
