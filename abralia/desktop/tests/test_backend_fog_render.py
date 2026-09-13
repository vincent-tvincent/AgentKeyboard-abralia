# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Fog must share the production scene without changing other controls or V."""

import unittest
from unittest.mock import patch

from abralia.backend.core import Broker, Caller
from abralia.backend.render import Renderer
from abralia.rgb import load_profile
from test_backend_brightness_reference import output_values


class FogSceneTests(unittest.TestCase):
    def scene(self, background=50, active=True):
        self.now = 0.
        self.b = Broker(clock=lambda: self.now)
        self.b.set_background_brightness(background)
        self.b.set_active(active)
        self.profile = load_profile('builtin:keychron-v3-8k-ansi-encoder-effect25')
        self.r = Renderer(self.profile)
        self.owners = []
        for index in range(2):
            owner = Caller(f'fog-fixture:{index}')
            token = self.b.call(owner, 'acquire_slot', {'label':'Fog fixture', 'idempotency_key':'start'})['allocation']['slot_token']
            self.b.call(owner, 'set_notification', {'slot_token':token, 'enabled':True, 'idempotency_key':'notice'})
            self.owners.append((owner, token))

    def advance(self, seconds):
        end = self.now + seconds
        while self.now < end:
            self.now = min(end, self.now + .1)
            self.b.step()
            self.r.frame(self.b)

    def without_fog(self):
        with patch.object(self.b, 'notification_visuals', return_value={'presentation':None, 'orbs':[]}):
            return Renderer(self.profile).frame(self.b).payload

    def test_multiple_orbs_do_not_modulate_unrelated_keys_after_firmware_normalization(self):
        for background in (0, 25, 50, 100):
            for active in (False, True):
                with self.subTest(background=background, active=active):
                    self.scene(background, active)
                    self.advance(23)
                    self.assertEqual(len(self.b.notification_visuals()['orbs']), 2)
                    before = self.without_fog()
                    for offset in (0, .3, .7):
                        self.advance(offset)
                        actual = self.r.frame(self.b).payload
                        baseline = self.without_fog()
                        for limit in (0, 77, 255):
                            old = output_values(baseline, self.profile, limit)
                            new = output_values(actual, self.profile, limit)
                            for key in set(old) - set(self.r.fog_points):
                                self.assertEqual(new[key], old[key], key)
                        self.assertEqual(actual.background, before.background)

    def test_navigation_page_and_question_cues_win_over_fog(self):
        self.scene()
        self.advance(23)
        self.b.toggle_navigation()
        self.b.cycle_knob()
        self.b.turn_page(1)
        baseline = self.without_fog()
        actual = self.r.frame(self.b).payload
        keys = ('UP', 'DOWN', 'LEFT', 'RIGHT', 'HOME', 'END', 'PAGE_UP', 'PAGE_DOWN',
                'INSERT', 'DELETE', 'ENTER', '1', 'SCREENSHOT', 'SCROLL_LOCK', 'PAUSE')
        for key in keys:
            if key in baseline.colors:
                self.assertEqual(actual.colors.get(key, actual.background), baseline.colors[key], key)

    def test_visual_timeout_restores_background_without_answering_or_rearming(self):
        self.scene()
        self.advance(23)
        self.advance(181)
        self.assertEqual(self.b.notification_visuals()['orbs'], [])
        frame = self.r.frame(self.b).payload
        baseline = self.without_fog()
        self.assertEqual(frame, baseline)
        self.assertEqual(self.b.slots[1].notification.status, 'active')
        self.assertEqual(self.b.slots[2].notification.status, 'queued')

    def test_condensation_endpoint_matches_combined_pool_on_main_and_navigation_keys(self):
        self.scene()
        self.advance(23)
        visuals = self.b.notification_visuals()
        # Force overlap across the typing/navigation border. Rendering the
        # formation endpoint must use the same simultaneous light mixture.
        for body in self.r.fog.bodies.values():
            body.x, body.y = self.r.fog_points['ENTER']
        ordinary = self.r.frame(self.b).payload
        orb = visuals['orbs'][1]
        visuals['presentation'] = dict(orb, phase='condensing', elapsed=2., progress=1.)
        with patch.object(self.b, 'notification_visuals', return_value=visuals):
            endpoint = self.r.frame(self.b).payload
        for key in self.r.fog_points:
            self.assertEqual(endpoint.colors.get(key, endpoint.background),
                             ordinary.colors.get(key, ordinary.background), key)

    def test_selected_background_fades_into_remaining_fog_without_white_flash(self):
        self.scene()
        self.advance(23)
        self.b.select_slot(self.owners[0][1])
        self.advance(.6)
        # The surviving body's center sits on A; freeze time/physics across
        # the endpoint comparison so this tests composition, not movement.
        for body in self.r.fog.bodies.values():
            body.x, body.y = self.r.fog_points['A']
        end = self.b.background_return_at + self.b.config.fade_seconds
        self.now = end - .000001
        near = self.r.frame(self.b).payload
        self.now = end
        done = self.r.frame(self.b).payload
        self.assertNotEqual(done.colors['A'], done.background)
        self.assertEqual(near.colors['A'], done.colors['A'])


if __name__ == '__main__':
    unittest.main()
