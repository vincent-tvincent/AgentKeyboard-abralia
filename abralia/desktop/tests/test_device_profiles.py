# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from abralia.device_profile import load_profile_data
from abralia.interaction import (
    ControlId,
    EventType,
    HostInteractionController,
    HostInteractionProtocolClient,
    Opcode,
    ProtocolError,
    StatusFlags,
)
from abralia.interaction.transport import AmbiguousDeviceError, find_profile_interface
from abralia.layout import CompatibilityLayoutError, load_compatibility_layout
from abralia.rgb import PhysicalSceneBuilder, RgbController, Srgb8, load_profile
from abralia.rgb.adapters.keychron_effect25 import FrameState, KeychronEffect25Adapter
from abralia.rgb.errors import CapabilityError, ProfileValidationError, TransportError
from abralia.rgb.led_mapper import DeviceFrame, LedColor
from abralia.rgb.profiles import EncoderDirection, EncoderPosition
from profile_fixtures import REFERENCE, REFERENCE_SOURCE, TINY
from test_coordinator import FakeProducer, event
from test_interaction_client import FakeFirmwareTransport
from test_keychron_adapter import DEVICE, FakeTransport
from test_shared_hid import FakeSharedHidDevice, rgb_effect_event

from abralia import (
    DeviceProfileError,
    SharedHidError,
    SharedHidMode,
    SharedKeyboardCoordinator,
    SharedKeyboardState,
    SharedRawHidSession,
    load_device_profile,
)

SOURCES = (
    REFERENCE_SOURCE,
    "builtin:keychron-v3-ansi-effect25",
    "builtin:keychron-v3-ansi-encoder-effect25",
)


def device_for(profile):
    return replace(DEVICE, **vars_for_match(profile))


def vars_for_match(profile):
    match = profile.device_match
    return dict(
        vendor_id=match.vendor_id,
        product_id=match.product_id,
        usage_page=match.usage_page,
        usage=match.usage,
    )


def rgb_mutations(requests):
    return [r for r in requests if r[0] == 7 or r[:2] in ([0xA8, 8], [0xA8, 10])]


