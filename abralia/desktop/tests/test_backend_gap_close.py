# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver, dispatch_event, routes_for
from abralia.backend.recovery import AllocationRecovery
from abralia.backend.render import Renderer
from abralia.backend.service import BrokerService
from abralia.interaction import DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import load_profile
from abralia.rgb.colors import to_hsv8
from test_backend_brightness_reference import output_values

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
OWNER = {'kind':'codex_gui','pid':1234,'parent':1,'started':'fixture',
         'tty':'??','executable':'/Applications/Codex.app/Contents/MacOS/Codex'}


class GapCloseTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.b = Broker(clock=lambda:self.now)
        self.profile = load_profile(PROFILE)
        self.r = Renderer(self.profile)
        self.owners, self.tokens = [], []
        for n in range(1,26):
            c=Caller('codex:'+str(UUID(int=n)),str(UUID(int=n)),surface='codex_desktop')
            self.owners.append(c)
            self.tokens.append(self.b.call(c,'acquire_slot',{'label':f'Agent {n}','idempotency_key':'start'})['allocation']['slot_token'])
        for n in range(2,6): self.b._release(self.b.slots[n])
        self.b.set_active(True)
        self.b.toggle_navigation()

    def driver(self, **kwargs):
        d=DeviceDriver(self.b,PROFILE,'simulated',**kwargs)
        d.routes, d.generation=routes_for(self.b,self.profile),7
        return d

    @staticmethod
    def event(d, binding, edge=Edge.UP, *, routes=None, generation=None):
        routes=routes or d.routes
        return DeviceEvent(EventType.CONTROL_EDGE,1,1,generation or d.generation,binding,
                           routes[binding].control,int(edge),EventFlags(0),0)

    def hold(self, d, binding=123, steps=13):
        d.handle_event(self.event(d,binding,Edge.DOWN))
        for _ in range(steps):
            self.now += .1
            d._service_gap_press(self.now)

    def test_any_key_in_f2_f5_selects_and_brightens_the_entire_gap(self):
        for held in (122,123,124,125):
            with self.subTest(binding=held):
                self.setUp()
                d=self.driver()
                before=self.r.frame(self.b).payload
                self.hold(d,held,steps=6)
                snapshot=self.b.gap_snapshot()
                self.assertEqual(snapshot['keys'],['F2','F3','F4','F5'])
                middle=self.r.frame(self.b).payload
                self.assertEqual(len({middle.colors[key] for key in snapshot['keys']}),1)
                self.assertGreater(to_hsv8(middle.colors['F2']).value,to_hsv8(before.colors['F2']).value)
                with patch.object(self.b,'gap_feedback',None):
                    without_gap=self.r.frame(self.b).payload
                for limit in (0,77,160,255):
                    old,new=output_values(without_gap,self.profile,limit),output_values(middle,self.profile,limit)
                    other=set(old)-set(snapshot['keys'])
                    self.assertEqual({k:old[k] for k in other},{k:new[k] for k in other})
                self.assertEqual(self.b.slots[6].position,6)

    def test_release_early_restores_whole_gap_without_moving_any_slot(self):
        d=self.driver()
        before=self.r.frame(self.b).payload
        self.hold(d,steps=4)
        d.handle_event(self.event(d,123))
        self.assertIsNone(self.b.gap_feedback)
        self.assertEqual(self.b.slots[6].position,6)
        after=self.r.frame(self.b).payload
        for key in ('F2','F3','F4','F5'): self.assertEqual(after.colors[key],before.colors[key])

    def test_full_hold_flashes_whole_gap_once_then_consumes_original_release(self):
        d=self.driver()
        old=d.routes
        release=self.event(d,124)
        self.hold(d,124,steps=12)
        self.assertEqual(self.b.gap_snapshot()['phase'],'flash')
        flash=self.r.frame(self.b).payload
        self.assertEqual(len({flash.colors[f'F{i}'] for i in range(2,6)}),1)
        self.assertEqual(self.b.slots[6].position,2)
        d.route_history[7]=old
        d.routes,d.generation=routes_for(self.b,self.profile),8
        d.handle_event(release)
        self.assertIsNone(self.b.selected)
        self.assertFalse(self.b.focus_requests)
        self.assertEqual(sum(e['kind']=='gap_closed' for e in self.b.events),1)
        self.now += .2
        self.b.step()
        self.assertIsNone(self.b.gap_snapshot())
        self.assertNotEqual(self.r.frame(self.b).payload.colors['F2'],flash.colors['F2'])

    def test_two_keys_in_the_same_gap_do_not_commit_twice(self):
        d=self.driver()
        d.handle_event(self.event(d,122,Edge.DOWN))
        d.handle_event(self.event(d,125,Edge.DOWN))
        d.handle_event(self.event(d,125))
        for _ in range(13):
            self.now += .1
            d._service_gap_press(self.now)
        d.handle_event(self.event(d,122))
        self.assertEqual(sum(e['kind']=='gap_closed' for e in self.b.events),1)

    def test_cross_page_compaction_keeps_ids_tokens_focus_questions_and_mutes(self):
        original={n:(s.slot_token,s.identity_color) for n,s in self.b.slots.items()}
        self.b.agent_mutes[self.tokens[5]]=600
        self.b.call(self.owners[14],'report_question',{'slot_token':self.tokens[14], 'idempotency_key':'q',
                    'question_id':'q','kind':'free_text','options':[]})
        self.b._pickup_slot(self.b.slots[15])
        self.b.turn_page(-1)
        gap=self.b.begin_gap_hold(4,self.b.layout_revision)
        self.assertTrue(self.b.close_gap(gap))
        self.assertEqual(self.b.page_count,2)
        self.assertEqual(self.b.slots[25].position,21)
        self.assertEqual(self.b.slots[6].f_key,'F2')
        self.assertEqual(self.b.selected,15)
        self.assertTrue(self.b.slots[15].question.picked_up)
        self.assertTrue(self.b.attention_muted(self.b.slots[6]))
        self.assertEqual(original,{n:(s.slot_token,s.identity_color) for n,s in self.b.slots.items()})
        retry=self.b.call(self.owners[5],'acquire_slot',{'label':'Agent 6','idempotency_key':'start'})
        self.assertTrue(retry['replayed'])
        self.assertEqual((retry['allocation']['slot_id'],retry['allocation']['display_position'],retry['allocation']['f_key']),(6,2,'F2'))

    def test_slot_id_is_not_used_as_a_display_position_after_new_allocation(self):
        self.b.close_gap(self.b.gap_at(3))
        c=Caller('new',str(UUID(int=99)),surface='codex_desktop')
        result=self.b.call(c,'acquire_slot',{'label':'New','idempotency_key':'new'})['allocation']
        self.assertEqual((result['slot_id'],result['display_position'],result['f_key']),(2,22,'F10'))
        self.b.navigate('last_agent')
        self.assertEqual(self.b.candidate().slot_id,2)
        self.assertEqual(self.b.page,1)
        self.b.navigate('first_agent')
        self.assertEqual(self.b.candidate().slot_id,1)
        self.b.call(self.owners[24],'set_notification',{'slot_token':self.tokens[24], 'enabled':True,'idempotency_key':'notify'})
        self.b.act_on_call('pickup',self.tokens[24],self.b.slots[25].notification.notification_id)
        self.assertEqual((self.b.selected,self.b.page),(25,1))
        self.assertEqual(self.b.snapshot(self.b.slots[25])['f_key'],'F9')

    def test_positions_before_gap_are_untouched_and_later_agents_keep_order(self):
        self.b._release(self.b.slots[8])
        self.b._release(self.b.slots[12])
        expected=[s.slot_id for s in sorted(self.b.slots.values(),key=lambda s:s.position)]
        self.b.close_gap(self.b.gap_at(5))
        self.assertEqual([s.slot_id for s in sorted(self.b.slots.values(),key=lambda s:s.position)],expected)
        self.assertEqual(self.b.slots[1].position,1)
        self.assertEqual(sorted(s.position for s in self.b.slots.values()),list(range(1,len(expected)+1)))

    def test_page_end_gap_pulls_from_later_pages_and_leaves_earlier_gap_unchanged(self):
        for n in range(9,15): self.b._release(self.b.slots[n])
        gap=self.b.gap_at(11)
        self.assertEqual((gap.start,gap.end),(9,12))
        self.b.close_gap(gap)
        self.assertEqual(self.b.slots[15].position,9)
        self.assertEqual(self.b.slots[18].position,12)
        self.assertTrue(all(self.b.slot_at_position(p) is None for p in range(2,6)))

    def test_a_wholly_empty_page_is_one_twelve_key_gap(self):
        for n in range(13,25): self.b._release(self.b.slots[n])
        self.b.turn_page(1)
        d=self.driver()
        self.hold(d,126,steps=5)
        self.assertEqual(len(self.b.gap_snapshot()['keys']),12)
        for _ in range(8):
            self.now += .1
            d._service_gap_press(self.now)
        self.assertEqual((self.b.slots[25].slot_id,self.b.slots[25].position),(25,13))
        self.assertEqual(self.b.page_count,2)

    def test_elapsed_empty_recovery_window_does_not_cancel_a_valid_hold(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            recovery=AllocationRecovery(self.b,temp,Path(temp)/'slots.json')
            recovery.deadline=.2  # All reservations already resumed earlier.
            d=self.driver()
            self.hold(d,steps=1)
            revision=self.b.layout_revision
            self.now=.2
            recovery.expire()
            self.assertEqual(self.b.layout_revision,revision)
            self.assertEqual(self.b.gap_snapshot()['phase'],'holding')

    def test_old_f_key_and_page_number_releases_cannot_act_after_compaction(self):
        old=routes_for(self.b,self.profile)
        self.b.close_gap(self.b.gap_at(2))
        for binding in (6,101):
            event=DeviceEvent(EventType.CONTROL_EDGE,1,1,7,binding,old[binding].control,int(Edge.UP),EventFlags(0),0)
            dispatch_event(self.b,event,old,7)
        self.assertIsNone(self.b.selected)
        self.assertEqual(self.b.page,0)

    def test_layout_change_page_change_mode_exit_and_stall_cancel_hold(self):
        for kind in ('allocation','release','page','mode','stall'):
            with self.subTest(kind=kind):
                self.setUp()
                d=self.driver()
                self.hold(d,steps=2)
                if kind=='allocation': self.b.call(Caller('new'),'acquire_slot',{'label':'New','idempotency_key':'a'})
                elif kind=='release': self.b._release(self.b.slots[7])
                elif kind=='page': self.b.turn_page(1)
                elif kind=='mode': self.b.set_active(False)
                self.now += 2 if kind=='stall' else .1
                d._service_gap_press(self.now)
                d.handle_event(self.event(d,123))
                self.assertIsNone(self.b.gap_feedback)
                self.assertEqual(self.b.slots[6].position,6)

    def test_empty_f_keys_stay_reserved_throughout_agent_mode_and_tail_has_no_action(self):
        self.assertEqual(routes_for(self.b,self.profile)[122].action,'close_gap')
        self.b.disarm_navigation('explicit')
        self.assertEqual(routes_for(self.b,self.profile)[122].action,'close_gap')
        self.assertTrue(self.b.gap_valid(self.b.gap_at(3)))
        self.b.select_slot(self.tokens[0])
        self.assertEqual(routes_for(self.b,self.profile)[122].action,'close_gap')
        self.b.set_active(False)
        self.assertFalse(any(r.action=='close_gap' for r in routes_for(self.b,self.profile).values()))
        self.b.set_active(True)
        self.b.toggle_navigation()
        self.b.turn_page(2)
        self.assertIsNone(self.b.gap_at(26))
        self.assertIsNone(self.b.begin_gap_hold(26,self.b.layout_revision))
        self.b.turn_page(-2)
        self.b.reserved_positions[30]='pending-owner'
        self.assertIsNone(self.b.gap_at(3))

    def test_whole_gap_hold_works_in_page_mode_without_navigation_or_agent_selection(self):
        self.b.disarm_navigation('explicit')
        self.assertEqual(self.b.knob_mode,'pages')
        self.assertFalse(self.b.navigation_active)
        d=self.driver()
        self.hold(d,124)
        self.assertEqual(self.b.gap_snapshot()['phase'],'flash')
        self.assertEqual(self.b.slots[6].position,2)

    def test_recovery_version_two_persists_position_and_version_one_keeps_old_layout(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            root=Path(temp)
            path=root/'slots.json'
            self.b.close_gap(self.b.gap_at(2))
            r=AllocationRecovery(self.b,root,path,owner_check=lambda _:True)
            r.attach(self.owners[5].caller_id,'a'*64,OWNER)
            r.save()
            saved=json.loads(path.read_text())
            self.assertEqual(saved['version'],2)
            self.assertEqual(saved['allocations'][0]['display_position'],2)
            for legacy,expected in ((False,2),(True,6)):
                if legacy:
                    saved['version']=1
                    saved['allocations'][0].pop('display_position')
                    path.write_text(json.dumps(saved))
                restored=Broker()
                recovery=AllocationRecovery(restored,root,path,owner_check=lambda _:True)
                recovery.load()
                self.assertEqual(restored.reserved_positions,{expected:self.owners[5].caller_id})
                recovery.resume([self.owners[5].caller_id],'a'*64)
                slot=restored.slots[6]
                self.assertEqual((slot.slot_id,slot.position),(6,expected))
                self.assertNotEqual(slot.slot_token,self.tokens[5])

    def test_recovery_position_collision_is_rejected_and_does_not_create_slots(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            root=Path(temp); path=root/'slots.json'
            r=AllocationRecovery(self.b,root,path)
            for index in (5,6): r.attach(self.owners[index].caller_id,'a'*64,OWNER)
            r.save()
            saved=json.loads(path.read_text())
            saved['allocations'][1]['display_position']=saved['allocations'][0]['display_position']
            path.write_text(json.dumps(saved))
            restored=Broker(); recovery=AllocationRecovery(restored,root,path)
            recovery.load()
            self.assertEqual(recovery.error,'invalid_recovery_state')
            self.assertFalse(restored.slots)

    def test_service_commit_rolls_back_before_flash_if_persistence_fails(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            root=Path(temp)
            service=BrokerService(root,PROFILE,endpoint=root/'broker.sock')
            service.broker=self.b
            service.recovery.broker=self.b
            service.recovery.attach(self.owners[5].caller_id,'a'*64,OWNER)
            service.recovery.save()
            d=self.driver(gap_committer=service._commit_gap)
            with patch('abralia.backend.recovery.os.replace',side_effect=OSError('fixture')):
                self.hold(d)
            self.assertEqual(self.b.slots[6].position,6)
            self.assertIsNone(self.b.gap_feedback)
            self.assertFalse(any(e['kind']=='gap_closed' for e in self.b.events))
            self.assertEqual(json.loads(service.recovery.path.read_text())['allocations'][0]['display_position'],6)

    def test_confirmation_flash_starts_after_a_slow_successful_save(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
            service=BrokerService(temp,PROFILE)
            service.broker=self.b
            service.recovery.broker=self.b
            d=self.driver(gap_committer=service._commit_gap)
            with patch.object(service.recovery,'save',side_effect=lambda:setattr(self,'now',self.now+2)):
                self.hold(d,steps=12)
            self.assertGreater(self.b.gap_feedback['until'],self.now)
            self.assertEqual(self.b.gap_snapshot()['phase'],'flash')


if __name__ == '__main__': unittest.main()
