# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Fixed, read-only protocol queries with an existing-owner guard."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import stat

from abralia.interaction.protocol import (Capabilities, Opcode, Result, get_capabilities_packet,
    parse_capabilities, response_envelope_matches)
from abralia.rgb.adapters.keychron_effect25 import FrameResult, FrameState
from abralia.rgb.compatibility import AdapterCapabilities
from abralia.rgb.transport import HidApiTransport
from .discovery import DeveloperError, describe, fingerprint, select_device, selection_id

PROTOCOLS = ('abralia-v2', 'keychron-rgb', 'abralia-keychron')


def capabilities_report(caps):
    if not isinstance(caps, Capabilities) or caps.response.result is not Result.OK:
        raise DeveloperError('invalid_capabilities', 'The firmware did not return usable Host Interaction capabilities.', exit_code=4)
    if (not 1 <= caps.matrix_rows <= 64 or not 1 <= caps.matrix_columns <= 256
            or not 0 <= caps.encoder_count <= 64
            or caps.total_control_slots != caps.matrix_rows * caps.matrix_columns + 2 * caps.encoder_count):
        raise DeveloperError('invalid_capabilities', 'The reported control dimensions are inconsistent.', exit_code=4)
    # Never serialize Response: it contains another client's ownership token.
    return {'protocol_version': 2, 'matrix_rows': caps.matrix_rows, 'matrix_columns': caps.matrix_columns,
            'encoder_count': caps.encoder_count, 'total_control_slots': caps.total_control_slots,
            'event_queue_capacity': caps.event_queue_capacity, 'double_tap_window_ms': caps.double_tap_window_ms,
            'heartbeat_timeout_ms': caps.heartbeat_timeout_ms, 'maximum_force_lease_ms': caps.maximum_force_lease_ms,
            'supported_binding_flags': int(caps.supported_binding_flags),
            'supported_lifetimes': sorted(x.name.lower() for x in caps.supported_lifetimes),
            'supports_toggle_single_tap': caps.supports_toggle_single_tap,
            'session_claimed': bool(caps.response.session_token)}


def cached_driver_report(driver):
    """Worker-owned cache only; this function performs zero HID operations."""
    if driver.mode != 'hardware' or driver.broker.delivery not in ('ready', 'written'):
        return None
    caps = getattr(driver.protocol, 'capabilities', None)
    if not isinstance(caps, Capabilities):
        return None
    host = capabilities_report(caps)
    host['session_claimed'] = bool(getattr(driver.protocol, 'session_token', 0))
    rgb = getattr(getattr(driver.rgb, 'adapter', None), '_capabilities', None)
    if not isinstance(rgb, AdapterCapabilities):
        return None
    return {'host_interaction': host,
            'keychron_rgb': {'led_count': driver.profile.expected_led_count, **asdict(rgb)},
            'observation': 'Capabilities verified when this backend opened the device; not a fresh firmware query.'}


def _read_private_info(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise DeveloperError('unsafe_backend_metadata', 'Backend discovery metadata is not a private regular file.', exit_code=3)
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 1
                or not isinstance(value.get('project'), str) or not Path(value['project']).is_absolute()
                or not isinstance(value.get('backend_epoch'), str)):
            raise DeveloperError('invalid_backend_metadata', 'Backend discovery metadata is invalid.', exit_code=3)
        return value
    except (OSError, ValueError) as error:
        if isinstance(error, DeveloperError):
            raise
        raise DeveloperError('backend_state_uncertain', 'Cannot safely inspect the existing backend; no HID probe was sent.', exit_code=3) from error


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        return True  # Permission uncertainty is not proof an owner disappeared.


def _exchange(connection, reader, message):
    connection.sendall(json.dumps(message).encode() + b'\n')
    line = reader.readline(4 * 1024 * 1024 + 1)
    if not line.endswith(b'\n') or len(line) > 4 * 1024 * 1024:
        raise ValueError('invalid_backend_response')
    return json.loads(line)


def backend_report(device, *, runtime_dir=None):
    """Use cached metadata from the exact HID owner; never open a second handle."""
    if not hasattr(os, 'getuid'):
        return None
    root = Path(runtime_dir or os.environ.get('ABRALIA_RUNTIME_DIR') or f'/tmp/abralia-{os.getuid()}')
    if not root.exists() and not root.is_symlink():
        return None
    meta = root.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
        raise DeveloperError('unsafe_backend_directory', 'The backend discovery directory must be private.', exit_code=3)
    wanted = fingerprint(device)
    for info_path in sorted(root.glob('*.info.json')):
        info = _read_private_info(info_path)
        if not _alive(info['pid']):
            continue
        endpoint = info_path.with_name(info_path.name.removesuffix('.info.json') + '.sock')
        try:
            metadata = endpoint.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise ValueError('invalid_backend_socket')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(str(endpoint))
                with connection.makefile('rb') as reader:
                    hello = _exchange(connection, reader, {'type': 'hello', 'role': 'admin', 'project': info['project']})
                    if hello.get('status') != 'accepted' or hello.get('backend_epoch') != info['backend_epoch']:
                        raise ValueError('backend_changed')
                    state = _exchange(connection, reader, {'type': 'admin', 'action': 'status'})
                    if state.get('status') != 'accepted':
                        raise ValueError('backend_unavailable')
                    actual = state.get('selected_device_fingerprint')
                    if state.get('mode') == 'simulated':
                        continue
                    if actual != wanted:
                        if actual is None:
                            raise ValueError('backend_device_unknown')
                        continue
                    result = _exchange(connection, reader, {'type': 'admin', 'action': 'developer_probe',
                        'expected_epoch': info['backend_epoch'], 'fingerprint': wanted})
                    if result.get('status') != 'accepted':
                        raise DeveloperError('backend_probe_unavailable',
                            'Abralia owns this interface but cannot provide its cached probe. Update or quit that backend before probing.', exit_code=3)
                    return result['capabilities']
        except DeveloperError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise DeveloperError('backend_state_uncertain',
                'An existing Abralia backend could not be checked safely. No direct HID probe was sent.', exit_code=3) from error
    return None


