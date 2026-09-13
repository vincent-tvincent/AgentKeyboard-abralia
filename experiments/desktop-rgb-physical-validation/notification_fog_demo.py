#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Bounded three-task fog trial using the production broker and device worker.

Simulated by default. Hardware mode requires the normal backend to be stopped.
Fixtures never open applications or present native questions. Global keyboard
brightness is preserved; normal exit and Ctrl-C restore the captured RGB state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import DeviceDriver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='builtin:keychron-v3-8k-ansi-encoder-effect25')
    parser.add_argument('--mode', choices=('simulated', 'hardware'), default='simulated')
    parser.add_argument('--seconds', type=float, default=75)
    parser.add_argument('--hold-seconds', type=float, default=25, help='shortened orb hold for this trial only')
    parser.add_argument('--fade-seconds', type=float, default=15, help='shortened orb fade for this trial only')
    parser.add_argument('--fast', action='store_true', help='advance virtual time; simulated mode only')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600 or (args.fast and args.mode != 'simulated'):
        parser.error('seconds must be 1..600; --fast requires simulated mode')
    config = BrokerConfig(orb_hold_seconds=args.hold_seconds, orb_fade_seconds=args.fade_seconds)
    virtual = [0.]
    clock = (lambda: virtual[0]) if args.fast else time.monotonic
    broker = Broker(config, clock=clock)
    driver = DeviceDriver(broker, args.profile, args.mode)
    evidence = {'mode':args.mode, 'fixtures':3, 'settings':{
        'orb_hold_seconds':args.hold_seconds, 'orb_fade_seconds':args.fade_seconds},
        'observations':[], 'cleanup':{}, 'error':None}
    started, next_sample, last_phase = clock(), 0., None
    try:
        driver.start()
        for i in range(3):
            caller = Caller(f'fog-demo:{i + 1}')
            result = broker.call(caller, 'acquire_slot', {'label':f'SIMULATED fog task {i + 1}', 'idempotency_key':'start'})
            broker.call(caller, 'set_notification', {'slot_token':result['allocation']['slot_token'],
                                                   'enabled':True, 'idempotency_key':'notice'})
        if args.mode == 'simulated':
            broker.set_active(True)
        print('Three SIMULATED callers on F1-F3. Double-tap the mode key for Agent Mode.', flush=True)
        print('Print: pick up current caller. Scroll: mute. F1-F3: acknowledge that task. Ctrl-C: restore and exit.', flush=True)
        while clock() - started < args.seconds:
            broker.step()
            driver.tick()
            if broker.device_error:
                raise RuntimeError(broker.device_error)
            elapsed = clock() - started
            state = broker.notification_visuals()
            presentation = state['presentation']
            phase = ((presentation['slot_id'], presentation['phase']) if presentation else None,
                     tuple(o['slot_id'] for o in state['orbs'] if not o.get('presenting') and o['opacity'] > 0))
            if phase != last_phase:
                print(json.dumps({'at':round(elapsed, 2), 'presentation':phase[0], 'ambient_slots':phase[1]}), flush=True)
                last_phase = phase
            if elapsed >= next_sample:
                evidence['observations'].append({'at':round(elapsed, 3), 'active':broker.active,
                    'presentation':phase[0], 'orbs':[{'slot_id':o['slot_id'], 'opacity':o['opacity'],
                                                     'quiet':o['quiet']} for o in state['orbs']]})
                next_sample = elapsed + .5
            if args.fast:
                virtual[0] += 1 / 30
            else:
                time.sleep(.005)
    except KeyboardInterrupt:
        evidence['interrupted'] = True
    except Exception as error:
        evidence['error'] = str(error)
    finally:
        evidence['events'] = [{k:v for k,v in event.items() if k != 'caller_id'} for event in broker.events]
        for slot in list(broker.slots.values()):
            broker._release(slot)
        try:
            driver.close()
            evidence['cleanup'] = {'remaining_slots':len(broker.slots),
                                   'rgb_restored':bool(driver.rgb and driver.rgb.recovery.restored)}
        except Exception as error:
            evidence['cleanup'] = {'error':str(error)}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps({'event':'DEMO_STOPPED', 'cleanup':evidence['cleanup'], 'error':evidence['error']}), flush=True)
    return int(bool(evidence['error'] or evidence['cleanup'].get('error')))


if __name__ == '__main__':
    raise SystemExit(main())
