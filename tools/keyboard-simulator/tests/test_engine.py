# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOL))
from abralia_simulator.engine import Simulator, PROFILES
from abralia.device_profile import load_profile_data


class EngineTests(unittest.TestCase):
    def test_import_and_all_profiles_never_open_physical_devices(self):
        with patch('abralia.shared_hid.SharedRawHidSession.__init__',side_effect=AssertionError('no HID')):
            for profile in PROFILES:
                simulator=Simulator(profile['id'])
                state=simulator.snapshot()
                self.assertEqual(state['profile']['id'],profile['id'])
                self.assertEqual(sum(key['rgb'] for key in state['keys']),87)
                if state['profile']['has_encoder']:
                    self.assertTrue(all(key['led'] is None for key in state['keys'] if key['id'].startswith('KNOB')))
        command="import sys,json;sys.path.insert(0,sys.argv[1]);from abralia_simulator import Simulator;s=Simulator();print(json.dumps([name for name in ('hid','fcntl','abralia.backend.device','abralia.backend.service','abralia.backend.mcp') if name in sys.modules]))"
        result=subprocess.run([sys.executable,'-c',command,str(TOOL)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),[])

    def test_idle_background_is_steady_and_inactive_keys_do_not_select(self):
        simulator=Simulator();agent=simulator.add_agent('Project','Task')
        before=simulator.snapshot()['colors']
        self.assertEqual(simulator.advance(.5)['colors'],before)
        simulator.input('F1')
        self.assertFalse(simulator.snapshot()['agents'][0]['selected'])
        simulator.input('MODE','double')
        simulator.input('F1')
        self.assertTrue(simulator.snapshot()['agents'][0]['selected'])
        self.assertTrue(any(event['kind']=='simulated_task_open' for event in simulator.snapshot()['events']))
        self.assertFalse(simulator.broker.focus_requests)

    def test_incoming_call_controls_need_no_preselection_and_mute_disarms(self):
        simulator=Simulator();agent=simulator.add_agent('Project','Task');simulator.notify(agent)
        self.assertEqual(simulator.snapshot()['bindings'],[])
        simulator.input('MODE','double')
        actions={row['key']:row['action'] for row in simulator.snapshot()['bindings']}
        self.assertEqual(actions['SCREENSHOT'],'pickup');self.assertEqual(actions['SCROLL_LOCK'],'mute')
        simulator.input('SCROLL_LOCK')
        actions={row['key']:row['action'] for row in simulator.snapshot()['bindings']}
        self.assertNotIn('SCREENSHOT',actions);self.assertNotIn('SCROLL_LOCK',actions)
        simulator.input('F1')
        self.assertTrue(simulator.snapshot()['agents'][0]['selected'])
        simulator.notify(agent,enabled=False)

    def test_holds_sorting_and_no_encoder_layout_use_production_rules(self):
        simulator=Simulator(PROFILES[2]['id'])
        for number in range(5):
            for project in ('A','B','C'):simulator.add_agent(project,f'{project}{number}')
        with self.assertRaisesRegex(ValueError,'no encoder'):simulator.input('KNOB_CW')
        simulator.input('MODE','double');simulator.press('MODE');simulator.advance(.9)
        self.assertTrue(simulator.snapshot()['navigation_active']);simulator.release_key('MODE')
        simulator.input('ESC')
        self.assertEqual(simulator.snapshot()['sort_policy'],'project')
        simulator.input('PAGE_DOWN');self.assertEqual(simulator.snapshot()['page'],2)
        simulator.advance(16);self.assertFalse(simulator.snapshot()['navigation_active'])

    def test_whole_gap_hold_preserves_ids_and_does_not_select_moved_agent(self):
        simulator=Simulator()
        agents=[simulator.add_agent('A',str(i)) for i in range(16)]
        for agent in agents[1:5]:simulator.release(agent)
        simulator.input('MODE','double')
        before={row['id']:row['slot_id'] for row in simulator.snapshot()['agents']}
        simulator.press('F3');simulator.advance(.6)
        self.assertEqual(simulator.broker.gap_snapshot()['keys'],['F2','F3','F4','F5'])
        simulator.advance(.7);simulator.release_key('F3')
        self.assertEqual(simulator.snapshot()['page_count'],1)
        self.assertEqual({row['id']:row['slot_id'] for row in simulator.snapshot()['agents']},before)
        self.assertFalse(any(row['selected'] for row in simulator.snapshot()['agents']))

    def test_guide_waits_for_pickup_and_escape_dismisses(self):
        simulator=Simulator();agent=simulator.add_agent('A','A')
        simulator.guide(agent,{'W':'A0FF00'})
        self.assertIsNone(simulator.broker.visible_keyboard_frame())
        simulator.input('MODE','double');simulator.input('SCREENSHOT')
        self.assertIsNotNone(simulator.broker.visible_keyboard_frame())
        self.assertEqual([binding['action'] for binding in simulator.snapshot()['bindings']],['dismiss_keyboard_frame'])
        simulator.input('MODE','hold',duration=1.)
        self.assertFalse(simulator.broker.navigation_active)
        simulator.input('ESC');self.assertIsNone(simulator.broker.visible_keyboard_frame())

    def test_release_active_fog_and_seeded_reset_are_safe(self):
        def run():
            simulator=Simulator(seed=9);a=simulator.add_agent('A','A');simulator.notify(a);simulator.advance(15)
            return simulator,a,simulator.snapshot()['colors']
        one,a,first=run();two,_,second=run()
        self.assertEqual(first,second)
        one.release(a);self.assertEqual(one.advance(5)['orb_count'],0)

    def test_design_validation_is_staged_and_reset_preserves_imported_frames(self):
        simulator=Simulator()
        design={'version':1,'name':'Custom','seed_agents':[],'timeline':[],'frames':[{'at':0,'colors':{'A':'FF4400'}}]}
        simulator.load_design(design)
        before=simulator.design()
        for invalid in ({**design,'plugin':'/tmp/code.py'},
                        {**design,'frames':[{'at':0,'colors':{'A':'not-rgb'}}]},
                        {**design,'timeline':[{'at':0,'action':{'type':'notify','agent_id':'missing'}}]},
                        {**design,'seed_agents':[{'id':'new','project':'P','label':'New'}],
                         'timeline':[{'at':0,'action':{'type':'guide','agent_id':'new','colors':{}}}]},
                        {**design,'timeline':[{'at':0,'action':{'type':'advance','seconds':4}}]}):
            with self.assertRaises(ValueError):simulator.load_design(invalid)
            self.assertEqual(simulator.design(),before)
        simulator.advance(4);state=simulator.reset()
        self.assertEqual(state['time'],0);self.assertEqual(state['colors']['A'],'FF4400')
        self.assertEqual(state['frame_source'],'frames')

    def test_custom_profile_without_function_row_remains_renderable(self):
        data=load_profile_data(PROFILES[0]['id'])
        text=json.dumps(data).replace('"F1"','"CUSTOM_KEY"')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profile.json';path.write_text(text)
            simulator=Simulator(path)
            self.assertFalse(simulator.profile_info()['interaction_available'])
            simulator.load_design({'version':1,'frames':[{'at':0,'colors':{'CUSTOM_KEY':'ABCDEF'}}]})
            self.assertEqual(simulator.snapshot()['colors']['CUSTOM_KEY'],'ABCDEF')
            with self.assertRaisesRegex(ValueError,'interaction unavailable'):simulator.input('MODE','double')

    def test_callback_receives_geometry_but_cannot_mutate_broker_state(self):
        def frame(elapsed,profile,state):
            self.assertTrue(state['keys']);state['agents'].clear()
            return {'colors':{'A':'123456'}}
        simulator=Simulator(frame_callback=frame);simulator.add_agent('A','A')
        state=simulator.snapshot()
        self.assertEqual(state['colors']['A'],'123456');self.assertEqual(len(state['agents']),1)
        self.assertEqual(state['frame_source'],'plugin')


if __name__=='__main__':unittest.main()
