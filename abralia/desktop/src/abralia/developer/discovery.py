# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Profile-free HID descriptor discovery and explicit interface selection."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import Counter

from abralia.rgb.transport import HidDeviceInfo, enumerate_hid_devices

MATCH_FIELDS = ('vendor_id', 'product_id', 'usage_page', 'usage')
USB_FIELDS = (*MATCH_FIELDS, 'interface_number')


class DeveloperError(ValueError):
    def __init__(self, code, message, *, exit_code=2, **details):
        super().__init__(message)
        self.code, self.exit_code, self.details = code, exit_code, details


def fingerprint(device: HidDeviceInfo):
    return {**{key: getattr(device, key) for key in USB_FIELDS},
            'serial_number': device.serial_number or '',
            'path_b64': base64.b64encode(os.fsencode(device.path)).decode('ascii')}


def selection_id(device: HidDeviceInfo):
    # Include the path even when a serial exists: this selects a particular
    # current interface, not an interchangeable unit or an enumeration index.
    value = json.dumps(fingerprint(device), sort_keys=True, separators=(',', ':')).encode()
    return 'hid-' + hashlib.sha256(value).hexdigest()[:24]


def describe(device: HidDeviceInfo):
    values = {key: getattr(device, key) for key in USB_FIELDS}
    missing = [key for key in MATCH_FIELDS if values[key] is None]
    return {'selection_id': selection_id(device),
            'product': device.product, 'manufacturer': device.manufacturer,
            'serial_number': device.serial_number, **values,
            'hex': {key: f'0x{values[key]:04X}' if values[key] is not None else None for key in MATCH_FIELDS},
            'device_match': {key: values[key] for key in MATCH_FIELDS},
            'fingerprint': fingerprint(device),
            'common_via_rawhid_usage': (device.usage_page, device.usage) == (0xFF60, 0x61),
            'missing_match_fields': missing, 'firmware_verified': False}


def discover(*, vendor_id=None, product_id=None, enumerator=None):
    devices = (enumerator or enumerate_hid_devices)()
    return [d for d in devices if (vendor_id is None or d.vendor_id == vendor_id)
            and (product_id is None or d.product_id == product_id)]


def scan(**kwargs):
    devices = discover(**kwargs)
    counts = Counter(selection_id(d) for d in devices)
    rows = [{**describe(d), 'selectable': counts[selection_id(d)] == 1} for d in devices]
    rows.sort(key=lambda row: (row['vendor_id'], row['product_id'], row['selection_id']))
    return {'format': 'abralia-hid-scan', 'version': 1, 'status': 'ok',
            'scope': 'HID interfaces visible to this process', 'device_count': len(rows), 'devices': rows,
            'note': 'USB descriptors do not verify firmware support. Empty results may reflect OS permissions.'}


def select_device(identifier, *, enumerator=None):
    if not isinstance(identifier, str) or not identifier.startswith('hid-'):
        raise DeveloperError('invalid_device_id', 'Use a selection_id returned by abralia-dev scan.')
    matches = [d for d in discover(enumerator=enumerator) if selection_id(d) == identifier]
    if len(matches) != 1:
        raise DeveloperError('device_missing' if not matches else 'device_ambiguous',
                             'The selected interface is missing or ambiguous. Scan again; no fallback was selected.',
                             exit_code=3)
    return matches[0]
