# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import contextlib
import colorsys
import io
import math
import unittest
from unittest.mock import patch

from abralia.rgb import RgbController, Srgb8, load_profile
from abralia.rgb.adapters.base import DeviceSnapshot
from abralia.rgb.compatibility import AdapterCapabilities
from f1_breathing_notification import breathing_color, build_phases, parse_args, run
from f1_notification_interaction_demo import (
    Demo, Producer, open_task, run as run_interaction,
    parse_args as interaction_args,
)
from main_area_notification_flash import FLASH_COLOR


PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"
WHITE = Srgb8(255, 255, 255)


class RecordingAdapter:
    adapter_id = "keychron-effect25-rawhid"
    adapter_version = 1

    def __init__(self, profile):
        self.profile = profile.device_profile
        self.frames = []
        self.restored = None
        self.closed = False
        self.refreshes = 0

    def capabilities(self):
        return AdapterCapabilities(True, True, True, True, 2.0)

    def snapshot(self):
        return DeviceSnapshot(self.adapter_id, {"saved": True})

    def submit_frame(self, frame, *, brightness_ceiling):
        self.frames.append(frame)
        return len(self.frames)

    def refresh(self):
        self.refreshes += 1
        return self.refreshes

    def restore(self, snapshot):
        self.restored = snapshot

    def close(self):
        self.closed = True


class F1BreathingTests(unittest.TestCase):
    def setUp(self):
        self.args = parse_args(["--profile", PROFILE])
        self.phases = build_phases(self.args)

    def test_all_white_initial_frame_and_only_f1_then_changes(self):
        initial = self.phases[0].frames[0].payload
        self.assertEqual(initial.background, WHITE)
        self.assertEqual(initial.colors, {})
        self.assertEqual(self.phases[1].frames[0].payload.colors, {"F1": self.args.status_color})

    def test_fill_peak_and_first_breath_are_identical(self):
        self.assertEqual(self.phases[4].frames[-1].payload, self.phases[5].frames[0].payload)
        self.assertEqual(self.phases[5].frames[0].payload, self.phases[6].frames[0].payload)
        self.assertEqual(breathing_color(self.args, 0), FLASH_COLOR)

    def test_breath_never_blacks_out_and_settles_to_status_color(self):
        colors = [breathing_color(self.args, i / 30) for i in range(361)]
        self.assertTrue(all(max(c.red, c.green, c.blue) > 0 for c in colors))
        peak = breathing_color(self.args, 8)
        trough = breathing_color(self.args, 9)
        self.assertGreater(peak.blue, trough.blue)
        self.assertGreater(peak.blue, peak.green)
        self.assertGreater(peak.green, peak.red)
        self.assertLess(peak.blue, self.args.status_color.blue)

    def test_f1_constant_and_outside_region_stays_white_through_compiler(self):
        profile = load_profile(PROFILE)
        adapter = RecordingAdapter(profile)
        with RgbController(adapter, profile) as controller:
            for phase in self.phases:
                for scene in phase.frames:
                    physical, _, _ = controller.compile([scene])
                    self.assertEqual(physical.colors["ESC"], WHITE)
                    self.assertEqual(physical.colors["F2"], WHITE)
                    self.assertEqual(physical.colors["SCROLL_LOCK"], WHITE)
                    expected_f1 = WHITE if phase.name == "WHITE_EVERYWHERE" else self.args.status_color
                    self.assertEqual(physical.colors["F1"], expected_f1)
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.restored.payload, {"saved": True})

    def test_notification_clear_returns_region_to_white_retaining_slot(self):
        for color in self.phases[7].frames[-1].payload.colors.values():
            self.assertIn(color, (WHITE, self.args.status_color))
        self.assertEqual(self.phases[-1].frames[0].payload.colors, {"F1": self.args.status_color})

    def test_dry_run_cannot_open_device(self):
        self.args.dry_run = True
        with patch("f1_breathing_notification.RgbController.open", side_effect=AssertionError("hardware open")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(self.args), 0)

    def test_invalid_and_overlapping_regions_fail_before_hardware(self):
        for field, value in (("slot_element", "unknown"), ("slot_element", "ESC"),
                             ("region", "full_keyboard"), ("fps", math.nan),
                             ("breath_seconds", 2), ("color_blend_seconds", math.inf)):
            args = parse_args(["--profile", PROFILE])
            setattr(args, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_phases(args)

    def test_runtime_exception_still_closes_and_restores_controller(self):
        profile = load_profile(PROFILE)
        adapter = RecordingAdapter(profile)
        controller = RgbController(adapter, profile)
        with patch("f1_breathing_notification.RgbController.open", return_value=controller):
            with patch("f1_breathing_notification.play_demo", side_effect=KeyboardInterrupt):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
                    run(self.args)
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.restored.payload, {"saved": True})