def _transact(transport, request, matcher):
    return transport.transact(request, matcher, timeout_ms=700)


def _host_probe(transport):
    caps = parse_capabilities(_transact(transport, get_capabilities_packet(),
                                      lambda r: response_envelope_matches(r, Opcode.GET_CAPABILITIES)))
    result = capabilities_report(caps)
    if result['session_claimed']:
        raise DeveloperError('device_busy', 'The firmware reports an active host session. No further queries were sent.', exit_code=3)
    return result


def _rgb_probe(transport):
    def rgb(command):
        reply = _transact(transport, [0xA8, command], lambda r: len(r) == 32 and r[:2] == bytes([0xA8, command]))
        if reply[2] != 0:
            raise DeveloperError('rgb_query_rejected', 'The firmware rejected a Keychron RGB information query.', exit_code=4)
        return reply
    protocol = _transact(transport, [0xA0], lambda r: len(r) == 32 and r[0] == 0xA0)
    if protocol[1] == 0:
        raise DeveloperError('unsupported_keychron_protocol', 'A supported Keychron protocol response was not received.', exit_code=4)
    led_count = rgb(0x05)[3]
    if not led_count:
        raise DeveloperError('invalid_led_count', 'The device reported no addressable LEDs.', exit_code=4)
    version_reply = rgb(0x01)
    rgb_version = version_reply[3] | version_reply[4] << 8
    result = {'keychron_protocol_version': protocol[1], 'rgb_protocol_version': rgb_version,
              'per_key_rgb': True if rgb_version else None, 'led_count': led_count}
    # This is an Abralia extension, so lack of its response is unknown, not false.
    try:
        # The GET handler overwrites these payload bytes. An unknown VIA
        # command may echo them; reject that echo instead of claiming support.
        frame = _transact(transport, [0x08, 0x00, 0x01, 0xA5, 0x5A, 0xA5, 0x5A, 0xA5],
                          lambda r: len(r) == 32 and r[:3] == bytes([0x08, 0x00, 0x01]))
        state, status = FrameState(frame[3]), FrameResult(frame[7])
        if status is FrameResult.OK:
            result.update(guarded_frames=True, frame_state=state.name.lower())
    except (ValueError, OSError, RuntimeError):
        result['guarded_frames'] = None
    return result


def probe(device, protocol, *, source='auto', runtime_dir=None, owner_check=None, opener=None):
    if protocol not in PROTOCOLS or source not in ('auto', 'backend', 'direct'):
        raise DeveloperError('invalid_probe_options', 'Select a supported protocol and source.')
    if (device.usage_page, device.usage) != (0xFF60, 0x61):
        raise DeveloperError('unsupported_interface', 'These protocols require an explicitly identified VIA Raw HID interface (FF60:0061).', exit_code=3)
    existing = (owner_check or backend_report)(device, runtime_dir=runtime_dir)
    if existing is not None:
        if source == 'direct':
            raise DeveloperError('device_busy', 'Abralia already owns this interface. Use --source backend or quit Abralia.', exit_code=3)
        observation = existing.get('observation', 'Cached backend capabilities.')
        sections = {k: v for k, v in existing.items() if k in ('host_interaction', 'keychron_rgb')}
        source = 'backend_cache'
    else:
        if source == 'backend':
            raise DeveloperError('backend_not_found', 'No running Abralia backend owns the selected interface.', exit_code=3)
        sections = {}
        if opener is None:
            # Owner discovery can take time. Recheck the exact path/descriptor
            # identity before opening, rather than retaining a stale list index.
            select_device(selection_id(device))
        transport = (opener or HidApiTransport.open_path)(device.path)
        try:
            if protocol in ('abralia-v2', 'abralia-keychron'):
                sections['host_interaction'] = _host_probe(transport)
            if protocol in ('keychron-rgb', 'abralia-keychron'):
                sections['keychron_rgb'] = _rgb_probe(transport)
        finally:
            transport.close()
        source, observation = 'device_query', 'Fixed information queries only; no session claim, bindings, lighting, keymap or EEPROM writes.'
    wanted = ('host_interaction',) if protocol == 'abralia-v2' else ('keychron_rgb',) if protocol == 'keychron-rgb' else ('host_interaction', 'keychron_rgb')
    if any(key not in sections or not isinstance(sections[key], dict) for key in wanted):
        raise DeveloperError('requested_capabilities_unavailable', 'The selected source did not supply the requested protocol capabilities.', exit_code=4)
    return {'format': 'abralia-device-probe', 'version': 1, 'status': 'ok', 'protocol': protocol,
            'source': source, 'device': describe(device), 'observations': {k: sections[k] for k in wanted if k in sections},
            'note': observation, 'hardware_profile_complete': False}
