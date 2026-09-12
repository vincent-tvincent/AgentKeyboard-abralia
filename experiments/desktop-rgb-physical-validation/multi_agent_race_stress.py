#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Concurrent synthetic agents through real STDIO MCP, on an isolated simulated backend."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5

from mcp import Client, StdioServerParameters

from abralia.backend.core import BrokerConfig
from abralia.backend.device import dispatch_event, routes_for
from abralia.backend.ipc import BrokerClient
from abralia.backend.service import BrokerService
from abralia.interaction import DeviceEvent, Edge, EventFlags, EventType


PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def invariant_errors(broker):
    errors = []
    slots = broker.slots
    if len(slots) != len(broker.owners):
        errors.append('owner_count')
    if len({s.slot_token for s in slots.values()}) != len(slots):
        errors.append('duplicate_live_token')
    if any(broker.owners.get(s.caller.caller_id) != number or s.slot_id != number
           for number, s in slots.items()):
        errors.append('owner_slot_mapping')
    if any(number not in slots for number in broker.owners.values()):
        errors.append('dangling_owner')
    active = [s.slot_id for s in slots.values() if s.notification and s.notification.status == 'active']
    if len(active) > 1:
        errors.append('multiple_ringing_calls')
    if active != ([] if broker.current_call is None else [broker.current_call]):
        errors.append('current_call_mismatch')
    if broker.selected is not None and broker.selected not in slots:
        errors.append('dangling_selection')
    if broker.background_slot is not None and broker.background_slot not in slots:
        errors.append('dangling_background')
    if not 0 <= broker.page < broker.page_count:
        errors.append('page_out_of_range')
    if active and (broker.pending_target() is None or broker.pending_target().slot_id != active[0]):
        errors.append('ringing_call_has_no_controls')
    return errors


class AuditedService(BrokerService):
    """Test-only instrumentation. Extra admin actions exist only in this process."""

    def __init__(self, root, config):
        super().__init__(root, PROFILE, mode='simulated', config=config, endpoint=Path(root)/'broker.sock')
        self.invariant_checks = 0
        self.violations = Counter()
        self.max_sampled_queue_depth = 0
        self.live_routes = {}
        self.binding_generation = 1
        self.saved_edge = None
        self.saved_attention_edges = {}

    def _handle(self, command):
        message = command['message']
        action = message.get('action') if message.get('type') == 'admin' and command.get('role') == 'admin' else None
        if action == 'stress_snapshot':
            result = {'status': 'accepted', **self.broker.admin_snapshot(),
                      'invariant_checks': self.invariant_checks, 'violations': dict(self.violations),
                      'max_sampled_queue_depth': self.max_sampled_queue_depth,
                      'call_order': [s.slot_id for s in sorted(self.broker.slots.values(),
                          key=lambda s: (s.notification.created_at if s.notification else math.inf, s.slot_id))
                          if s.notification and s.notification.status in ('active', 'queued')],
                      'notification_starts': {s.slot_id: s.notification.started_at for s in self.broker.slots.values() if s.notification}}
        elif action == 'stress_capture_f1':
            route = self.live_routes.get(1)
            require(route is not None, 'cannot capture empty F1')
            self.saved_edge = DeviceEvent(EventType.CONTROL_EDGE, 1, 1, self.binding_generation,
                                          1, route.control, int(Edge.UP), EventFlags(0), 0)
            result = {'status': 'accepted'}
        elif action == 'stress_delayed_f1_release':
            require(self.saved_edge is not None, 'missing captured edge')
            before = self.broker.selected
            dispatch_event(self.broker, self.saved_edge, self.live_routes, self.binding_generation)
            result = {'status': 'accepted', 'selection_unchanged': before == self.broker.selected}
        elif action == 'stress_capture_attention':
            for binding in (30, 31):
                route = self.live_routes.get(binding)
                require(route is not None, 'attention controls unavailable for capture')
                self.saved_attention_edges[binding] = DeviceEvent(
                    EventType.CONTROL_EDGE, 1, binding, self.binding_generation,
                    binding, route.control, int(Edge.UP), EventFlags(0), 0)
            result = {'status': 'accepted'}
        elif action == 'stress_replay_attention':
            edge = self.saved_attention_edges[message['binding']]
            dispatch_event(self.broker, edge, self.live_routes, self.binding_generation)
            result = {'status': 'accepted'}
        else:
            result = super()._handle(command)
        self.invariant_checks += 1
        self.violations.update(invariant_errors(self.broker))
        self.max_sampled_queue_depth = max(self.max_sampled_queue_depth, self.commands.qsize())
        desired = routes_for(self.broker, self.driver.profile)
        if desired != self.live_routes:
            self.live_routes = desired
            self.binding_generation = self.binding_generation % 65535 + 1
        return result


