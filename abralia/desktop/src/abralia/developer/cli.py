# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Discover HID interfaces, query explicit protocols, and author keyboard profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from abralia.device_profile import DeviceProfileError, load_profile_data
from .discovery import DeveloperError, scan, select_device
from .probe import PROTOCOLS, probe
from .profiles import make_draft, read_json, validate_document, write_json


def usb_number(value):
    try:
        number = int(value, 16 if value.lower().startswith('0x') else 10)
        if not 0 <= number <= 65535:
            raise ValueError()
        return number
    except ValueError as error:
        raise argparse.ArgumentTypeError('Use a decimal value or a 0x-prefixed hexadecimal USB ID.') from error


def parser():
    root = argparse.ArgumentParser(prog='abralia-dev', description=__doc__)
    root.add_argument('--json', action='store_true', help='machine-readable output')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='machine-readable output')
    commands = root.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('scan', parents=[common], help='list HID descriptors without a profile or opening a device')
    listing.add_argument('--vendor-id', type=usb_number)
    listing.add_argument('--product-id', type=usb_number)
    query = commands.add_parser('probe', parents=[common], help='read capabilities from a selected interface using an explicit protocol')
    query.add_argument('--device', required=True, help='selection_id from scan; never a list index')
    query.add_argument('--protocol', required=True, choices=PROTOCOLS)
    query.add_argument('--source', choices=('auto', 'backend', 'direct'), default='auto')
    query.add_argument('--runtime-dir', type=Path, help='private Abralia backend-discovery directory')
    query.add_argument('--output', type=Path, help='save a new probe report; refuses overwrites')
    profile = commands.add_parser('profile', parents=[common], help='create or validate a profile')
    profiles = profile.add_subparsers(dest='profile_command', required=True)
    draft = profiles.add_parser('draft', parents=[common], help='write an explicitly incomplete draft from a scanned interface')
    draft.add_argument('--device', required=True)
    draft.add_argument('--probe-report', type=Path)
    draft.add_argument('--id', dest='profile_id')
    draft.add_argument('--name', dest='display_name')
    draft.add_argument('--output', type=Path, required=True)
    validate = profiles.add_parser('validate', parents=[common], help='validate JSON data and layout invariants without hardware access')
    validate.add_argument('source', help='profile/draft JSON path or builtin: profile ID')
    validate.add_argument('--for-agent', action='store_true', help='also check the current agent backend control requirements')
    validate.add_argument('--output', type=Path, help='export validated profile data into a new file')
    return root


def execute(args):
    if args.command == 'scan':
        return scan(vendor_id=args.vendor_id, product_id=args.product_id)
    if args.command == 'probe':
        device = select_device(args.device)
        report = probe(device, args.protocol, source=args.source, runtime_dir=args.runtime_dir)
        if args.output:
            write_json(args.output, report)
        return report
    if args.profile_command == 'draft':
        device = select_device(args.device)
        report = read_json(args.probe_report) if args.probe_report else None
        draft = make_draft(device, profile_id=args.profile_id, display_name=args.display_name, report=report)
        write_json(args.output, draft)
        return {'status': 'draft_created', 'output': str(args.output), 'hardware_verified': False,
                'profile_id': draft['profile']['profile_id'], 'missing_fields': draft['missing_fields']}
    try:
        document = load_profile_data(args.source) if args.source.startswith('builtin:') else read_json(args.source)
    except DeviceProfileError as error:
        raise DeveloperError('invalid_profile_source', str(error)) from error
    result, profile = validate_document(document, for_agent=args.for_agent)
    if args.output and profile is not None:
        write_json(args.output, profile)
        result['output'] = str(args.output)
    return result


def human(result):
    if result.get('format') == 'abralia-hid-scan':
        if not result['devices']:
            print('No HID interfaces are visible. Check connection/permissions; DFU devices are not HID interfaces.')
        for d in result['devices']:
            # Descriptor strings are untrusted data; JSON quoting prevents ANSI
            # control sequences from escaping into a developer's terminal.
            name = json.dumps(d['product'] or '(unnamed device)', ensure_ascii=False)
            interface = d['interface_number'] if d['interface_number'] is not None else '?'
            print(f"{d['selection_id']}  {d['hex']['vendor_id']}:{d['hex']['product_id']}  "
                  f"usage {d['hex']['usage_page']}:{d['hex']['usage']}  interface {interface}  {name}")
        print(f"{result['device_count']} interface(s). No firmware queries or changes were made.")
        return
    if result.get('status') == 'error':
        print(f"{result['code']}: {json.dumps(result['message'], ensure_ascii=False)}", file=sys.stderr)
        return
    if result.get('status') == 'draft_created':
        print(f"Draft saved to {json.dumps(result['output'])}. It is not a usable hardware profile yet.")
        for item in result['missing_fields']:
            print(f"  {item['field']}: {item['reason']}")
        return
    if 'valid' in result:
        print('Profile configuration is valid; hardware remains unverified.' if result['valid'] else 'Profile configuration is incomplete or invalid:')
        for error in result['errors']:
            print(f"  {json.dumps(error['field'], ensure_ascii=False)}: {json.dumps(error['message'], ensure_ascii=False)}")
        if result.get('output'):
            print('Exported ' + json.dumps(result['output']))
        return
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        code = 2 if result.get('valid') is False else 0
    except DeveloperError as error:
        result = {'status': 'error', 'code': error.code, 'message': str(error), **error.details}
        code = error.exit_code
    except (OSError, ValueError, RuntimeError, RecursionError) as error:
        result = {'status': 'error', 'code': 'operation_failed', 'message': str(error)}
        code = 4
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        human(result)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
