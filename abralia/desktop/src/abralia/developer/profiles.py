# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Versioned draft generation and offline validation of completed profiles."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re

import jsonschema

from abralia.device_profile import load_schema
from abralia.rgb.profiles import validate_profile
from .discovery import DeveloperError, describe, fingerprint

DRAFT_FORMAT = 'abralia-device-profile-draft'
MAX_JSON_BYTES = 4 * 1024 * 1024
CAPABILITIES = ('per_key_rgb', 'independent_brightness', 'guarded_frames', 'local_animation')


def read_json(source):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate JSON key: {key}')
            value[key] = item
        return value
    try:
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError('JSON file exceeds 4 MiB')
        result = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'invalid JSON number: {value}')))
        if not isinstance(result, dict):
            raise ValueError('expected a JSON object')
        return result
    except (OSError, ValueError, RecursionError) as error:
        raise DeveloperError('invalid_json', str(error)) from error


def write_json(path, value):
    """Exclusive creation: never overwrite an existing file or follow a symlink."""
    data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    if len(data) > MAX_JSON_BYTES:
        raise DeveloperError('output_too_large', 'Generated JSON exceeds 4 MiB.')
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError as error:
        raise DeveloperError('output_exists', 'Output already exists. Choose a new filename; nothing was overwritten.') from error
    except OSError as error:
        raise DeveloperError('output_unavailable', str(error)) from error
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        path.unlink(missing_ok=True)
        raise DeveloperError('output_write_failed', str(error)) from error


def missing_fields(profile):
    missing = []
    def check(path, value, reason):
        if value is None or value == []:
            missing.append({'field': path, 'reason': reason})
    for key, value in profile['device_match'].items():
        check('device_match.' + key, value, 'The operating system did not report this matching field.')
    check('adapter', profile['adapter'], 'Choose the adapter matching the actual firmware contract.')
    for key, value in profile['keymap'].items():
        check('keymap.' + key, value, 'Not reported by USB descriptors; use a supported firmware probe or source metadata.')
    check('expected_led_count', profile['expected_led_count'], 'Read LED count from supported firmware or source metadata.')
    for key, value in profile['capabilities'].items():
        check('capabilities.' + key, value, 'Confirm this capability from the firmware contract and device tests.')
    check('interaction.toggle_matrix', profile['interaction']['toggle_matrix'],
          'The reserved physical mode-key position is not advertised by the current protocol.')
    check('elements', profile['elements'], 'Add physical keys, geometry, matrix addresses and LED mappings.')
    check('regions', profile['regions'], 'Define the profile regions from its physical layout.')
    return missing


def make_draft(device, *, profile_id=None, display_name=None, report=None):
    profile_id = profile_id or f'keyboard-{device.vendor_id:04x}-{device.product_id:04x}'
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', profile_id):
        raise DeveloperError('invalid_profile_id', 'Use a lowercase profile ID containing letters, numbers, dots, underscores or hyphens.')
    name = display_name or device.product or profile_id
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise DeveloperError('invalid_display_name', 'Display name must contain 1–256 characters.')
    observations = {}
    if report is not None:
        if (not isinstance(report, dict) or report.get('format') != 'abralia-device-probe'
                or type(report.get('version')) is not int or report['version'] != 1
                or not isinstance(report.get('device'), dict)
                or report.get('status') != 'ok' or report['device'].get('fingerprint') != fingerprint(device)
                or not isinstance(report.get('observations'), dict)):
            raise DeveloperError('probe_report_mismatch', 'The probe report must describe this exact scanned interface.')
        observations = report['observations']
    host, rgb = observations.get('host_interaction', {}), observations.get('keychron_rgb', {})
    if not isinstance(host, dict) or not isinstance(rgb, dict):
        raise DeveloperError('invalid_probe_report', 'Protocol observations must be objects.')
    def number(source, key, low, high):
        value = source.get(key)
        if value is not None and (type(value) is not int or not low <= value <= high):
            raise DeveloperError('invalid_probe_report', f'Invalid reported {key}.')
        return value
    caps = {key: rgb.get(key) for key in CAPABILITIES}
    if any(value is not None and type(value) is not bool for value in caps.values()):
        raise DeveloperError('invalid_probe_report', 'Capability values must be boolean or unknown.')
    profile = {'schema_version': 1, 'profile_id': profile_id, 'display_name': name,
        'adapter': {'id': 'keychron-effect25-rawhid', 'min_version': 1} if caps['guarded_frames'] is True else None,
        'device_match': describe(device)['device_match'],
        'keymap': {'matrix_rows': number(host, 'matrix_rows', 1, 64),
                   'matrix_columns': number(host, 'matrix_columns', 1, 256),
                   'encoder_count': number(host, 'encoder_count', 0, 64)},
        'expected_led_count': number(rgb, 'led_count', 1, 255),
        'capabilities': caps, 'interaction': {'toggle_matrix': None}, 'elements': [], 'regions': []}
    return {'format': DRAFT_FORMAT, 'version': 1, 'status': 'incomplete', 'hardware_verified': False,
            'profile': profile, 'missing_fields': missing_fields(profile),
            'evidence': {'usb': describe(device), 'probe': deepcopy(report)},
            'instructions': ['Fill unknown fields in profile using firmware/layout metadata and device verification.',
                'For an RGB-only profile, omit interaction if Host Interaction is intentionally unsupported.',
                'Run profile validate on this draft; --output exports only a complete, validated profile.',
                'Validation checks configuration; it does not flash, install, or certify the keyboard.']}


