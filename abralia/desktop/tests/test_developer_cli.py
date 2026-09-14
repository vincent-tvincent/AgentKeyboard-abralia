# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socketserver
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from abralia.developer.cli import main, parser
from abralia.developer.discovery import DeveloperError, describe, fingerprint, scan, select_device, selection_id
from abralia.developer.probe import backend_report, cached_driver_report, capabilities_report, probe
from abralia.developer.profiles import DRAFT_FORMAT, make_draft, read_json, validate_document, write_json
from abralia.device_profile import load_profile_data
from abralia.interaction.protocol import Opcode, parse_capabilities
from abralia.rgb.compatibility import AdapterCapabilities
from abralia.rgb.transport import HidDeviceInfo

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
DEVICE = HidDeviceInfo(b'fixture-raw-interface', 0x3434, 0x0F30, 0xFF60, 0x61, 1,
                       'Fixture keyboard', 'Fixture vendor', 'fixture-serial')


def caps_packet(token=0):
    packet = bytearray(32)
    packet[:6] = bytes([8, 0, 2, 2, Opcode.GET_CAPABILITIES, 0])
    struct.pack_into('<I', packet, 6, token)
    packet[12:16] = bytes([6, 17, 1, 32])
    struct.pack_into('<HHHH', packet, 16, 104, 300, 4000, 30000)
    packet[24:27] = bytes([7, 7, 1])
    return bytes(packet)


class QueryTransport:
    def __init__(self, *, busy=False, echo_frame=False):
        self.requests = []
        self.closed = False
        self.busy, self.echo_frame = busy, echo_frame

    def transact(self, request, matcher, timeout_ms):
        request = bytes(request)
        self.requests.append(request)
        reply = bytearray(32)
        reply[:len(request)] = request
        if request[:5] == bytes([8, 0, 2, 2, 0]):
            reply = caps_packet(0x11223344 if self.busy else 0)
        elif request == bytes([0xA0]):
            reply[1] = 1
        elif request == bytes([0xA8, 5]):
            reply[3] = 87
        elif request == bytes([0xA8, 1]):
            reply[3] = 2
        elif request[:3] == bytes([8, 0, 1]) and not self.echo_frame:
            reply[3:8] = bytes(5)
        if not matcher(bytes(reply)):
            raise RuntimeError('fixture matcher mismatch')
        return bytes(reply)

    def close(self):
        self.closed = True


class DiscoveryTests(unittest.TestCase):
    def test_scan_needs_no_profile_and_never_opens_hid(self):
        with patch('abralia.developer.discovery.enumerate_hid_devices', return_value=[DEVICE]), \
             patch('abralia.rgb.transport.HidApiTransport.open_path') as opener:
            result = scan(vendor_id=0x3434, product_id=0x0F30)
        self.assertEqual(result['device_count'], 1)
        self.assertEqual(result['devices'][0]['device_match'],
                         {'vendor_id': 13364, 'product_id': 3888, 'usage_page': 65376, 'usage': 97})
        self.assertFalse(result['devices'][0]['firmware_verified'])
        opener.assert_not_called()

    def test_selection_uses_current_interface_not_position_or_reused_serial(self):
        other = HidDeviceInfo(b'other-path', *tuple(getattr(DEVICE, k) for k in (
            'vendor_id', 'product_id', 'usage_page', 'usage', 'interface_number', 'product', 'manufacturer', 'serial_number')))
        self.assertNotEqual(selection_id(other), selection_id(DEVICE))
        self.assertEqual(select_device(selection_id(DEVICE), enumerator=lambda: [other, DEVICE]), DEVICE)
        with self.assertRaisesRegex(DeveloperError, 'missing or ambiguous'):
            select_device(selection_id(DEVICE), enumerator=lambda: [other])
        with self.assertRaisesRegex(DeveloperError, 'missing or ambiguous'):
            select_device(selection_id(DEVICE), enumerator=lambda: [DEVICE, DEVICE])
        self.assertFalse(scan(enumerator=lambda: [DEVICE, DEVICE])['devices'][0]['selectable'])

    def test_unknown_usage_is_reported_not_fabricated(self):
        unknown = HidDeviceInfo(b'no-usage', 1, 2, None, None, None, '', '', '')
        item = describe(unknown)
        self.assertEqual(item['missing_match_fields'], ['usage_page', 'usage'])
        self.assertIsNone(item['device_match']['usage'])
        self.assertFalse(item['common_via_rawhid_usage'])

    def test_scan_empty_is_not_an_unsupported_hardware_claim(self):
        result = scan(enumerator=lambda: [])
        self.assertEqual(result['status'], 'ok')
        self.assertIn('permissions', result['note'])


