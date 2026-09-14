# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from abralia.backend.client_lifetime import capture_client_owner, owner_alive
from abralia.backend.core import Broker, Caller
from abralia.backend.ipc import BrokerClient
from abralia.backend.recovery import AllocationRecovery, proof_digest
from abralia.backend.service import BrokerService

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
OWNER = {'kind':'codex_gui','pid':1234,'parent':1,'started':'fixture start',
         'tty':'??','executable':'/Applications/Codex.app/Contents/MacOS/Codex'}


class ProcessOwnerTests(unittest.TestCase):
    def test_tui_inside_gui_must_also_remain_alive(self):
        tui={'pid':200,'parent':1234,'started':'tui','tty':'ttys001','executable':'/opt/bin/codex'}
        gui={k:v for k,v in OWNER.items() if k!='kind'}
        with patch('abralia.backend.client_lifetime.process_identity', side_effect=lambda p:tui if p==200 else gui):
            captured=capture_client_owner(200)
            self.assertEqual(captured['client_process']['pid'],200)
            self.assertTrue(owner_alive(captured))
        with patch('abralia.backend.client_lifetime.process_identity', side_effect=lambda p:None if p==200 else gui):
            self.assertFalse(owner_alive(captured))

    def test_gui_ancestor_wins_over_daemon_and_process_restart_is_not_alive(self):
        daemon = {'pid':200,'parent':1234,'started':'daemon','tty':'??','executable':'/app/Resources/codex'}
        with patch('abralia.backend.client_lifetime.process_identity', side_effect=lambda p: daemon if p==200 else {k:v for k,v in OWNER.items() if k!='kind'}):
            self.assertEqual(capture_client_owner(200), OWNER)
            self.assertTrue(owner_alive(OWNER))
        with patch('abralia.backend.client_lifetime.process_identity', return_value={**OWNER,'started':'new process'}):
            self.assertFalse(owner_alive(OWNER))
        with patch('abralia.backend.client_lifetime.process_identity', return_value=None):
            self.assertFalse(owner_alive(OWNER))

    def test_tui_requires_its_original_terminal_and_unknown_ancestry_fails_closed(self):
        tui={'pid':20,'parent':1,'started':'start','tty':'ttys001','executable':'/opt/bin/codex'}
        with patch('abralia.backend.client_lifetime.process_identity', side_effect=lambda p:tui if p==20 else None):
            self.assertEqual(capture_client_owner(20)['kind'], 'codex_tui')
        with patch('abralia.backend.client_lifetime.process_identity', return_value={**tui,'tty':'??'}):
            self.assertFalse(owner_alive({**tui,'kind':'codex_tui'}))
        with patch('abralia.backend.client_lifetime.process_identity', return_value=None):
            self.assertIsNone(capture_client_owner(20))


class RecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='abralia-recovery-',dir='/private/tmp')
        self.root=Path(self.temp.name)
        self.endpoint=self.root/'broker.sock'
        self.clients=[]
        self.live=True
        self.serial=0
        self.service=self.start_service()

    def start_service(self):
        return BrokerService(self.root, PROFILE, endpoint=self.endpoint,
                             recovery_owner_check=lambda owner:self.live and owner==OWNER).start()

    def tearDown(self):
        for client in self.clients: client.close()
        self.service.close()
        self.temp.cleanup()

    def client(self, **kwargs):
        c=BrokerClient(self.root,endpoint=self.endpoint,recover_slots=True,recovery_owner=OWNER,**kwargs)
        self.clients.append(c)
        return c

    def call(self,c,n,op,**args):
        self.serial+=1
        if op!='get_status': args.setdefault('idempotency_key',str(self.serial))
        return c.request({'type':'call','operation':op,'metadata':{'thread_id':str(UUID(int=n))},'arguments':args})

    def acquire(self,c,n):
        r=self.call(c,n,'acquire_slot',label=f'Caller {n}',harness='codex_desktop')
        self.assertEqual(r['status'],'accepted')
        return r['allocation']

    def restart(self):
        self.service.close()
        self.service=self.start_service()
        self.assertFalse(self.service.broker.slots)

    def reconnect(self,c):
        for _ in range(4):
            r=c.request({'type':'ping'})
            if r.get('backend_epoch')==self.service.broker.epoch: return
        self.fail('client did not reconnect')

    def test_live_client_recovers_two_owned_positions_colors_and_fresh_tokens_only(self):
        c=self.client(); a=self.acquire(c,1); b=self.acquire(c,2)
        self.call(c,1,'set_slot_state',slot_token=a['slot_token'],state='progressing',summary='PRIVATE SUMMARY',progress=.4)
        self.call(c,1,'set_notification',slot_token=a['slot_token'],enabled=True)
        state=self.endpoint.with_suffix('.slots.json')
        text=state.read_text()
        self.assertNotIn(a['slot_token'],text)
        self.assertNotIn(c.recovery_key,text)
        self.assertNotIn('PRIVATE SUMMARY',text)
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.restart(); self.reconnect(c)
        for number,old in ((1,a),(2,b)):
            current=self.call(c,number,'get_status')['allocation']
            self.assertEqual((current['slot_id'],current['identity_color']), (old['slot_id'],old['identity_color']))
            self.assertNotEqual(current['slot_token'],old['slot_token'])
            self.assertEqual(current['state'],'idle')
            self.assertIsNone(current['notification'])
            self.assertIsNone(current['question'])
        self.assertFalse(self.service.broker.active)
        rejected=self.call(c,1,'set_slot_state',slot_token=a['slot_token'],state='completed')
        self.assertEqual(rejected['reason'],'invalid_or_stale_slot_token')

    def test_closed_bridge_or_closed_gui_does_not_restore(self):
        c=self.client(); self.acquire(c,1)
        c.close()
        self.restart()
        fresh=self.client(); self.reconnect(fresh)
        self.assertNotIn('allocation', self.call(fresh,1,'get_status'))
        self.acquire(fresh,1)
        self.live=False
        self.restart(); self.reconnect(fresh)
        self.assertFalse(self.service.broker.slots)

    def test_quarantine_reserves_positions_and_colors_for_a_late_live_bridge(self):
        survivor=self.client(); old=self.acquire(survivor,1)
        self.restart()
        newcomer=self.client(); new=self.acquire(newcomer,2)
        self.assertEqual(new['slot_id'],2)
        self.assertNotEqual(new['identity_color'],old['identity_color'])
        self.reconnect(survivor)
        self.assertEqual(self.call(survivor,1,'get_status')['allocation']['slot_id'],1)

    def test_one_caller_can_resume_through_either_registered_bridge(self):
        a,b=self.client(),self.client()
        original=self.acquire(a,1)
        self.assertEqual(self.acquire(b,1)['slot_token'],original['slot_token'])
        a.close()
        self.restart(); self.reconnect(b)
        self.assertEqual(self.call(b,1,'get_status')['allocation']['slot_id'],1)

    def test_lost_recovery_ack_does_not_rotate_token_twice(self):
        c=self.client(); self.acquire(c,1)
        self.restart()
        with c.lock: c._close()
        exchange=c._exchange
        def lost(message):
            result=exchange(message)
            if message.get('type')=='hello': raise OSError('fixture lost ACK')
            return result
        with patch.object(c,'_exchange',side_effect=lost):
            self.assertEqual(c.request({'type':'ping'})['reason'],'backend_unavailable')
        first=self.service.broker.slots[1].slot_token
        self.reconnect(c)
        self.assertEqual(self.call(c,1,'get_status')['allocation']['slot_token'],first)

    def test_explicit_release_with_lost_ack_does_not_resurrect(self):
        c=self.client(); a=self.acquire(c,1)
        exchange=c._exchange
        def lost(message):
            result=exchange(message)
            if message.get('operation')=='release_slot': raise OSError('fixture lost ACK')
            return result
        with patch.object(c,'_exchange',side_effect=lost):
            self.call(c,1,'release_slot',slot_token=a['slot_token'])
        self.assertFalse(c.resume_claims)
        self.assertEqual(json.loads(self.endpoint.with_suffix('.slots.json').read_text())['allocations'],[])
        self.restart(); self.reconnect(c)
        self.assertFalse(self.service.broker.slots)

    def test_wrong_caller_release_cannot_remove_another_callers_recovery_claim(self):
        c=self.client(); a=self.acquire(c,1); self.acquire(c,2)
        r=self.call(c,2,'release_slot',slot_token=a['slot_token'])
        self.assertEqual(r['status'],'rejected')
        self.restart(); self.reconnect(c)
        self.assertEqual(set(self.service.broker.slots),{1,2})

    def test_committed_acquisition_with_lost_ack_is_recovered_without_reallocation(self):
        c=self.client()
        exchange=c._exchange
        def lost(message):
            result=exchange(message)
            if message.get('operation')=='acquire_slot': raise OSError('fixture lost acquire ACK')
            return result
        with patch.object(c,'_exchange',side_effect=lost):
            r=self.call(c,1,'acquire_slot',label='Caller 1',harness='codex_desktop')
        self.assertEqual(r['reason'],'backend_unavailable')
        original=self.service.broker.slots[1].slot_token
        self.restart(); self.reconnect(c)
        self.assertEqual(set(self.service.broker.slots),{1})
        self.assertNotEqual(self.service.broker.slots[1].slot_token,original)

    def test_persistence_failure_rolls_back_acquire_and_release(self):
        c=self.client(); a=self.acquire(c,1)
        with patch('abralia.backend.recovery.os.replace',side_effect=OSError('fixture disk failure')):
            r=self.call(c,1,'release_slot',slot_token=a['slot_token'])
        self.assertEqual(r['reason'],'recovery_storage_unavailable')
        self.assertEqual(self.service.broker.slots[1].slot_token,a['slot_token'])
        self.service.recovery.error=None
        with patch('abralia.backend.recovery.os.replace',side_effect=OSError('fixture disk failure')):
            r=self.call(c,2,'acquire_slot',label='New',harness='codex_desktop')
        self.assertEqual(r['status'],'rejected')
        self.assertEqual(set(self.service.broker.slots),{1})

    def test_real_stdio_bridge_heartbeat_restores_without_a_model_call(self):
        from mcp import Client, StdioServerParameters
        launcher=self.root/'launch_mcp.py'
        launcher.write_text("from abralia.backend import ipc\nipc.capture_client_owner = lambda: " + repr(OWNER) +
                            "\nfrom abralia.backend.mcp import main\nraise SystemExit(main())\n")
        async def scenario():
            import sys
            parameters=StdioServerParameters(command=sys.executable,
                args=['-B',str(launcher),'--project',str(self.root),'--socket',str(self.endpoint)],
                env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'})
            meta={'x-codex-turn-metadata':{'thread_id':str(UUID(int=50))}}
            async with Client(parameters) as client:
                r=await client.call_tool('acquire_slot',{'label':'STDIO fixture','harness':'codex_desktop','idempotency_key':'acquire'},meta=meta)
                original=r.structured_content['allocation']
                self.restart()
                deadline=time.monotonic()+12
                # No MCP/model call is made while the bridge's own heartbeat restores it.
                while time.monotonic()<deadline and not self.service.broker.slots:
                    await asyncio.sleep(.05)
                self.assertEqual(list(self.service.broker.slots),[original['slot_id']])
                self.assertNotEqual(self.service.broker.slots[original['slot_id']].slot_token,original['slot_token'])
                result=await client.call_tool('get_status',{},meta=meta)
                self.assertEqual(result.structured_content['allocation']['slot_id'],original['slot_id'])
        asyncio.run(scenario())


class RecoveryFileTests(unittest.TestCase):
    def test_prevalidated_owner_cannot_resume_another_owners_proof(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory); path = root/'state.json'
            callers = [Caller('codex:'+str(UUID(int=n)), str(UUID(int=n)), surface='codex_desktop') for n in (51, 52)]
            owners = [OWNER, {**OWNER, 'pid': 1235, 'started': 'other owner generation'}]
            proofs = [proof_digest(letter*64) for letter in ('a', 'b')]
            records = [{'slot_id': index+1, 'caller': asdict(caller), 'label': 'Fixture',
                        'identity_color': color, 'leases': [{'proof': proof, 'owner': owner}]}
                       for index, (caller, owner, proof, color) in enumerate(zip(callers, owners, proofs, ('2080FF', 'FF8020')))]
            path.write_text(json.dumps({'version': 1, 'project': str(root), 'allocations': records})); path.chmod(0o600)
            broker = Broker()
            check = Mock(side_effect=AssertionError('native process checks must stay outside the device worker'))
            recovery = AllocationRecovery(broker, root, path, owner_check=check)
            recovery.load()
            self.assertIsNone(recovery.error)
            self.assertFalse(recovery.resume([callers[1].caller_id], proofs[1], validated_owner=owners[0]))
            self.assertFalse(recovery.resume([callers[0].caller_id], proofs[1], validated_owner=owners[0]))
            self.assertFalse(broker.slots)
            restored = recovery.resume([callers[0].caller_id], proofs[0], validated_owner=owners[0])
            self.assertEqual(set(restored), {callers[0].caller_id})
            broker.slots[1].agent_attached = False
            self.assertFalse(recovery.resume([callers[0].caller_id], proofs[0], validated_owner=owners[1]))
            self.assertFalse(broker.slots[1].agent_attached)
            check.assert_not_called()

    def test_editor_owner_record_loads_but_still_requires_live_owner_proof(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory); path = root/'state.json'
            caller = Caller('codex:'+str(UUID(int=12)), str(UUID(int=12)), surface='unknown')
            editor = {**OWNER, 'kind': 'codex_editor', 'executable': '/Applications/Visual Studio Code.app/Contents/Resources/codex'}
            proof = proof_digest('c'*64)
            record = {'slot_id': 1, 'caller': asdict(caller), 'label': 'Editor task', 'identity_color': '2080FF',
                      'leases': [{'proof': proof, 'owner': editor}]}
            path.write_text(json.dumps({'version': 1, 'project': str(root), 'allocations': [record]})); path.chmod(0o600)
            broker = Broker()
            recovery = AllocationRecovery(broker, root, path, owner_check=lambda _: False)
            recovery.load()
            self.assertIsNone(recovery.error)
            self.assertEqual(broker.reserved_slots, {1})
            self.assertFalse(recovery.resume([caller.caller_id], proof))
            self.assertFalse(broker.slots)

    def test_unclaimed_records_expire_and_symlink_or_wrong_project_is_not_loaded(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root=Path(directory); path=root/'state.json'; now=[0]
            caller=Caller('codex:'+str(UUID(int=1)),str(UUID(int=1)),surface='codex_desktop')
            record={'slot_id':1,'caller':asdict(caller),'label':'Caller','identity_color':'2080FF',
                    'leases':[{'proof':proof_digest('a'*64),'owner':OWNER}]}
            path.write_text(json.dumps({'version':1,'project':str(root),'allocations':[record]})); path.chmod(0o600)
            broker=Broker(clock=lambda:now[0])
            recovery=AllocationRecovery(broker,root,path,grace_seconds=5,owner_check=lambda _:True)
            recovery.load()
            self.assertEqual(broker.reserved_slots,{1})
            self.assertFalse(broker.slots)
            recovery.begin_window()
            now[0]=6; recovery.expire(); recovery.save()
            self.assertFalse(broker.reserved_slots)
            self.assertFalse(recovery.resume([caller.caller_id],proof_digest('a'*64)))
            wrong=AllocationRecovery(Broker(),root/'other',path)
            wrong.load(); self.assertEqual(wrong.error,'invalid_recovery_state')
            path.unlink(); target=root/'user-file'; target.write_text('USER DATA')
            path.symlink_to(target)
            linked=AllocationRecovery(Broker(),root,path)
            linked.load(); linked.save()
            self.assertEqual(target.read_text(),'USER DATA')
            self.assertIsNotNone(linked.error)


if __name__=='__main__': unittest.main()
