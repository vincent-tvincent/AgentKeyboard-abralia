# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver, routes_for
from abralia.interaction import ControlId, DeviceEvent, Edge, EventFlags, EventType, StatusFlags

PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


class DeviceBoundaryTests(unittest.TestCase):
    def test_call_keys_are_separate_from_mode_and_release_after_handling(self):
        profiles = (PROFILE, 'builtin:keychron-v3-ansi-effect25',
                    'builtin:keychron-v3-ansi-encoder-effect25')
        for profile in profiles:
            for action, binding in (('mute', 30), ('pickup', 31), ('select', 1)):
                with self.subTest(profile=profile, action=action):
                    b = Broker()
                    caller = Caller('fixture')
                    token = b.call(caller, 'acquire_slot', {'label':'fixture', 'idempotency_key':'a'})['allocation']['slot_token']
                    b.call(caller, 'set_notification', {'slot_token':token, 'enabled':True, 'idempotency_key':'n'})
                    driver = DeviceDriver(b, profile, 'simulated')
                    b.set_active(True)
                    driver.routes = routes_for(b, driver.profile, hold_enabled=True)
                    driver.generation = 7
                    routes = driver.routes
                    self.assertEqual(routes[31].control, ControlId.key(*driver.profile.element_by_id['SCREENSHOT'].matrix))
                    self.assertEqual(routes[30].control, ControlId.key(*driver.profile.element_by_id['SCROLL_LOCK'].matrix))
                    self.assertEqual(routes[50].control, driver.mode_key)
                    self.assertEqual(len({r.control for r in routes.values()}), len(routes))
                    def edge(number, generation=7):
                        return DeviceEvent(EventType.CONTROL_EDGE, 1, 1, generation, number,
                                           routes[number].control, int(Edge.UP), EventFlags(0), 0)
                    b.set_active(False)
                    driver.handle_event(edge(binding))
                    self.assertEqual(b.slots[1].notification.status, 'active')
                    inactive = driver.renderer.frame(b).payload.colors
                    self.assertNotIn('SCREENSHOT', inactive)
                    self.assertNotIn('SCROLL_LOCK', inactive)
                    b.set_active(True)
                    driver.handle_event(edge(binding, 6))
                    driver.handle_event(edge(50))
                    self.assertEqual(b.slots[1].notification.status, 'active')
                    driver.handle_event(edge(binding))
                    self.assertEqual(b.slots[1].notification.status, 'muted' if action == 'mute' else 'picked_up')
                    after = routes_for(b, driver.profile, hold_enabled=True)
                    self.assertFalse({30, 31} & after.keys())
                    self.assertIn(50, after)
                    colors = driver.renderer.frame(b).payload.colors
                    self.assertNotIn('SCREENSHOT', colors)
                    self.assertNotIn('SCROLL_LOCK', colors)

    def test_f_key_events_dispatch_only_latest_task_and_ignore_stale_generation(self):
        b = Broker()
        b.delivery = 'written'
        b.set_active(True)
        callers = [Caller(str(i), f'11111111-2222-3333-4444-{i:012d}', surface='codex_desktop') for i in (1, 2)]
        for caller in callers:
            b.call(caller, 'acquire_slot', {'label':caller.caller_id, 'idempotency_key':'acquire'})
        driver = DeviceDriver(b, PROFILE, 'hardware')
        routes = routes_for(b, driver.profile)
        driver.interaction = MagicMock()
        driver.interaction.replace_bindings.return_value.binding_generation = 7
        driver.protocol = MagicMock()
        driver.rgb = MagicMock()
        def edge(binding, generation=7):
            return DeviceEvent(EventType.CONTROL_EDGE, 1, binding, generation,
                               binding, routes[binding].control, int(Edge.UP), EventFlags(0), 0)
        # Two valid selections and then a stale keyup for the previous task.
        driver.protocol.service.return_value = [edge(1), edge(2), edge(1, 6)]
        with patch('abralia.backend.device.subprocess.Popen') as opener:
            opener.return_value.poll.return_value = 0
            opener.return_value.returncode = 0
            driver.tick()
            opener.assert_called_once()
            self.assertEqual(opener.call_args.args[0], ['/usr/bin/open', f'codex://threads/{callers[1].thread_id}'])
        self.assertEqual(b.selected, 2)
        self.assertFalse(b.focus_requests)

    def test_start_rejects_incapable_busy_and_wrong_effect_before_claim_or_rgb(self):
        for capable, token, flags, reason in ((False, 0, 128, 'single-tap'),
                                             (True, 77, 128, 'another host'),
                                             (True, 0, 0, 'effect 25')):
            with self.subTest(reason=reason), \
                 patch('abralia.backend.device.SharedRawHidSession.open_profile') as raw, \
                 patch('abralia.backend.device.HostInteractionProtocolClient') as protocol, \
                 patch('abralia.backend.device.RgbController') as rgb:
                protocol.return_value.get_capabilities.return_value = SimpleNamespace(supports_toggle_single_tap=capable)
                protocol.return_value.get_status.return_value = SimpleNamespace(session_token=token, status_flags=StatusFlags(flags))
                driver = DeviceDriver(Broker(),PROFILE,'hardware')
                with self.assertRaisesRegex(RuntimeError,reason): driver.start()
                protocol.return_value.__enter__.assert_not_called()
                rgb.assert_not_called()
                raw.return_value.__exit__.assert_called_once()

    def test_simulated_driver_never_opens_hid_or_dispatches_task_url(self):
        b=Broker()
        with patch('abralia.backend.device.SharedRawHidSession.open_profile') as raw, \
             patch('abralia.backend.device.subprocess.Popen') as opener:
            driver=DeviceDriver(b,PROFILE,'simulated')
            driver.start()
            c=Caller('fixture','11111111-2222-3333-4444-555555555555',surface='codex_desktop')
            token=b.call(c,'acquire_slot',{'label':'fixture','idempotency_key':'a'})['allocation']['slot_token']
            b.call(c,'set_notification',{'slot_token':token,'enabled':True,'idempotency_key':'n'})
            b.set_active(True)
            b.act_on_call('pickup',token,b.slots[1].notification.notification_id)
            driver.tick()
            self.assertIn('focus_simulated',[e['kind'] for e in b.events])
            driver.close()
            raw.assert_not_called()
            opener.assert_not_called()

    def test_effect_loss_stops_output_and_does_not_keep_capture_alive(self):
        b=Broker()
        b.delivery='written'
        b.set_active(True)
        driver=DeviceDriver(b,PROFILE,'hardware')
        driver.rgb=MagicMock()
        driver.protocol=MagicMock()
        driver.protocol.service.return_value=[SimpleNamespace(event_type=EventType.RGB_EFFECT_CHANGED, rgb_effect25_selected=False)]
        driver.lease=MagicMock()
        lease=driver.lease
        driver.tick()
        self.assertEqual(b.delivery,'suspended')
        self.assertFalse(b.active)
        driver.rgb.suspend_output.assert_called_once()
        driver.rgb.display.assert_not_called()
        lease.close.assert_called_once()
        driver.tick()
        self.assertEqual(driver.protocol.service.call_count,1)

    def test_missing_encoder_is_supported_without_encoder_bindings(self):
        with patch('abralia.backend.device.SharedRawHidSession.open_profile') as raw:
            b = Broker()
            b.call(Caller('fixture'), 'acquire_slot', {'label':'fixture','idempotency_key':'start'})
            b.set_active(True)
            b.toggle_navigation()
            driver = DeviceDriver(b,'builtin:keychron-v3-ansi-effect25','simulated')
            self.assertFalse({20, 21, 22} & driver.build_routes().keys())
            self.assertTrue(set(range(60, 68)) <= driver.build_routes().keys())
            raw.assert_not_called()

    def test_event_observer_receives_native_event_without_replacing_dispatch(self):
        b=Broker()
        b.delivery='ready'
        observed=[]
        driver=DeviceDriver(b,PROFILE,'hardware',event_observer=observed.append)
        event=SimpleNamespace(event_type=EventType.MODE_CHANGED,mode_active=True)
        driver.protocol=MagicMock()
        driver.protocol.service.return_value=[event]
        driver.rgb=MagicMock()
        driver.tick()
        self.assertEqual(observed,[event])
        self.assertTrue(b.active)


if __name__ == '__main__':
    unittest.main()