class ProbeTests(unittest.TestCase):
    def test_direct_probe_sends_only_information_queries_and_closes(self):
        transport = QueryTransport()
        report = probe(DEVICE, 'abralia-keychron', owner_check=lambda *a, **k: None,
                       opener=lambda path: transport)
        self.assertTrue(transport.closed)
        self.assertEqual(report['observations']['host_interaction']['matrix_rows'], 6)
        self.assertEqual(report['observations']['keychron_rgb']['led_count'], 87)
        self.assertTrue(report['observations']['keychron_rgb']['guarded_frames'])
        self.assertEqual(len(transport.requests), 5)
        for packet in transport.requests:
            self.assertIn(packet[:2], (bytes([8, 0]), bytes([0xA8, 1]), bytes([0xA8, 5]), bytes([0xA0])))
        self.assertFalse(report['hardware_profile_complete'])

    def test_caps_never_expose_other_clients_token(self):
        result = capabilities_report(parse_capabilities(caps_packet(0x11223344)))
        self.assertTrue(result['session_claimed'])
        self.assertNotIn('session_token', json.dumps(result))
        self.assertNotIn(str(0x11223344), json.dumps(result))

    def test_session_busy_stops_before_further_queries(self):
        transport = QueryTransport(busy=True)
        with self.assertRaises(DeveloperError) as caught:
            probe(DEVICE, 'abralia-keychron', owner_check=lambda *a, **k: None, opener=lambda p: transport)
        self.assertEqual(caught.exception.code, 'device_busy')
        self.assertTrue(transport.closed)
        self.assertEqual(len(transport.requests), 1)

    def test_unknown_frame_command_echo_does_not_advertise_frame_support(self):
        result = probe(DEVICE, 'keychron-rgb', owner_check=lambda *a, **k: None,
                       opener=lambda path: QueryTransport(echo_frame=True))
        self.assertIsNone(result['observations']['keychron_rgb']['guarded_frames'])

    def test_direct_probe_rechecks_disconnection_before_open(self):
        with patch('abralia.developer.discovery.enumerate_hid_devices', return_value=[]), \
             patch('abralia.rgb.transport.HidApiTransport.open_path') as opener:
            with self.assertRaises(DeveloperError) as result:
                probe(DEVICE, 'abralia-v2', owner_check=lambda *a, **k: None)
        self.assertEqual(result.exception.code, 'device_missing')
        opener.assert_not_called()

    def test_backend_cache_is_used_without_a_second_handle(self):
        cached = {'host_interaction': {'matrix_rows': 6}, 'keychron_rgb': {'led_count': 87}, 'observation': 'fixture cache'}
        opener = Mock()
        result = probe(DEVICE, 'abralia-v2', owner_check=lambda *a, **k: cached, opener=opener)
        self.assertEqual(result['source'], 'backend_cache')
        self.assertNotIn('keychron_rgb', result['observations'])
        opener.assert_not_called()
        with self.assertRaisesRegex(DeveloperError, 'already owns'):
            probe(DEVICE, 'abralia-v2', source='direct', owner_check=lambda *a, **k: cached, opener=opener)
        opener.assert_not_called()

    def test_uncertain_owner_missing_backend_and_wrong_interface_do_not_open(self):
        opener = Mock()
        with self.assertRaises(DeveloperError):
            probe(DEVICE, 'abralia-v2', source='backend', owner_check=lambda *a, **k: None, opener=opener)
        with self.assertRaises(DeveloperError):
            probe(DEVICE, 'abralia-v2', owner_check=Mock(side_effect=DeveloperError('uncertain', 'uncertain')), opener=opener)
        keyboard_interface = HidDeviceInfo(b'key', 0x3434, 0x0F30, 1, 6, 0, '', '', '')
        with self.assertRaisesRegex(DeveloperError, 'Raw HID'):
            probe(keyboard_interface, 'abralia-v2', owner_check=Mock(), opener=opener)
        opener.assert_not_called()

    def test_inconsistent_capabilities_are_rejected(self):
        packet = bytearray(caps_packet()); packet[12] = 0
        with self.assertRaises(DeveloperError):
            capabilities_report(parse_capabilities(bytes(packet)))

    def test_driver_cache_is_read_without_queries(self):
        rgb = AdapterCapabilities(True, True, True, True, 2.)
        protocol = SimpleNamespace(capabilities=parse_capabilities(caps_packet()), session_token=0x11223344,
                                   get_capabilities=Mock(side_effect=AssertionError('must not query')))
        driver = SimpleNamespace(mode='hardware', broker=SimpleNamespace(delivery='written'), protocol=protocol,
            rgb=SimpleNamespace(adapter=SimpleNamespace(_capabilities=rgb)), profile=SimpleNamespace(expected_led_count=87))
        report = cached_driver_report(driver)
        self.assertEqual(report['keychron_rgb']['led_count'], 87)
        self.assertTrue(report['host_interaction']['session_claimed'])
        self.assertNotIn(str(0x11223344), json.dumps(report))
        protocol.get_capabilities.assert_not_called()
        driver.broker.delivery = 'suspended'
        self.assertIsNone(cached_driver_report(driver))


