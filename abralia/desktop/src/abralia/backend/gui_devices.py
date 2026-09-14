# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Read-only supported-device discovery and exact physical-device identities."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from .project_policy import read_private_json, default_state_dir

from abralia.device_profile import load_device_profile
from abralia.rgb.transport import enumerate_hid_devices


SUPPORTED_PROFILES = (
    'builtin:keychron-v3-8k-ansi-encoder-effect25',
    'builtin:keychron-v3-ansi-encoder-effect25',
    'builtin:keychron-v3-ansi-effect25',
)
USB_FIELDS = ('vendor_id', 'product_id', 'usage_page', 'usage', 'interface_number')


class DeviceSelectionError(ValueError):
    pass


def saved_device_selection(state_dir=None):
    """Read an explicit previous GUI choice; never open or enumerate hardware."""
    directory = Path(state_dir) if state_dir is not None else default_state_dir()
    if not directory.exists():
        return None
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise DeviceSelectionError('selection_directory_not_private')
    value = read_private_json(directory / 'device-selection.json')
    if value is None:
        return None
    selected = value.get('selected_device')
    if value.get('version') != 1 or not isinstance(selected, dict):
        raise DeviceSelectionError('invalid_saved_selection')
    fingerprint = validate_fingerprint(selected.get('fingerprint'))
    if selected.get('id') != device_id(fingerprint) or selected.get('profile_id') not in SUPPORTED_PROFILES:
        raise DeviceSelectionError('invalid_saved_selection')
    return dict(selected)


def device_fingerprint(info) -> dict:
    """The path is fallback identity only when firmware supplies no serial."""
    return {**{key: getattr(info, key) for key in USB_FIELDS},
            'serial_number': info.serial_number or '',
            'path_b64': base64.b64encode(os.fsencode(info.path)).decode('ascii')}


def validate_fingerprint(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {*USB_FIELDS, 'serial_number', 'path_b64'}:
        raise DeviceSelectionError('invalid_device_fingerprint')
    for key in USB_FIELDS:
        field = value[key]
        nullable = key not in ('vendor_id', 'product_id')
        if field is None and nullable:
            continue
        lower = -1 if key == 'interface_number' else 0
        if type(field) is not int or not lower <= field <= 65535:
            raise DeviceSelectionError('invalid_device_fingerprint')
    serial, path = value['serial_number'], value['path_b64']
    if not isinstance(serial, str) or len(serial) > 512 or not isinstance(path, str) or len(path) > 8192:
        raise DeviceSelectionError('invalid_device_fingerprint')
    try:
        decoded = base64.b64decode(path, validate=True)
    except (ValueError, TypeError):
        raise DeviceSelectionError('invalid_device_fingerprint') from None
    if not decoded:
        raise DeviceSelectionError('invalid_device_fingerprint')
    return {key: value[key] for key in (*USB_FIELDS, 'serial_number', 'path_b64')}


def device_id(fingerprint: dict) -> str:
    fingerprint = validate_fingerprint(fingerprint)
    identity = {key: fingerprint[key] for key in USB_FIELDS}
    if fingerprint['serial_number']:
        identity['serial_number'] = fingerprint['serial_number']
    else:
        identity['path_b64'] = fingerprint['path_b64']
    digest = hashlib.blake2s(json.dumps(identity, sort_keys=True).encode(), digest_size=16).hexdigest()
    return 'hid-' + digest


def resolve_device(fingerprint, profile, *, devices=None):
    """Select one fresh descriptor; never substitute an enumeration index."""
    expected = validate_fingerprint(fingerprint)
    identity = device_id(expected)
    matches = [info for info in (enumerate_hid_devices() if devices is None else devices)
               if profile.device_match.matches(info) and device_id(device_fingerprint(info)) == identity]
    if not matches:
        raise DeviceSelectionError('selected_device_disconnected')
    if len(matches) != 1:
        raise DeviceSelectionError('selected_device_ambiguous')
    return matches[0]


def scan_devices(*, enumerator=None, profiles=SUPPORTED_PROFILES):
    """Descriptors only: matching a profile does not verify installed firmware."""
    infos = (enumerator or enumerate_hid_devices)()
    found = {}
    for profile_id in profiles:
        profile = load_device_profile(profile_id)
        for info in infos:
            if not profile.device_match.matches(info):
                continue
            fingerprint = device_fingerprint(info)
            identity = device_id(fingerprint)
            if identity in found:
                found[identity]['ambiguous_count'] += 1
                found[identity]['selectable'] = False
                found[identity]['selection_reason'] = 'duplicate_device_identity'
                continue
            found[identity] = {
                'id': identity, 'profile_id': profile_id,
                'name': info.product or profile.display_name,
                'profile_name': profile.display_name, 'manufacturer': info.manufacturer,
                'serial': info.serial_number or '', 'connected': True,
                'experimental': profile_id != SUPPORTED_PROFILES[0],
                'firmware_verified': False, 'selectable': True,
                'selection_reason': None, 'ambiguous_count': 1,
                'identity_source': 'serial' if info.serial_number else 'device_path',
                'fingerprint': fingerprint,
            }
    return sorted(found.values(), key=lambda item: (item['name'], item['id']))