class InteractiveNotificationTests(unittest.TestCase):
    def make_demo(self, background="white"):
        return Demo(interaction_args(["--profile", PROFILE,
                                      "--pickup-background", background]))

    def test_inactive_does_not_black_out_or_accept_actions(self):
        demo = self.make_demo()
        self.assertEqual(demo.frame(0).payload.background, WHITE)
        self.assertEqual(demo.frame(0).payload.colors, {})
        now = demo.notification_at + demo.onset_seconds + 4
        frame = demo.frame(now).payload
        self.assertEqual(frame.background, WHITE)
        self.assertEqual(frame.colors["F1"], Srgb8(88, 160, 255))
        self.assertIn("PAUSE", frame.colors)
        self.assertNotIn("SCROLL_LOCK", frame.colors)
        self.assertFalse(demo.act("mute", 1, now))

    def test_activated_pending_controls_are_red_and_green(self):
        demo = self.make_demo()
        now = demo.notification_at + 1
        demo.set_active(True, now)
        colors = demo.frame(now).payload.colors
        self.assertGreater(colors["PAUSE"].red, 0)
        self.assertEqual((colors["PAUSE"].green, colors["PAUSE"].blue), (0, 0))
        self.assertEqual(colors["SCROLL_LOCK"], Srgb8(0, 255, 0))
        self.assertNotIn("SCREENSHOT", colors)
        self.assertNotIn(demo.profile.element_by_id["SCREENSHOT"].matrix,
                         [(binding.entry.control_id.primary, binding.entry.control_id.secondary)
                          for binding in demo.bindings(now)])
        self.assertIn(demo.controls["mute"], [b.entry.control_id for b in demo.bindings(now)])

    def test_toggle_position_follows_each_profile_without_keycode_lookup(self):
        for name, expected in (("keychron-v3-8k-ansi-encoder-effect25", 0x0010),
                               ("keychron-v3-ansi-effect25", 0x030E),
                               ("keychron-v3-ansi-encoder-effect25", 0x030E)):
            with self.subTest(profile=name):
                demo = Demo(interaction_args(["--profile", "builtin:" + name]))
                self.assertEqual(int(demo.controls["mute"]), expected)
                controls = [int(binding.entry.control_id) for binding in demo.bindings(20)]
                self.assertIn(expected, controls)
                toggle = demo.profile.interaction_toggle_element_id()
                self.assertIn(toggle, demo.frame(20).payload.colors)
                demo.set_active(True, 20)
                self.assertIn(toggle, demo.frame(20).payload.colors)

    def test_inactive_notification_cue_breathes_and_survives_mute_until_pickup(self):
        demo = self.make_demo()
        now = demo.notification_at + 1
        peak = demo.frame(now).payload.colors["PAUSE"]
        trough = demo.frame(now + demo.args.breath_period / 2).payload.colors["PAUSE"]
        self.assertEqual(peak, FLASH_COLOR)
        self.assertEqual(trough, WHITE)
        for i in range(31):
            color = demo.frame(now + demo.args.breath_period * i / 30).payload.colors["PAUSE"]
            self.assertEqual(max(color.red, color.green, color.blue), 255)
        demo.set_active(True, now)
        demo.act("mute", 1, now)
        demo.set_active(False, now)
        self.assertEqual(demo.frame(now).payload.colors["PAUSE"], peak)
        self.assertNotIn("A", demo.frame(now).payload.colors)  # No main-region re-alert.
        demo.set_active(True, now)
        demo.act("pickup", 1, now)
        demo.set_active(False, now)
        self.assertNotIn("PAUSE", demo.frame(now).payload.colors)

    def test_active_toggle_breathes_red_pending_and_white_after_pickup(self):
        demo = self.make_demo()
        now = demo.notification_at + 1
        demo.set_active(True, now)
        peak = demo.frame(now).payload.colors["PAUSE"]
        trough = demo.frame(now + demo.args.breath_period / 2).payload.colors["PAUSE"]
        self.assertGreater(peak.red, trough.red)
        self.assertGreater(trough.red, 0)
        self.assertEqual((trough.green, trough.blue), (0, 0))
        demo.act("pickup", 1, now)
        peak = demo.frame(now).payload.colors["PAUSE"]
        trough = demo.frame(now + demo.args.breath_period / 2).payload.colors["PAUSE"]
        self.assertGreater(peak.red, trough.red)
        self.assertGreater(trough.red, 0)
        for color in (peak, trough):
            self.assertEqual(color.red, color.green)
            self.assertEqual(color.green, color.blue)
        self.assertTrue(demo.active)
        self.assertNotIn("SCROLL_LOCK", demo.frame(now).payload.colors)

    def test_active_toggle_cue_exists_before_notification_without_mute_binding(self):
        demo = self.make_demo()
        demo.set_active(True, 0)
        color = demo.frame(0).payload.colors["PAUSE"]
        self.assertGreater(color.red, 0)
        self.assertEqual(color.red, color.green)
        self.assertEqual(color.green, color.blue)
        self.assertEqual(demo.bindings(0), ())

    def test_inactive_saturation_scales_slot_but_preserves_notification_and_restores_on_entry(self):
        demo = self.make_demo()
        # Cover onset plus blended breathing; compare in HSV independently of the renderer.
        for now in (demo.notification_at + .1, demo.notification_at + .2,
                    demo.notification_at + .4, demo.notification_at + .5,
                    demo.notification_at + 5):
            demo.set_active(True, now)
            active = demo.frame(now).payload
            demo.set_active(False, now)
            inactive = demo.frame(now).payload
            self.assertEqual(inactive.background, active.background)
            for element, color in active.colors.items():
                if element in ("PAUSE", "SCROLL_LOCK"):
                    continue  # Different semantic cues, tested separately.
                if element in demo.region_ids:
                    self.assertEqual(inactive.colors[element], color)
                    continue
                hsva = colorsys.rgb_to_hsv(color.red / 255, color.green / 255, color.blue / 255)
                reduced = inactive.colors[element]
                hsvi = colorsys.rgb_to_hsv(reduced.red / 255, reduced.green / 255, reduced.blue / 255)
                self.assertAlmostEqual(hsvi[1], hsva[1] * .75, delta=.015)
                self.assertEqual(hsvi[2], hsva[2])
            demo.set_active(True, now)
            self.assertEqual(demo.frame(now).payload, active)

    def test_inactive_saturation_configuration_and_invalid_values(self):
        for percent, expected in ((0, WHITE), (50, Srgb8(144, 192, 255)),
                                  (75, Srgb8(88, 160, 255)), (100, Srgb8(32, 128, 255))):
            demo = Demo(interaction_args(["--profile", PROFILE, "--inactive-saturation-percent", str(percent)]))
            self.assertEqual(demo.frame(demo.args.idle_seconds).payload.colors["F1"], expected)
            now = demo.notification_at + 1
            inactive = demo.frame(now).payload.colors
            self.assertEqual(inactive["PAUSE"], FLASH_COLOR)
            demo.set_active(True, now)
            active = demo.frame(now).payload.colors
            self.assertEqual({key: inactive[key] for key in demo.region_ids},
                             {key: active[key] for key in demo.region_ids})
        for value in ("-1", "101", "nan", "inf"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "inactive-saturation-percent"):
                Demo(interaction_args(["--profile", PROFILE, "--inactive-saturation-percent", value]))

    def test_old_firmware_blocks_live_demo_before_session_claim_or_rgb(self):
        args = interaction_args(["--profile", PROFILE])
        with patch("f1_notification_interaction_demo.SharedRawHidSession.open_profile"), \
             patch("f1_notification_interaction_demo.KeychronEffect25Adapter"), \
             patch("f1_notification_interaction_demo.HostInteractionProtocolClient") as protocol, \
             patch("f1_notification_interaction_demo.RgbController") as rgb:
            protocol.return_value.get_capabilities.return_value.supports_toggle_single_tap = False
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "matching firmware"):
                run_interaction(args)
            protocol.return_value.__enter__.assert_not_called()
            rgb.assert_not_called()

    def test_muted_notification_remains_selectable_and_pickup_works_without_realerting(self):
        demo = self.make_demo()
        now = demo.notification_at + 1
        demo.set_active(True, now)
        self.assertTrue(demo.act("mute", 1, now))
        self.assertFalse(demo.act("mute", 1, now))
        self.assertEqual([binding.entry.binding_id for binding in demo.bindings(now)], [1, 2, 3])
        self.assertEqual(set(demo.frame(now).payload.colors), {"F1", "PAUSE", "SCROLL_LOCK"})
        self.assertEqual(demo.frame(now).payload.colors["F1"], demo.args.status_color)
        # The still-available green pickup indicator accompanies the white base.
        self.assertEqual(demo.frame(now).payload.colors["SCROLL_LOCK"], Srgb8(0, 255, 0))
        now += 4
        self.assertTrue(demo.pending(now))
        self.assertEqual(demo.outcome, "muted")
        self.assertTrue(demo.act("select", 1, now))
        self.assertEqual(demo.frame(now).payload.colors["A"], demo.args.status_color)
        self.assertEqual(demo.outcome, "muted")
        self.assertFalse(demo.act("pickup", 0, now))
        self.assertTrue(demo.act("pickup", 1, now))

    def test_pickup_background_options_and_exit_to_white(self):
        for background in ("white", "status"):
            with self.subTest(background=background):
                demo = self.make_demo(background)
                now = demo.notification_at + 1
                demo.set_active(True, now)
                self.assertTrue(demo.act("pickup", 1, now))
                self.assertEqual([binding.entry.binding_id for binding in demo.bindings(now)], [1])
                frame = demo.frame(now + 10).payload
                expected = WHITE if background == "white" else demo.args.status_color
                self.assertEqual(frame.colors.get("A", frame.background), expected)
                demo.set_active(False, now + 10)
                self.assertFalse(demo.active)  # Input is normal before the visual fade finishes.
                self.assertEqual(demo.frame(now + 10 + demo.args.return_fade_seconds).payload.colors,
                                 {"F1": Srgb8(88, 160, 255)})
                self.assertEqual(demo.frame(now + 10).payload.background, WHITE)

    def test_idle_return_waits_for_mute_or_pickup_and_resets_on_slot_selection(self):
        demo = self.make_demo("status")
        now = demo.notification_at + 1
        demo.set_active(True, now)
        self.assertTrue(demo.act("select", 1, now))
        self.assertFalse(demo.auto_backlight_return_due(now + 100))
        self.assertTrue(demo.act("mute", 1, now + 1))
        self.assertFalse(demo.auto_backlight_return_due(now + 15))
        self.assertTrue(demo.auto_backlight_return_due(now + 16))
        demo.act("select", 1, now + 10)
        self.assertFalse(demo.auto_backlight_return_due(now + 24))
        self.assertTrue(demo.auto_backlight_return_due(now + 25))
        demo.return_backlight_to_white(now + 25)
        self.assertTrue(demo.active)
        self.assertEqual(demo.frame(now + 26).payload.colors.get("A", WHITE), WHITE)
        demo.args.idle_return_seconds = 0
        self.assertFalse(demo.auto_backlight_return_due(now + 1000))

    def test_task_open_uses_exact_uuid_without_shell_or_wait(self):
        target = "11111111-2222-3333-4444-555555555555"
        with patch("f1_notification_interaction_demo.subprocess.Popen") as popen:
            process = open_task(target)
            self.assertIs(process, popen.return_value)
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/open", f"codex://threads/{target}"])
            self.assertNotIn("shell", popen.call_args.kwargs)
            process.wait.assert_not_called()
        with patch("f1_notification_interaction_demo.subprocess.Popen") as popen:
            with self.assertRaises(ValueError):
                open_task("settings; arbitrary command")
            popen.assert_not_called()

    def test_reentry_cancels_post_pickup_exit_countdown(self):
        demo = self.make_demo("status")
        now = demo.notification_at + 1
        demo.set_active(True, now)
        demo.act("pickup", 1, now)
        demo.set_active(False, now + 1)
        self.assertIsNotNone(demo.exited_after_pickup_at)
        demo.set_active(True, now + 2)
        self.assertIsNone(demo.exited_after_pickup_at)

    def test_print_screen_substitution_and_invalid_action_layout_are_rejected(self):
        for element in ("SCREENSHOT", "F1", "SCROLL_LOCK", "A", "missing"):
            with self.subTest(element=element), self.assertRaises(ValueError):
                Demo(interaction_args(["--profile", PROFILE, "--mute-element", element]))

    def test_standby_producer_renders_white_and_refreshes_static_scene(self):
        demo = self.make_demo()
        adapter = RecordingAdapter(demo.profile)
        with RgbController(adapter, demo.profile) as rgb:
            producer = Producer(rgb, demo, 0)
            producer.resume_standby(restart=True)
            producer.tick(0)
            producer.tick(1.1)
            self.assertEqual(len(adapter.frames), 1)
            self.assertEqual(adapter.refreshes, 1)
            self.assertFalse(demo.active)
            producer.close()


if __name__ == "__main__":
    unittest.main()
