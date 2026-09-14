# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import math
import unittest
from uuid import UUID

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import DeviceDriver, routes_for, dispatch_event
from abralia.backend.notification_animation import validate_animation, render_animation
from abralia.backend.render import intensity, hex_color
from abralia.interaction import DeviceEvent, EventType, EventFlags, Edge
from abralia.rgb import Srgb8


def clip(shape='ring', **fields):
    return {'duration_ms': 3000, 'layers': [{'shape': shape, 'color': 'slot_highlight', **fields}]}


class AnimationMathTests(unittest.TestCase):
    def test_all_primitives_are_bounded_and_zero_at_clip_endpoints(self):
        points = {str(i): (i / 20, .5) for i in range(21)}
        base, identity = Srgb8(60, 20, 90), Srgb8(170, 20, 255)
        for shape in ('ring', 'spot', 'sweep', 'pulse'):
            animation = validate_animation(clip(shape))
            self.assertEqual(render_animation(animation, points, 0, identity, base, 128), {})
            self.assertEqual(render_animation(animation, points, 3000, identity, base, 128), {})
            self.assertEqual(render_animation(animation, points, 4000, identity, base, 128), {})
            changed = False
            for elapsed in (300, 800, 1400, 2000, 2700):
                result = render_animation(animation, points, elapsed, identity, base, 128)
                changed |= any(c != base for c in result.values())
                self.assertTrue(all(max(c.red,c.green,c.blue) <= 128 for c in result.values()))
            self.assertTrue(changed, shape)

    def test_overlapping_layers_cannot_hide_background_and_order_is_irrelevant(self):
        layer = {'shape': 'spot', 'from':[.5,.5], 'to':[.5,.5], 'color':'negative', 'opacity':.7}
        animation = validate_animation({'duration_ms':3000,'layers':[layer]*4})
        base = Srgb8(0,0,255)
        color = render_animation(animation, {'center':(.5,.5)}, 1500, base, base, 255)['center']
        # At least30% of the blue background remains in linear light even
        # when four red overlays coincide (sRGB blue about149).
        self.assertGreaterEqual(color.blue,149)
        mixed = {'duration_ms':3000,'layers':[
            layer, {**layer, 'color':'positive', 'opacity':.3}]}
        a = validate_animation(mixed)
        b = validate_animation({**mixed, 'layers':mixed['layers'][::-1]})
        self.assertEqual(render_animation(a,{'a':(.5,.5)},1200,base,base,255),
                         render_animation(b,{'a':(.5,.5)},1200,base,base,255))

    def test_invalid_code_colors_coordinates_and_complexity_are_rejected(self):
        invalid = [None, {'code':'anything'}, {'duration_ms':9000,'layers':[{'shape':'ring'}]},
            {'duration_ms':3000,'layers':[{'shape':'ring'}]*5}, clip('shader'),
            clip('ring', center=[2,.5]), clip('ring', opacity=1),
            clip('ring', center=[True,.5]), clip('ring', center=[math.nan,.5]),
            clip('pulse', pulses=4), clip('sweep', axis='z'), clip('spot', **{'from':[0]}),
            clip('ring', start_ms=2900, end_ms=3000)]
        invalid.append({'duration_ms':3000,'layers':[{'shape':'ring','color':'FFFFFF'}]})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((TypeError,ValueError)):
                validate_animation(value)


class CustomVisualTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.b = Broker(clock=lambda:self.now)
        self.d = DeviceDriver(self.b,'builtin:keychron-v3-8k-ansi-encoder-effect25','simulated')
        self.owner = Caller('fixture',str(UUID(int=101)),surface='codex_desktop')
        self.other = Caller('other',str(UUID(int=102)),surface='codex_desktop')
        self.counter = 0
        r=self.b.call(self.owner,'acquire_slot',{'label':'Fixture','idempotency_key':'start'})
        self.token=r['allocation']['slot_token']; self.slot=self.b.slots[1]
        self.b.set_active(True)

    def call(self, operation, **args):
        self.counter+=1
        return self.b.call(self.owner,operation,{'slot_token':self.token,'idempotency_key':str(self.counter),**args})

    def advance(self, seconds):
        self.now+=seconds; self.b.step()

    def pickup(self):
        target=self.b.pending_target()
        self.b.act_on_call('pickup',target.slot_token,target.notification.notification_id)

    def test_default_is_optional_and_invalid_clip_does_not_lose_call(self):
        result=self.call('set_notification',enabled=True,animation={'code':'no code execution'})
        self.assertEqual(result['status'],'accepted')
        self.assertIsNone(self.slot.notification.animation)
        self.assertIsNotNone(result['allocation']['notification']['animation']['fallback'])
        self.assertEqual(self.slot.notification.status,'active')

    def test_saved_default_does_not_notify_and_is_snapshotted_for_native_questions(self):
        value=clip()
        self.call('set_notification_animation',animation=value)
        self.assertIsNone(self.slot.notification)
        self.assertIsNone(self.b.notification_visuals()['presentation'])
        value['layers'][0]['shape']='bad'
        self.b.observe_codex(self.token,{'thread_id':self.owner.thread_id,'execution':'running','turn_id':'turn',
            'questions':[{'request_id':'q','turn_id':'turn','tool':'request_user_input_async','stage':'accepted'}]})
        notice=self.slot.notification
        self.assertEqual(notice.animation['layers'][0]['shape'],'ring')
        self.call('set_notification_animation',animation=None)
        self.assertIsNone(self.slot.notification_animation)
        self.assertIsNotNone(notice.animation)
        self.assertIsNotNone(self.b.notification_visuals()['presentation']['animation'])

    def test_explicit_default_overrides_saved_clip(self):
        self.call('set_notification_animation',animation=clip())
        self.call('set_notification',enabled=True,animation='default')
        self.assertIsNone(self.slot.notification.animation)

    def test_host_can_disable_custom_clips(self):
        self.b.config=BrokerConfig(agent_animations_enabled=False)
        result=self.call('set_notification',enabled=True,animation=clip())
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(self.slot.notification.animation_fallback,'disabled_by_host')
        self.assertIsNone(self.slot.notification.animation)

    def test_custom_window_does_not_extend_queue_and_has_fixed_background(self):
        value=clip('spot'); value['duration_ms']=1000
        self.call('set_notification',enabled=True,animation=value)
        start=self.b.notification_visuals()['presentation']['phase']
        self.assertEqual(start,'onset')
        self.advance(.58+5.2)
        a=self.d.renderer.frame(self.b).payload
        self.advance(.5)
        b=self.d.renderer.frame(self.b).payload
        expected=intensity(hex_color(self.slot.identity_color),.5)
        for key in self.d.renderer.region:
            self.assertEqual(a.colors[key],expected,key)
            self.assertEqual(b.colors[key],expected,key)
        self.advance(2.4)
        self.assertEqual(self.b.notification_visuals()['presentation']['phase'],'condensing')
        self.advance(2)
        self.assertIsNone(self.b.notification_visuals()['presentation'])
        self.assertEqual(len(self.b.notification_visuals()['orbs']),1)

    def test_custom_overlay_preserves_control_keys_and_frame_ceiling(self):
        self.call('set_notification',enabled=True,animation=clip('sweep'))
        self.b.toggle_navigation()
        self.advance(.58+5.5)
        custom=self.d.renderer.frame(self.b).payload
        protected=('ENTER','PAUSE','SCREENSHOT','SCROLL_LOCK','HOME','END','LEFT','RIGHT','UP','DOWN')
        self.assertEqual(custom.colors['SCREENSHOT'],Srgb8(0,255,0))
        self.assertEqual(custom.colors['HOME'],Srgb8(160,96,255))
        self.assertEqual(custom.colors['LEFT'],Srgb8(255,144,0))
        self.assertEqual(custom.colors['ENTER'],Srgb8(160,255,0))
        self.assertTrue(all(max(c.red,c.green,c.blue)<=255 for c in custom.colors.values()))
        self.assertTrue(all(key in custom.colors for key in protected))

    def test_retry_cannot_change_animation_or_override_mute(self):
        args={'slot_token':self.token,'enabled':True,'animation':clip(),'idempotency_key':'same'}
        first=self.b.call(self.owner,'set_notification',args)
        self.advance(5)
        self.assertTrue(self.b.call(self.owner,'set_notification',args)['replayed'])
        self.b.act_on_call('mute',self.token,self.slot.notification.notification_id)
        self.call('set_notification',enabled=True,animation=clip('sweep'))
        self.assertEqual(self.slot.notification.status,'muted')
        self.assertEqual(self.slot.notification.animation['layers'][0]['shape'],'ring')
        self.assertEqual(self.b.call(self.owner,'set_notification',{**args,'animation':clip('pulse')})['reason'],'idempotency_conflict')
        self.assertEqual(first['allocation']['notification']['animation']['mode'],'custom')

    def show(self, colors=None):
        result=self.call('show_keyboard_frame',colors=colors or {'W':'positive','A':'20A0FF','F2':'negative','ESC':'off'})
        self.assertEqual(result['status'],'accepted',result)
        return result['allocation']['keyboard_frame']['id']

    def test_guide_requires_pickup_is_static_and_only_escape_is_captured(self):
        frame_id=self.show()
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.pickup()
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,frame_id)
        frame=self.d.renderer.frame(self.b).payload
        self.assertEqual(frame.colors['ESC'],Srgb8(128,0,0))
        self.assertEqual(frame.colors['W'],Srgb8(80,128,0))
        self.advance(.37)
        later=self.d.renderer.frame(self.b).payload
        self.assertEqual({k:v for k,v in frame.colors.items() if k!='PAUSE'},
                         {k:v for k,v in later.colors.items() if k!='PAUSE'})
        routes=routes_for(self.b,self.d.profile,hold_enabled=True)
        self.assertEqual(set(routes),{41})
        self.advance(900)
        stable=self.d.renderer.frame(self.b).payload
        self.assertEqual({k:v for k,v in stable.colors.items() if k!='PAUSE'},
                         {k:v for k,v in frame.colors.items() if k!='PAUSE'})
        self.assertTrue(self.b.dismiss_keyboard_frame(self.token,frame_id))
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.assertFalse(self.b.focused_slot())
        self.assertNotIn(41,routes_for(self.b,self.d.profile))

    def test_muted_guide_can_be_picked_by_its_slot_without_rearming_call(self):
        frame_id=self.show()
        self.b.act_on_call('mute',self.token,self.slot.notification.notification_id)
        self.assertIsNone(self.b.pending_target())
        self.b.select_slot(self.token)
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,frame_id)

    def test_guide_remains_accessible_after_notification_expiry(self):
        frame_id=self.show()
        self.advance(301)
        self.assertEqual(self.slot.notification.status,'expired')
        self.b.select_slot(self.token)
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,frame_id)

    def test_bad_frame_or_foreign_owner_cannot_replace_current_guide(self):
        frame_id=self.show(); self.pickup()
        for colors in ({'missing':'white'},{'A':'invalid'},{}):
            self.assertEqual(self.call('show_keyboard_frame',colors=colors)['status'],'rejected')
        result=self.b.call(self.other,'clear_keyboard_frame',{'slot_token':self.token,'frame_id':frame_id,'idempotency_key':'bad'})
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,frame_id)

    def test_stale_escape_and_old_control_events_cannot_act_on_new_guide(self):
        first=self.show(); self.pickup()
        old=routes_for(self.b,self.d.profile)
        self.b.dismiss_keyboard_frame(self.token,first)
        self.advance(6)
        second=self.show({'SPACE':'positive'}); self.pickup()
        event=DeviceEvent(EventType.CONTROL_EDGE,1,1,1,41,old[41].control,int(Edge.UP),EventFlags(0),0)
        dispatch_event(self.b,event,old,1)
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,second)
        current=routes_for(self.b,self.d.profile)
        dispatch_event(self.b,event,current,2)
        self.assertIsNotNone(self.b.visible_keyboard_frame())
        dispatch_event(self.b,event,current,1)
        self.assertIsNone(self.b.visible_keyboard_frame())

    def test_guide_hides_when_inactive_without_forcing_mode(self):
        frame_id=self.show(); self.pickup()
        self.b.set_active(False)
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.assertNotEqual(self.d.renderer.frame(self.b).scene_id,'abralia-keyboard-frame')
        self.b.set_active(True)
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,frame_id)

    def test_guide_replacement_needs_fresh_pickup_and_same_frame_does_not_renotify(self):
        first=self.show(); self.pickup()
        repeated=self.show()
        self.assertEqual(first,repeated)
        self.advance(6)
        second=self.show({'SPACE':'positive'})
        self.assertNotEqual(first,second)
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.assertEqual(self.call('clear_keyboard_frame',frame_id=first)['status'],'rejected')
        self.pickup()
        self.assertEqual(self.b.visible_keyboard_frame()[1].frame_id,second)

    def test_clear_or_disconnect_releases_guide_without_answering_native_question(self):
        frame_id=self.show(); self.pickup()
        self.b.observe_codex(self.token,{'thread_id':self.owner.thread_id,'execution':'running','turn_id':'turn',
            'questions':[{'request_id':'q','turn_id':'turn','tool':'request_user_input_async','stage':'accepted'}]})
        self.assertIsNone(self.b.notification_visuals()['presentation'])
        self.call('clear_keyboard_frame',frame_id=frame_id)
        self.assertIn('q',self.slot.native_requests)
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.advance(6); self.show({'SPACE':'slot'}); self.b.select_slot(self.token)
        self.b.disconnected_at(self.owner.caller_id)
        self.advance(31)
        self.assertIsNone(self.b.visible_keyboard_frame())
        self.assertFalse(self.b.slots)


if __name__=='__main__':
    unittest.main()