class DraftValidationTests(unittest.TestCase):
    def test_passive_draft_is_explicitly_incomplete(self):
        draft = make_draft(DEVICE)
        self.assertEqual(draft['format'], DRAFT_FORMAT)
        self.assertEqual(draft['status'], 'incomplete')
        profile = draft['profile']
        self.assertIsNone(profile['keymap']['matrix_rows'])
        self.assertIsNone(profile['interaction']['toggle_matrix'])
        self.assertFalse(draft['hardware_verified'])
        result, exported = validate_document(draft)
        self.assertEqual(result['status'], 'incomplete')
        self.assertIsNone(exported)

    def test_probe_populates_only_reported_fields(self):
        report = probe(DEVICE, 'abralia-keychron', owner_check=lambda *a, **k: None, opener=lambda p: QueryTransport())
        draft = make_draft(DEVICE, report=report)
        profile = draft['profile']
        self.assertEqual(profile['keymap'], {'matrix_rows': 6, 'matrix_columns': 17, 'encoder_count': 1})
        self.assertEqual(profile['expected_led_count'], 87)
        self.assertIsNone(profile['capabilities']['independent_brightness'])
        self.assertIsNone(profile['interaction']['toggle_matrix'])
        self.assertEqual(profile['elements'], [])
        bad = deepcopy(report); bad['device']['fingerprint']['path_b64'] = 'other'
        with self.assertRaisesRegex(DeveloperError, 'exact scanned'):
            make_draft(DEVICE, report=bad)
        for bad in ({}, {'format': 'abralia-device-probe', 'version': True}, {'device': []}):
            with self.assertRaises(DeveloperError):
                make_draft(DEVICE, report=bad)

    def test_completed_draft_exports_native_profile_without_certifying_hardware(self):
        profile = load_profile_data(PROFILE)
        result, exported = validate_document({'format': DRAFT_FORMAT, 'version': 1, 'profile': profile}, for_agent=True)
        self.assertTrue(result['valid'])
        self.assertFalse(result['hardware_verified'])
        self.assertEqual(exported, profile)
        self.assertNotIn('format', exported)

    def test_agent_validation_is_separate_and_checks_reserved_mode(self):
        profile = load_profile_data(PROFILE)
        profile.pop('interaction')
        self.assertTrue(validate_document(profile)[0]['valid'])
        result, _ = validate_document(profile, for_agent=True)
        self.assertFalse(result['valid'])
        self.assertIn('interaction.toggle_matrix', [e['field'] for e in result['errors']])

    def test_invalid_layout_semantics_and_json_are_rejected_offline(self):
        profile = load_profile_data(PROFILE)
        profile['elements'][1]['led_address'] = 500
        self.assertFalse(validate_document(profile)[0]['valid'])
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'invalid.json'
            for text in ('{"a":1,"a":2}', '{"x":NaN}', '[]'):
                p.write_text(text)
                with self.assertRaises(DeveloperError):
                    read_json(p)

    def test_agent_validation_checks_protocol_ranges_and_required_features(self):
        profile = load_profile_data(PROFILE)
        profile['keymap']['matrix_rows'] = 100
        profile['capabilities']['guarded_frames'] = False
        self.assertTrue(validate_document(profile)[0]['valid'])
        result, _ = validate_document(profile, for_agent=True)
        self.assertFalse(result['valid'])
        fields = {item['field'] for item in result['errors']}
        self.assertIn('keymap.matrix_rows', fields)
        self.assertIn('capabilities.guarded_frames', fields)

    def test_output_creation_preserves_existing_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'profile.json'
            write_json(p, {'new': True})
            before = p.read_bytes()
            with self.assertRaises(DeveloperError):
                write_json(p, {'overwrite': True})
            self.assertEqual(p.read_bytes(), before)
            link = Path(temp) / 'link.json'; link.symlink_to(p)
            with self.assertRaises(DeveloperError):
                write_json(link, {'overwrite': True})
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(p.stat().st_mode & 0o077, 0)