def _agent_errors(profile):
    errors = []
    if profile.get('adapter', {}).get('id') != 'keychron-effect25-rawhid':
        errors.append({'field': 'adapter.id', 'message': 'The current agent backend needs the Keychron effect-25 adapter.'})
    for field, maximum in (('matrix_rows', 64), ('matrix_columns', 256), ('encoder_count', 64)):
        if profile['keymap'][field] > maximum:
            errors.append({'field': 'keymap.' + field, 'message': f'Host Interaction v2 supports at most {maximum} here.'})
    if profile['expected_led_count'] > 255:
        errors.append({'field': 'expected_led_count', 'message': 'The current Keychron adapter has 8-bit LED addressing.'})
    for field in ('per_key_rgb', 'independent_brightness', 'guarded_frames'):
        if profile['capabilities'][field] is not True:
            errors.append({'field': 'capabilities.' + field, 'message': 'The current agent renderer requires this capability.'})
    matrix = profile.get('interaction', {}).get('toggle_matrix')
    if matrix is None:
        errors.append({'field': 'interaction.toggle_matrix', 'message': 'Agent Mode needs the declared physical toggle position.'})
    elements = {e['id']: e for e in profile.get('elements', [])}
    required = ('ESC', 'ENTER', 'INSERT', 'DELETE', 'SCREENSHOT', 'SCROLL_LOCK', 'HOME', 'END',
                'PAGE_UP', 'PAGE_DOWN', 'UP', 'DOWN', 'LEFT', 'RIGHT',
                *(f'F{i}' for i in range(1, 13)), *(str(i) for i in range(10)))
    for name in required:
        e = elements.get(name)
        if e is None or e.get('matrix') is None or e.get('led_address') is None:
            errors.append({'field': 'elements.' + name, 'message': 'The current agent controls need a physical matrix/RGB element here.'})
    if matrix is not None and not any(e.get('matrix') == matrix and e.get('led_address') is not None for e in elements.values()):
        errors.append({'field': 'interaction.toggle_matrix', 'message': 'No RGB key represents the reserved mode position.'})
    return errors


def validate_document(document, *, for_agent=False):
    draft = document.get('format') == DRAFT_FORMAT
    if draft and (type(document.get('version')) is not int or document['version'] != 1
                  or not isinstance(document.get('profile'), dict)):
        raise DeveloperError('invalid_draft', 'Expected a version-1 draft with a profile object.')
    profile = document['profile'] if draft else document
    validator = jsonschema.Draft202012Validator(load_schema('profile-v1.schema.json'))
    errors = [{'field': '.'.join(map(str, error.absolute_path)) or 'profile', 'message': error.message}
              for error in sorted(validator.iter_errors(profile), key=lambda e: str(list(e.absolute_path)))][:100]
    if not errors:
        try:
            validate_profile(profile)
        except (ValueError, RuntimeError, AssertionError) as error:
            errors.append({'field': 'profile', 'message': str(error)})
    if not errors and for_agent:
        errors.extend(_agent_errors(profile))
    result = {'format': 'abralia-profile-validation', 'version': 1,
              'status': 'valid' if not errors else 'incomplete' if draft else 'invalid',
              'valid': not errors, 'input_is_draft': draft, 'profile_id': profile.get('profile_id'),
              'validation_scope': 'agent_configuration' if for_agent else 'rgb_profile',
              'hardware_verified': False, 'errors': errors}
    return result, deepcopy(profile) if not errors else None