class DeviceProfileTests(unittest.TestCase):
    def test_all_profiles_have_correct_metadata_and_geometry(self):
        expected = (
            (0x0F30, 17, 1, (0, 16)),
            (0x0330, 16, 0, (3, 14)),
            (0x0331, 16, 1, (3, 14)),
        )
        for source, (pid, columns, encoders, toggle) in zip(
            SOURCES, expected, strict=True
        ):
            with self.subTest(source=source):
                full = load_profile(source)
                profile = load_device_profile(source)
                self.assertEqual(profile, full.device_profile)
                self.assertEqual(profile.device_match.product_id, pid)
                self.assertEqual(profile.keymap.matrix_columns, columns)
                self.assertEqual(profile.keymap.encoder_count, encoders)
                self.assertEqual(profile.require_interaction().toggle_matrix, toggle)
                self.assertEqual(len(full.rgb_elements), 87)
                self.assertEqual(
                    sum(e.encoder is not None for e in full.elements), 2 * encoders
                )
                self.assertEqual(
                    full.element_by_id[full.interaction_toggle_element_id()].matrix,
                    toggle,
                )
                if columns == 16:
                    for element_id, matrix in {
                        "PAGE_UP": (3, 15),
                        "PAGE_DOWN": (3, 12),
                        "RIGHT": (4, 14),
                    }.items():
                        self.assertEqual(full.element_by_id[element_id].matrix, matrix)
                    self.assertEqual(
                        full.element_by_id["F1"].geometry.x, 1.25 if encoders else 2
                    )
                    self.assertEqual(
                        full.interaction_toggle_element_id(), "LIGHTING_KEY"
                    )
        with self.assertRaises(FrozenInstanceError):
            REFERENCE.expected_led_count = 1

    def test_metadata_loader_does_not_require_rgb_geometry(self):
        data = load_profile_data(REFERENCE_SOURCE)
        for name in ("elements", "regions", "aliases", "semantic_bindings"):
            data.pop(name, None)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "metadata.json"
            source.write_text(json.dumps(data))
            self.assertEqual(load_device_profile(source), REFERENCE)
            with self.assertRaises(ProfileValidationError):
                load_profile(source)
            data["elements"] = "deliberately not RGB geometry"
            source.write_text(json.dumps(data))
            self.assertEqual(load_device_profile(source), REFERENCE)

    def test_invalid_toggle_and_missing_interaction(self):
        data = load_profile_data(REFERENCE_SOURCE)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "profile.json"
            for toggle in ([6, 0], [0, 17], [-1, 0], [0], [True, 0], "0,16"):
                with self.subTest(toggle=toggle):
                    data["interaction"] = {"toggle_matrix": toggle}
                    source.write_text(json.dumps(data))
                    with self.assertRaises(DeviceProfileError):
                        load_device_profile(source)
            del data["interaction"]
            source.write_text(json.dumps(data))
            full = load_profile(source)
            transport = FakeTransport(profile=full.device_profile)
            adapter = KeychronEffect25Adapter(
                transport, DEVICE, profile=full.device_profile
            )
            adapter.capabilities()  # RGB-only still works.
            self.assertFalse(any(r[:3] == [8, 0, 2] for r in transport.requests))
            with self.assertRaises(DeviceProfileError):
                HostInteractionProtocolClient(
                    FakeFirmwareTransport(), profile=full.device_profile
                )
            self.assertFalse(rgb_mutations(transport.requests))

    def test_profiles_are_required_by_public_entrypoints(self):
        for function in (
            load_profile,
            load_device_profile,
            RgbController.open,
            SharedRawHidSession.open_profile,
            HostInteractionProtocolClient.open_profile,
        ):
            with (
                self.subTest(function=function.__qualname__),
                self.assertRaises(TypeError),
            ):
                function()

    def test_unknown_model_identity_is_not_filtered_out(self):
        profile = replace(
            REFERENCE,
            profile_id="synthetic-compatible-keyboard",
            device_match=replace(
                REFERENCE.device_match, vendor_id=0x1234, product_id=0xBEEF
            ),
        )
        device = device_for(profile)
        with patch(
            "abralia.rgb.adapters.keychron_effect25.enumerate_hid_devices",
            return_value=[DEVICE, device],
        ):
            self.assertEqual(KeychronEffect25Adapter.discover(profile), [device])
        self.assertEqual(find_profile_interface(profile, [DEVICE, device]), device)
        with (
            patch("abralia.rgb.transport.enumerate_hid_devices", return_value=[device]),
            patch.object(SharedRawHidSession, "open_path") as opened,
        ):
            SharedRawHidSession.open_profile(profile)
            self.assertEqual(opened.call_args.args, (device.path, device))
        with self.assertRaises(AmbiguousDeviceError):
            find_profile_interface(profile, [device, device])
        self.assertEqual(
            find_profile_interface(profile, [device, device], device_index=1), device
        )
        with (
            patch(
                "abralia.rgb.transport.enumerate_hid_devices",
                return_value=[device, device],
            ),
            self.assertRaises(SharedHidError),
        ):
            SharedRawHidSession.open_profile(profile)
        transport = FakeTransport(profile=profile)
        adapter = KeychronEffect25Adapter(transport, device, profile=profile)
        self.assertTrue(adapter.capabilities().guarded_frames)

    def test_wrong_identity_profile_or_led_count_rejected_before_writes(self):
        transport = FakeTransport(profile=REFERENCE)
        with self.assertRaises(CapabilityError):
            KeychronEffect25Adapter(
                transport, replace(DEVICE, product_id=1), profile=REFERENCE
            )
        self.assertEqual(transport.requests, [])
        adapter = KeychronEffect25Adapter(transport, DEVICE, profile=REFERENCE)
        with self.assertRaises(CapabilityError):
            RgbController(adapter, load_profile(SOURCES[1]))
        self.assertEqual(transport.requests, [])
        transport.colors.pop()
        with self.assertRaises(CapabilityError):
            adapter.capabilities()
        self.assertFalse(rgb_mutations(transport.requests))

    def test_synthetic_led_count_drives_frames_and_restoration(self):
        profile = replace(
            REFERENCE, expected_led_count=3, profile_id="three-led-fixture"
        )
        transport = FakeTransport(profile=profile)
        adapter = KeychronEffect25Adapter(transport, DEVICE, profile=profile)
        snapshot = adapter.snapshot()
        frame = DeviceFrame(tuple(LedColor(i, Srgb8(20, 40, 60)) for i in range(3)))
        adapter.submit_frame(frame, brightness_ceiling=100)
        adapter.clear()
        self.assertEqual(len(transport.colors), 3)
        self.assertTrue(all(color.value == 0 for color in transport.colors))
        adapter.restore(snapshot)
        self.assertEqual(tuple(transport.colors), snapshot.payload.colors)

    def test_stock_firmware_has_no_rgb_frame_endpoint(self):
        transport = FakeTransport(profile=REFERENCE)
        original = transport.transact

        def stock(request, matcher, timeout_ms=1000):
            if list(request)[:3] == [8, 0, 1]:
                raise TransportError("No Abralia frame endpoint")
            return original(request, matcher, timeout_ms)

        transport.transact = stock
        adapter = KeychronEffect25Adapter(transport, DEVICE, profile=REFERENCE)
        with self.assertRaises(CapabilityError):
            adapter.capabilities()
        self.assertFalse(rgb_mutations(transport.requests))

    def test_interaction_checks_capabilities_before_claiming(self):
        transport = FakeFirmwareTransport(profile=TINY)
        client = HostInteractionProtocolClient(transport, profile=REFERENCE)
        with self.assertRaises(ProtocolError):
            client.claim_session()
        self.assertEqual([r[4] for r in transport.requests], [Opcode.GET_CAPABILITIES])
        self.assertEqual(transport.session_token, 0)
        with self.assertRaises(ProtocolError):
            with client:
                self.fail("Mismatched runtime must not open")
        self.assertTrue(transport.closed)

    def test_reserved_toggle_and_encoder_bounds_follow_each_profile(self):
        for source in SOURCES:
            profile = load_device_profile(source)
            transport = FakeFirmwareTransport(profile=profile)
            client = HostInteractionProtocolClient(transport, profile=profile)
            controller = HostInteractionController(client)
            toggle = ControlId.key(*profile.require_interaction().toggle_matrix)
            self.assertEqual(controller.toggle_control, toggle)
            client.claim_session(42)
            before = len(transport.requests)
            with self.assertRaises(ProtocolError):
                controller.set_controls([toggle], binding_id=1)
            self.assertEqual(len(transport.requests), before)
            if profile.keymap.encoder_count == 0:
                with self.assertRaises(ProtocolError):
                    controller.set_controls(
                        [ControlId.encoder_clockwise(0)], binding_id=1
                    )
            controller.set_controls([ControlId.key(1, 15)], binding_id=1)
            client.close()

    def test_live_via_lookup_uses_profile_matrix_stride(self):
        for source in SOURCES:
            full = load_profile(source)
            profile = full.device_profile
            transport = FakeTransport(profile=profile)
            row, col = full.element_by_id["PAGE_UP"].matrix
            transport.keymap[1][row][col] = 0x1234
            adapter = KeychronEffect25Adapter(
                transport, device_for(profile), profile=profile
            )
            values = adapter.read_matrix_keycodes(
                (1,), rows=6, columns=profile.keymap.matrix_columns
            )
            self.assertEqual(values[(1, row, col)], 0x1234)
            if not profile.keymap.encoder_count:
                with self.assertRaises(CapabilityError):
                    adapter.read_encoder_keycodes(
                        (0,), (EncoderPosition(0, EncoderDirection.CLOCKWISE),)
                    )

    def test_compatibility_overlay_cannot_change_hardware_metadata(self):
        for source in SOURCES:
            profile = load_profile(source)
            data = {
                "schema_version": 1,
                "profile_id": profile.profile_id,
                "regions": [
                    {
                        "id": "custom",
                        "rows": [[{"matrix": [1, 15]}]],
                        "strategies": ["row_key_index"],
                    }
                ],
            }
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "layout.json"
                path.write_text(json.dumps(data))
                overlay = load_compatibility_layout(profile, path)
                self.assertEqual(
                    overlay.apply_to_profile(profile).device_profile,
                    profile.device_profile,
                )
                changed = copy.deepcopy(data)
                changed["interaction"] = {"toggle_matrix": [0, 0]}
                path.write_text(json.dumps(changed))
                with self.assertRaises(CompatibilityLayoutError):
                    load_compatibility_layout(profile, path)

    def test_shared_rendering_event_routing_and_recovery_for_each_profile(self):
        for source in SOURCES:
            full = load_profile(source)
            profile = full.device_profile
            for mode in SharedHidMode:
                with self.subTest(source=source, mode=mode):
                    rgb_firmware = FakeTransport(profile=profile)
                    input_firmware = FakeFirmwareTransport(profile=profile)
                    input_firmware.extra_status_flags = (
                        StatusFlags.RGB_EFFECT_25_SELECTED
                    )

                    def handler(request):
                        if request[:3] in (bytes([7, 0, 2]), bytes([8, 0, 2])):
                            return input_firmware.transact(request, lambda _: True)
                        # RGB packets are padded by Raw HID; this fixture expects
                        # original request lengths for its one-byte VIA query.
                        packet = request[:1] if request[0] == 0x11 else request
                        return rgb_firmware.transact(packet, lambda _: True)

                    device = FakeSharedHidDevice(handler)
                    with SharedRawHidSession(
                        device, device_for(profile), mode=mode
                    ) as session:
                        adapter = KeychronEffect25Adapter(
                            session.rgb_transport(),
                            session.device_info,
                            profile=profile,
                        )
                        protocol = HostInteractionProtocolClient(
                            session.interaction_transport(), profile=profile
                        )
                        with RgbController(adapter, full) as rgb, protocol:
                            producer = FakeProducer()
                            coordinator = SharedKeyboardCoordinator(
                                rgb, protocol, producer
                            )
                            self.assertEqual(
                                coordinator.initialize().current,
                                SharedKeyboardState.STANDBY,
                            )
                            coordinator.handle_event(
                                event(EventType.MODE_CHANGED, True)
                            )
                            self.assertEqual(
                                coordinator.state, SharedKeyboardState.ACTIVE
                            )
                            device.before_response.append(
                                rgb_effect_event(protocol.session_token, False)
                            )
                            scene = PhysicalSceneBuilder().build(
                                "test", {"PAGE_UP": Srgb8(40, 100, 180)}
                            )
                            lease = rgb.display([scene], brightness_ceiling=160)
                            lit = [
                                i
                                for i, color in enumerate(rgb_firmware.colors)
                                if color.value
                            ]
                            self.assertEqual(
                                lit, [full.element_by_id["PAGE_UP"].led_address]
                            )
                            self.assertEqual(
                                len(rgb_firmware.colors), profile.expected_led_count
                            )
                            incoming = protocol.read_event(50)
                            self.assertIsNotNone(incoming)
                            self.assertEqual(
                                incoming.event_type, EventType.RGB_EFFECT_CHANGED
                            )
                            rgb_firmware.effect = 23
                            rgb_firmware.frame_state = FrameState.AWAITING
                            rgb_firmware.pending = False
                            rgb_firmware.active_valid = False
                            coordinator.handle_event(incoming)
                            self.assertEqual(
                                coordinator.state, SharedKeyboardState.RGB_SUSPENDED
                            )
                            self.assertFalse(lease.generation == rgb._lease_generation)
                            rgb_firmware.effect = 25
                            coordinator.handle_event(
                                event(EventType.RGB_EFFECT_CHANGED, True)
                            )
                            self.assertEqual(
                                coordinator.state, SharedKeyboardState.STANDBY
                            )
                    self.assertEqual(device.close_count, 1)
                    self.assertEqual(rgb_firmware.effect, 25)


if __name__ == "__main__":
    unittest.main()