class CliTests(unittest.TestCase):
    def test_json_flag_works_at_all_documented_positions(self):
        for args in (['--json', 'scan'], ['scan', '--json'], ['--json', 'profile', 'validate', PROFILE],
                     ['profile', '--json', 'validate', PROFILE], ['profile', 'validate', PROFILE, '--json']):
            self.assertTrue(parser().parse_args(args).json)

    def test_scan_strings_cannot_inject_terminal_escape_codes(self):
        malicious = HidDeviceInfo(b'fixture', 1, 2, None, None, None, '\x1b[31mBAD\nNAME', '', '')
        output = io.StringIO()
        with patch('abralia.developer.discovery.enumerate_hid_devices', return_value=[malicious]), contextlib.redirect_stdout(output):
            self.assertEqual(main(['scan']), 0)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('\\u001b', output.getvalue())

    def test_invalid_draft_returns_nonzero_json_and_never_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            draft, output = Path(temp) / 'draft.json', Path(temp) / 'output.json'
            write_json(draft, make_draft(DEVICE))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(['profile', 'validate', str(draft), '--output', str(output), '--json'])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'incomplete')
            self.assertFalse(output.exists())

    def test_complete_profile_validation_has_no_hardware_calls(self):
        stdout = io.StringIO()
        with patch('abralia.developer.discovery.enumerate_hid_devices', side_effect=AssertionError('no scan')), \
             patch('abralia.rgb.transport.HidApiTransport.open_path', side_effect=AssertionError('no open')), \
             contextlib.redirect_stdout(stdout):
            self.assertEqual(main(['profile', 'validate', PROFILE, '--for-agent', '--json']), 0)
        self.assertTrue(json.loads(stdout.getvalue())['valid'])