class StressRun:
    def __init__(self, args, root, service):
        self.args, self.root, self.service = args, root, service
        self.clients = []
        self.ids = [str(uuid5(NAMESPACE_URL, f'abralia-race-fixture:{args.seed}:{i}')) for i in range(args.agents)]
        self.tokens = {}
        self.slot_ids = {}
        self.counts = Counter()
        self.latencies = []
        self.phases = []
        self.inflight = self.peak_inflight = 0
        self.rng = random.Random(args.seed)
        self.admin_client = BrokerClient(root, endpoint=service.endpoint, role='admin')

    async def call(self, actor, operation, *, lane=0, **arguments):
        await asyncio.sleep(self.rng.random() * .004)
        client = self.clients[(actor + lane) % len(self.clients)]
        self.inflight += 1
        self.peak_inflight = max(self.peak_inflight, self.inflight)
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(client.call_tool(operation, arguments,
                meta={'x-codex-turn-metadata': {'thread_id': self.ids[actor]}}), timeout=20)
            result = response.structured_content
            require(not response.is_error and isinstance(result, dict), 'MCP transport/schema error')
            self.counts[f'{operation}:{result.get("status")}:{result.get("reason", "")}'] += 1
            return result
        finally:
            self.latencies.append((time.perf_counter() - started) * 1000)
            self.inflight -= 1

    async def admin(self, action='stress_snapshot', **fields):
        result = await asyncio.to_thread(self.admin_client.request, {'type': 'admin', 'action': action, **fields})
        require(result.get('status') == 'accepted', f'admin action failed: {action}')
        return result

    async def phase(self, name, operation):
        started = time.perf_counter()
        count = len(self.latencies)
        try:
            await operation()
            snapshot = await self.admin()
            require(not snapshot['violations'], f'broker invariant failure during {name}')
        except Exception:
            self.phases.append({'phase': name, 'status': 'failed',
                                'mcp_calls': len(self.latencies)-count,
                                'seconds': round(time.perf_counter()-started, 3)})
            raise
        entry = {'phase': name, 'status': 'passed', 'mcp_calls': len(self.latencies)-count,
                 'seconds': round(time.perf_counter()-started, 3), 'allocations': len(snapshot['slots'])}
        self.phases.append(entry)
        print(json.dumps(entry), flush=True)

    async def acquire(self):
        async def actor(i):
            args = dict(label=f'SIMULATED agent {i}', harness='codex_desktop', idempotency_key='claim')
            pair = await asyncio.gather(self.call(i, 'acquire_slot', lane=0, **args),
                                        self.call(i, 'acquire_slot', lane=1, **args))
            require(all(r['status'] == 'accepted' for r in pair), 'claim rejected')
            require(pair[0]['allocation']['slot_token'] == pair[1]['allocation']['slot_token'], 'duplicate claim allocated twice')
            require(sum(bool(r.get('replayed')) for r in pair) == 1, 'duplicate claim did not replay exactly once')
            require(pair[0]['caller']['thread_id'] == self.ids[i], 'identity crossed between callers')
            self.tokens[i] = pair[0]['allocation']['slot_token']
            self.slot_ids[i] = pair[0]['allocation']['slot_id']
        await asyncio.gather(*(actor(i) for i in range(self.args.agents)))
        require(len(set(self.tokens.values())) == self.args.agents, 'live tokens not unique')
        require(len(set(self.slot_ids.values())) == self.args.agents, 'live slots not unique')
        require((await self.admin())['page_count'] == math.ceil(self.args.agents/12), 'wrong page count')

    async def conflicting_retries_and_ownership(self):
        async def actor(i):
            token = self.tokens[i]
            pair = await asyncio.gather(
                self.call(i, 'set_slot_state', slot_token=token, state='idle', idempotency_key='conflict'),
                self.call(i, 'set_slot_state', lane=1, slot_token=token, state='error', idempotency_key='conflict'))
            accepted = [r for r in pair if r['status'] == 'accepted']
            rejected = [r for r in pair if r.get('reason') == 'idempotency_conflict']
            require(len(accepted) == len(rejected) == 1, 'conflicting retry applied more than one value')
            status = await self.call(i, 'get_status')
            require(status['allocation']['state'] == accepted[0]['allocation']['state'], 'conflict winner differs from final state')
            wrong = await self.call(i, 'set_notification', slot_token=self.tokens[(i+1) % self.args.agents],
                                    enabled=True, idempotency_key='cross-owner')
            require(wrong.get('reason') == 'invalid_or_stale_slot_token', 'cross-owner notification admitted')
        await asyncio.gather(*(actor(i) for i in range(self.args.agents)))

    async def simultaneous_calls_and_mute(self):
        async def notify(i, key):
            result = await self.call(i, 'set_notification', slot_token=self.tokens[i], enabled=True, idempotency_key=key)
            require(result['status'] == 'accepted', 'notification rejected')
        await asyncio.gather(*(notify(i, 'first-call') for i in range(self.args.agents)))
        snapshot = await self.admin()
        expected = snapshot['call_order']
        require(len(expected) == self.args.agents, 'notification lost from queue')
        await self.admin('input', event='toggle')
        muted = set()
        for index, slot_id in enumerate(expected):
            before = await self.admin()
            require(before['current_call'] == slot_id, 'notification FIFO changed')
            await asyncio.gather(self.admin('input', event='mute'),
                                 *(notify(i, f'retry-call-{index}') for i in range(self.args.agents)))
            muted.add(slot_id)
            after = await self.admin()
            for slot in after['slots']:
                if slot['slot_id'] in muted:
                    require(slot['notification']['status'] == 'muted', 'retry unmuted a notification')
            for number, start in before['notification_starts'].items():
                if start is not None:
                    require(after['notification_starts'].get(number) == start, 'retry restarted notification onset')
        require((await self.admin())['current_call'] is None, 'muted call still ringing')
        # Quiet pickup remains available after muting, without an F-key selection.
        await self.admin('input', event='pickup')

    async def update_and_input_race(self):
        async def actor(i):
            for round_id in range(self.args.rounds):
                args = dict(slot_token=self.tokens[i], state='progressing', progress=(round_id+1)/self.args.rounds,
                            idempotency_key=f'progress-{round_id}')
                pair = await asyncio.gather(self.call(i, 'set_slot_state', **args),
                                            self.call(i, 'set_slot_state', lane=1, **args))
                require(all(r['status'] == 'accepted' for r in pair), 'progress update rejected')
            status = await self.call(i, 'get_status')
            require(status['allocation']['progress'] == 1, 'another agent overwrote progress')
            require(status['allocation']['slot_id'] == self.slot_ids[i], 'logical slot moved')
        async def controls():
            for i in range(self.args.rounds * 2):
                await self.admin('input', event='next_page' if i % 2 == 0 else 'previous_page')
                await self.admin('input', event='select', position=i % 12 + 1)
                await self.admin('input', event='mute' if i % 3 else 'pickup')
        await asyncio.gather(controls(), *(actor(i) for i in range(self.args.agents)))

    async def pickup_during_new_calls(self):
        async def notify(i, enabled, key):
            result = await self.call(i, 'set_notification', slot_token=self.tokens[i],
                                     enabled=enabled, idempotency_key=key)
            require(result['status'] == 'accepted', 'pickup-race notification rejected')

        await asyncio.gather(*(notify(i, False, 'pickup-reset') for i in range(self.args.agents)))
        question = await self.call(0, 'report_question', slot_token=self.tokens[0],
            question_id='pickup-race-question', kind='free_text', options=[],
            allow_other=True, idempotency_key='pickup-question')
        require(question['status'] == 'accepted', 'pickup-race question rejected')
        require((await self.admin())['current_call'] == self.slot_ids[0], 'first call did not appear directly')
        await self.admin('stress_capture_attention')
        await asyncio.gather(self.admin('stress_replay_attention', binding=31),
            *(notify(i, True, 'call-during-pickup') for i in range(1, self.args.agents)))
        snapshot = await self.admin()
        picked = next(s for s in snapshot['slots'] if s['slot_id'] == self.slot_ids[0])
        require(picked['question']['picked_up'], 'pickup lost during incoming calls')
        require(snapshot['selected_slot'] == self.slot_ids[0] and snapshot['current_call'] is None,
                'incoming call replaced picked-up question')
        require(snapshot['page'] == (self.slot_ids[0]-1)//12+1, 'pickup did not switch to target page')
        require(len(snapshot['call_order']) == self.args.agents-1, 'calls lost while question was held')
        expected_next = snapshot['call_order'][0]

        # Finishing the picked-up question unblocks the oldest queued call.
        cleared, *_ = await asyncio.gather(
            self.call(0, 'clear_question', slot_token=self.tokens[0], question_id='pickup-race-question',
                      outcome='withdrawn', idempotency_key='pickup-question-clear'),
            *(notify(i, True, 'retry-during-clear') for i in range(1, self.args.agents)))
        require(cleared['status'] == 'accepted', 'pickup question cleanup failed')
        before = await self.admin()
        require(before['current_call'] == expected_next, 'question cleanup did not promote oldest queued call')
        await self.admin('stress_replay_attention', binding=30)
        await self.admin('stress_replay_attention', binding=31)
        after = await self.admin()
        require(after['current_call'] == before['current_call'] and
                after['selected_slot'] == before['selected_slot'] and
                after['call_order'] == before['call_order'],
                'old attention key release acted on the next caller')
        await asyncio.gather(*(notify(i, False, 'pickup-cleanup') for i in range(self.args.agents)))

    async def question_replacement_race(self):
        async def report(i, question_id):
            return await self.call(i, 'report_question', slot_token=self.tokens[i], question_id=question_id,
                                   kind='single_choice', options=[{'id':'a','label':'A'},{'id':'b','label':'B'}],
                                   allow_other=True, idempotency_key=question_id)
        await asyncio.gather(*(report(i, 'old-question') for i in range(self.args.agents)))
        async def actor(i):
            newer, older = await asyncio.gather(report(i, 'new-question'),
                self.call(i, 'clear_question', lane=1, slot_token=self.tokens[i], question_id='old-question',
                          outcome='withdrawn', idempotency_key='old-clear'))
            require(newer['status'] == 'accepted', 'replacement question rejected')
            require(older['status'] == 'accepted' or older.get('reason') == 'stale_question', 'unexpected old-question result')
            status = await self.call(i, 'get_status')
            require(status['allocation']['question']['id'] == 'new-question', 'old cleanup erased replacement question')
            await self.call(i, 'clear_question', slot_token=self.tokens[i], question_id='new-question',
                            outcome='withdrawn', idempotency_key='new-clear')
        await asyncio.gather(*(actor(i) for i in range(self.args.agents)))

    async def release_reuse_and_stale_input(self):
        # Capture an UP event before a binding-generation change, using actual routing code.
        snapshot = await self.admin()
        for _ in range(snapshot['page']-1):
            await self.admin('input', event='previous_page')
        await self.admin('stress_capture_f1')
        old_tokens = dict(self.tokens)
        async def release(i):
            released, updated = await asyncio.gather(
                self.call(i, 'release_slot', slot_token=old_tokens[i], idempotency_key='release-race'),
                self.call(i, 'set_slot_state', lane=1, slot_token=old_tokens[i], state='completed', idempotency_key='late-update'))
            require(released['status'] == 'accepted', 'release rejected')
            require(updated['status'] == 'accepted' or updated.get('reason') == 'invalid_or_stale_slot_token', 'bad release/update outcome')
        await asyncio.gather(*(release(i) for i in range(self.args.agents)))
        require(not (await self.admin())['slots'], 'release race left allocations')
        async def reacquire(i):
            result = await self.call(i, 'acquire_slot', label=f'SIMULATED replacement {i}',
                                     harness='codex_desktop', idempotency_key='reacquire')
            require(result['status'] == 'accepted', 'reacquisition failed')
            self.tokens[i] = result['allocation']['slot_token']
            require(self.tokens[i] != old_tokens[i], 'allocation generation reused token')
            stale = await self.call(i, 'set_notification', slot_token=old_tokens[i], enabled=True, idempotency_key='stale-notification')
            require(stale.get('reason') == 'invalid_or_stale_slot_token', 'stale token changed replacement')
            await self.call(i, 'release_slot', slot_token=old_tokens[i], idempotency_key='old-release-retry')
            status = await self.call(i, 'get_status')
            require(status['allocation']['slot_token'] == self.tokens[i], 'old release erased replacement')
        await asyncio.gather(*(reacquire(i) for i in range(self.args.agents)))
        require((await self.admin('stress_delayed_f1_release'))['selection_unchanged'], 'delayed F1 release selected new occupant')
        async def cleanup(i):
            args = dict(slot_token=self.tokens[i], idempotency_key='final-release')
            pair = await asyncio.gather(self.call(i, 'release_slot', **args), self.call(i, 'release_slot', lane=1, **args))
            require(all(r['status'] == 'accepted' for r in pair), 'duplicate release failed')
        await asyncio.gather(*(cleanup(i) for i in range(self.args.agents)))
        require(not (await self.admin())['slots'], 'final allocations leaked')

    async def disconnect_lifecycle(self):
        # Separate IPC lifecycle case: actual socket close, not a simulated model heartbeat.
        identity = str(uuid5(NAMESPACE_URL, f'abralia-race-disconnect:{self.args.seed}'))
        meta = {'thread_id': identity}
        one = BrokerClient(self.root, endpoint=self.service.endpoint)
        two = BrokerClient(self.root, endpoint=self.service.endpoint)
        try:
            def request(client, operation, arguments):
                return client.request({'type':'call', 'metadata':meta, 'operation':operation, 'arguments':arguments})
            created = await asyncio.to_thread(request, one, 'acquire_slot',
                {'label':'SIMULATED disconnect owner','harness':'codex_desktop','idempotency_key':'acquire'})
            require(created['status'] == 'accepted', 'disconnect fixture acquire failed')
            await asyncio.to_thread(request, two, 'get_status', {})
            one.close()
            await asyncio.sleep(1.3)
            kept = await asyncio.to_thread(request, two, 'get_status', {})
            require('allocation' in kept, 'closing one connection prematurely cleaned live owner')
            two.close()
            deadline = time.monotonic() + 4
            while (await self.admin())['slots'] and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            require(not (await self.admin())['slots'], 'confirmed disconnection did not clean allocation')
        finally:
            one.close()
            two.close()

    async def execute(self):
        started = time.perf_counter()
        try:
            async with AsyncExitStack() as stack:
                for _ in range(self.args.bridges):
                    parameters = StdioServerParameters(command=sys.executable,
                        args=['-B','-m','abralia.backend.mcp','--project',str(self.root),'--socket',str(self.service.endpoint)],
                        env={'PATH':os.environ.get('PATH',''), 'PYTHONDONTWRITEBYTECODE':'1'})
                    self.clients.append(await stack.enter_async_context(Client(parameters)))
                for name, operation in (
                    ('duplicate_acquisition', self.acquire),
                    ('conflicting_retries_and_ownership', self.conflicting_retries_and_ownership),
                    ('simultaneous_calls_fifo_mute_retry', self.simultaneous_calls_and_mute),
                    ('progress_with_paging_selection_pickup', self.update_and_input_race),
                    ('pickup_during_new_calls_and_delayed_attention_keys', self.pickup_during_new_calls),
                    ('question_replacement_vs_old_clear', self.question_replacement_race),
                    ('release_reuse_stale_tokens_and_keyup', self.release_reuse_and_stale_input),
                    ('disconnect_with_other_connection_alive', self.disconnect_lifecycle)):
                    await self.phase(name, operation)
                snapshot = await self.admin()
                require(not snapshot['slots'] and not snapshot['violations'], 'dirty final state')
                elapsed = time.perf_counter()-started
                ordered = sorted(self.latencies)
                return {'status':'passed', 'phases':self.phases, 'mcp_calls':len(ordered),
                        'elapsed_seconds':round(elapsed,3), 'calls_per_second_including_startup':round(len(ordered)/elapsed,1),
                        'latency_ms':{'median':round(statistics.median(ordered),2),
                                      'p95':round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*.95)-1)],2),
                                      'max':round(max(ordered),2)},
                        'peak_client_calls_pending':self.peak_inflight, 'results':dict(self.counts),
                        'invariant_checks':snapshot['invariant_checks'], 'invariant_violations':snapshot['violations'],
                        'max_sampled_worker_queue_depth':snapshot['max_sampled_queue_depth'], 'final_allocations':0}
        finally:
            self.admin_client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', type=int, default=32)
    parser.add_argument('--bridges', type=int, default=8)
    parser.add_argument('--rounds', type=int, default=20)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    if not 2 <= args.agents <= 128 or not 2 <= args.bridges <= 32 or not 1 <= args.rounds <= 200:
        parser.error('agents: 2..128; bridges: 2..32; rounds: 1..200')
    report = {'agents':args.agents, 'bridges':args.bridges, 'rounds':args.rounds, 'seed':args.seed,
              'evidence':'Synthetic actor metadata through real STDIO MCP; simulated device and input; no native UI or HID.',
              'settings_overrides':{'notification_cooldown_seconds':0, 'disconnect_grace_seconds':1,
                                    'notification_seconds':3600, 'question_seconds':3600}}
    try:
        with tempfile.TemporaryDirectory(prefix='abralia-race-', dir='/private/tmp') as temp:
            config = BrokerConfig(**report['settings_overrides'])
            service = AuditedService(temp, config)
            run = StressRun(args, Path(temp), service)
            try:
                with service:
                    report.update(asyncio.run(run.execute()))
            finally:
                report.update(cleanup_error=service.cleanup_error, worker_error=service.worker_error,
                              socket_removed=not service.endpoint.exists())
    except Exception as error:
        # Do not leak allocation tokens via an unexpected exception message.
        report.update(status='failed', error=re.sub(r'\b[0-9a-f]{48}\b', '<slot-token>', str(error)))
        if 'run' in locals():
            report.update(phases=run.phases, mcp_calls=len(run.latencies))
    if report.get('cleanup_error') or report.get('worker_error'):
        report['status'] = 'failed'
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('results','phases')}, indent=2))
    return 0 if report.get('status') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