@unittest.skipUnless(hasattr(os, 'getuid'), 'Current Abralia backend discovery uses private Unix sockets.')
class BackendCacheTests(unittest.TestCase):
    def setup_backend(self, *, supported=True, fingerprint_value=None, uncertain=False):
        temporary = tempfile.TemporaryDirectory(prefix='abralia-dev-', dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        meta = {'pid': os.getpid(), 'project': str(root), 'backend_epoch': 'test-epoch'}
        write_json(root / 'shared.info.json', meta)
        state = {'status': 'accepted', 'mode': 'hardware',
                 'selected_device_fingerprint': None if uncertain else fingerprint_value or fingerprint(DEVICE)}
        payload = {'status': 'accepted', 'capabilities': {'host_interaction': {'matrix_rows': 6},
                   'keychron_rgb': {'led_count': 87}, 'observation': 'fixture cache'}} if supported else {
                   'status': 'rejected', 'reason': 'unsupported_admin_operation'}
        messages = []
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for response in ({'status': 'accepted', 'backend_epoch': 'test-epoch'}, state, payload):
                    line = self.rfile.readline(65536)
                    if not line:
                        break
                    messages.append(json.loads(line))
                    self.wfile.write(json.dumps(response).encode() + b'\n')
                    self.wfile.flush()
        server = socketserver.UnixStreamServer(str(root / 'shared.sock'), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join(2)
        self.addCleanup(stop)
        return root, messages

    def test_real_private_socket_reads_cache_not_hid(self):
        root, messages = self.setup_backend()
        opener = Mock(side_effect=AssertionError('must not open HID'))
        result = probe(DEVICE, 'abralia-keychron', runtime_dir=root, opener=opener)
        self.assertEqual(result['source'], 'backend_cache')
        self.assertEqual([m.get('action') for m in messages], [None, 'status', 'developer_probe'])
        self.assertEqual(messages[2]['fingerprint'], fingerprint(DEVICE))
        self.assertEqual(messages[2]['expected_epoch'], 'test-epoch')
        opener.assert_not_called()

    def test_old_backend_or_unknown_owner_never_falls_back_to_direct_hid(self):
        for options in ({'supported': False}, {'uncertain': True}):
            with self.subTest(options=options):
                root, _ = self.setup_backend(**options)
                opener = Mock(side_effect=AssertionError('must not open HID'))
                with self.assertRaises(DeveloperError):
                    probe(DEVICE, 'abralia-v2', runtime_dir=root, opener=opener)
                opener.assert_not_called()

    def test_service_requires_admin_exact_device_and_epoch(self):
        from abralia.backend.service import BrokerService
        with tempfile.TemporaryDirectory() as temp:
            service = BrokerService(temp, PROFILE, endpoint=Path(temp) / 'test.sock')
            caps = parse_capabilities(caps_packet())
            service.driver = SimpleNamespace(mode='hardware', broker=service.broker,
                selected_device_fingerprint=fingerprint(DEVICE), profile=SimpleNamespace(expected_led_count=87),
                protocol=SimpleNamespace(capabilities=caps, session_token=0x11223344),
                rgb=SimpleNamespace(adapter=SimpleNamespace(_capabilities=AdapterCapabilities(True, True, True, True))))
            service.broker.delivery = 'written'
            base = {'connection': 'test', 'role': 'admin', 'message': {'type': 'admin', 'action': 'developer_probe',
                    'expected_epoch': service.broker.epoch, 'fingerprint': fingerprint(DEVICE)}}
            self.assertEqual(service._handle(base)['status'], 'accepted')
            for field, value in (('expected_epoch', 'stale'), ('fingerprint', {})):
                request = deepcopy(base); request['message'][field] = value
                self.assertEqual(service._handle(request)['status'], 'rejected')
            base['role'] = 'agent'
            self.assertEqual(service._handle(base)['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
