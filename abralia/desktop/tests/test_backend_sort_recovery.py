# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from abralia.backend.core import Broker, Caller
from abralia.backend.recovery import AllocationRecovery
from abralia.backend.service import BrokerService

PROFILE = 'builtin:keychron-v3-8k-ansi-encoder-effect25'
OWNER = {'kind':'codex_gui','pid':1234,'parent':1,'started':'fixture start',
         'tty':'??','executable':'/Applications/Codex.app/Contents/MacOS/Codex'}
PROOF = 'a' * 64


class SortRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = BrokerService(self.root, PROFILE, mode='simulated', endpoint=self.root/'broker.sock')
        self.b = self.service.broker
        self.recovery = self.service.recovery
        self.callers = []
        for number, project in enumerate(('A','B','A'), 1):
            task = str(UUID(int=number))
            caller = Caller('codex:'+task, task, surface='codex_desktop', project_id=project,
                            project_path=str(self.root/project))
            self.callers.append(caller)
            result = self.b.call(caller, 'acquire_slot', {'label':f'{project}{number}','idempotency_key':'acquire'})
            self.assertEqual(result['status'], 'accepted')
            self.recovery.attach(caller.caller_id, PROOF, OWNER)
        self.recovery.save()
        self.assertIsNone(self.recovery.error)
        self.b.set_active(True); self.b.toggle_navigation()

    def restored(self, *, live=True):
        broker = Broker()
        recovery = AllocationRecovery(broker, self.root, self.recovery.path, owner_check=lambda _: live)
        recovery.load()
        return broker, recovery

    @staticmethod
    def placements(broker):
        return {s.slot_id: (s.slot_token,s.position,s.arrival_order,s.individual_color,s.project_color,s.identity_color)
                for s in broker.slots.values()}

    def test_policy_both_palettes_and_positions_survive_reverse_order_live_recovery(self):
        self.assertTrue(self.service._commit_sort())
        before = self.placements(self.b)
        self.assertEqual([s.slot_id for s in self.b.visible_slots()], [1,3,2])
        state = self.b.export_sort_state()
        recovered, recovery = self.restored()
        self.assertIsNone(recovery.error)
        self.assertEqual(recovered.export_sort_state(), state)
        self.assertFalse(recovered.slots)
        recovered.set_active(True); recovered.toggle_navigation()
        self.assertFalse(recovered.toggle_sort())
        self.assertEqual(recovered.sort_policy, 'project')
        for caller in reversed(self.callers):
            self.assertIn(caller.caller_id, recovery.resume([caller.caller_id], PROOF))
        after = self.placements(recovered)
        for number in before:
            self.assertNotEqual(after[number][0], before[number][0])
            self.assertEqual(after[number][1:], before[number][1:])
        self.assertTrue(recovered.toggle_sort())
        self.assertEqual([s.slot_id for s in recovered.visible_slots()], [1,2,3])
        self.assertTrue(all(s.identity_color == s.individual_color for s in recovered.slots.values()))

    def test_closed_native_owner_never_restores_even_with_valid_order_metadata(self):
        self.assertTrue(self.service._commit_sort())
        broker, recovery = self.restored(live=False)
        self.assertFalse(recovery.resume([c.caller_id for c in self.callers], PROOF))
        self.assertFalse(broker.slots)
        self.assertEqual(len(recovery.pending), 3)

    def test_legacy_version_two_migrates_stable_arrival_and_preserves_existing_positions(self):
        saved = json.loads(self.recovery.path.read_text())
        saved.pop('sort_state')
        for record in saved['allocations']:
            for key in ('arrival_order','individual_color','project_color'):
                record.pop(key)
        saved['allocations'][0]['display_position'] = 9
        self.recovery.path.write_text(json.dumps(saved))
        broker, recovery = self.restored()
        self.assertIsNone(recovery.error)
        self.assertEqual(broker.sort_policy, 'incoming')
        recovery.resume([c.caller_id for c in self.callers], PROOF)
        self.assertEqual(broker.slots[1].position, 9)
        self.assertEqual([broker.slots[n].arrival_order for n in (1,2,3)], [3,1,2])
        self.assertEqual(broker.slots[1].individual_color, saved['allocations'][0]['identity_color'])
        recovery.save()
        current = json.loads(self.recovery.path.read_text())
        self.assertEqual(current['version'], 2)
        self.assertIn('sort_state', current)

    def test_legacy_reused_slot_ids_do_not_override_saved_display_order(self):
        saved = json.loads(self.recovery.path.read_text())
        saved.pop('sort_state')
        for record in saved['allocations']:
            for key in ('arrival_order','individual_color','project_color'):
                record.pop(key)
        saved['allocations'][0]['slot_id'] = 99
        saved['allocations'][1]['slot_id'] = 1
        self.recovery.path.write_text(json.dumps(saved))
        broker, recovery = self.restored()
        self.assertIsNone(recovery.error)
        recovery.resume([c.caller_id for c in self.callers], PROOF)
        self.assertEqual((broker.slots[99].position, broker.slots[99].arrival_order), (1,1))
        self.assertEqual((broker.slots[1].position, broker.slots[1].arrival_order), (2,2))

    def test_duplicate_colors_are_valid_but_duplicate_arrival_or_bad_palette_is_not(self):
        original = json.loads(self.recovery.path.read_text())
        duplicates = deepcopy(original)
        duplicates['allocations'][1]['individual_color'] = duplicates['allocations'][0]['individual_color']
        duplicates['allocations'][1]['identity_color'] = duplicates['allocations'][0]['identity_color']
        self.recovery.path.write_text(json.dumps(duplicates))
        _, recovery = self.restored()
        self.assertIsNone(recovery.error)
        variants = []
        bad = deepcopy(original); bad['allocations'][1]['arrival_order'] = bad['allocations'][0]['arrival_order']; variants.append(bad)
        bad = deepcopy(original); bad['allocations'][0]['project_color'] = 'nothex'; variants.append(bad)
        bad = deepcopy(original); bad['allocations'][0]['identity_color'] = '000000'; variants.append(bad)
        bad = deepcopy(original); bad['sort_state']['next_arrival_order'] = 1; variants.append(bad)
        bad = deepcopy(original); bad['sort_state']['policy'] = 'random'; variants.append(bad)
        bad = deepcopy(original); bad['sort_state'] = None; variants.append(bad)
        bad = deepcopy(original); bad['sort_state']['projects'][0]['colors'][self.callers[0].caller_id] = '000000'; variants.append(bad)
        for data in variants:
            with self.subTest(data=data):
                self.recovery.path.write_text(json.dumps(data))
                broker, recovery = self.restored()
                self.assertEqual(recovery.error, 'invalid_recovery_state')
                self.assertFalse(broker.slots)
                self.assertFalse(broker.reserved_positions)
                self.assertEqual(broker.sort_policy, 'incoming')

    def test_failed_sort_save_restores_layout_colors_tokens_selection_and_visuals(self):
        self.b.select_slot(self.b.slots[2].slot_token)
        slot = self.b.slots[1]
        self.b.call(slot.caller,'set_notification',{'slot_token':slot.slot_token,'enabled':True,'idempotency_key':'notice'})
        before, state = self.placements(self.b), self.b.export_sort_state()
        visual = deepcopy(vars(self.b._notification_visual_state))
        file_before = self.recovery.path.read_bytes()
        with patch('abralia.backend.recovery.os.replace', side_effect=OSError('fixture disk error')):
            self.assertFalse(self.service._commit_sort())
        self.assertEqual(self.placements(self.b), before)
        self.assertEqual(self.b.export_sort_state(), state)
        self.assertEqual(self.b.selected, 2)
        self.assertEqual(vars(self.b._notification_visual_state), visual)
        self.assertEqual(self.recovery.path.read_bytes(), file_before)
        self.assertFalse(any(e['kind'] == 'slot_sort_changed' for e in self.b.events))

    def test_failed_grouped_acquire_restores_counter_project_cache_and_old_positions(self):
        self.assertTrue(self.service._commit_sort())
        before, state = self.placements(self.b), self.b.export_sort_state()
        task = str(UUID(int=4))
        newcomer = Caller('codex:'+task, task, surface='codex_desktop', project_id='A',
                          project_path=str(self.root/'A'))
        command = {'role':'agent','connection':'unverified-fixture', 'message':{
            'type':'call','metadata':{'thread_id':task},'operation':'acquire_slot',
            'arguments':{'label':'A new','idempotency_key':'new'}}}
        hello = self.service._handle({'role':'agent','connection':'unverified-fixture',
            'handshake':True,'message':{'type':'hello','role':'agent','project':str(self.root)}})
        self.assertEqual(hello['status'], 'accepted')
        with patch.object(self.service, '_caller', return_value=newcomer), \
                patch('abralia.backend.recovery.os.replace', side_effect=OSError('fixture disk error')):
            result = self.service._handle(command)
        self.assertEqual(result['reason'], 'recovery_storage_unavailable')
        self.assertEqual(self.placements(self.b), before)
        self.assertEqual(self.b.export_sort_state(), state)
        self.recovery.error = None
        with patch.object(self.service, '_caller', return_value=newcomer):
            result = self.service._handle(command)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['allocation']['slot_id'], 4)
        self.assertEqual(self.b.slots[4].arrival_order, state['next_arrival_order'])
        self.assertEqual(self.b.slots[4].position, 3)
        self.assertEqual(self.b.slots[2].position, 4)

    def test_rollback_restores_pending_recovery_deadline_so_sort_cannot_remain_blocked(self):
        broker, recovery = self.restored()
        now = [0]
        broker.clock = lambda: now[0]
        recovery.begin_window()
        self.service.broker, self.service.recovery = broker, recovery
        checkpoint = self.service._recovery_checkpoint()
        now[0] = 31
        recovery.expire()
        self.assertFalse(recovery.pending)
        self.service._rollback_recovery(checkpoint)
        self.assertTrue(recovery.pending)
        self.assertEqual(recovery.deadline, 30)
        recovery.expire()
        self.assertFalse(recovery.pending)
        self.assertFalse(broker.reserved_positions)


if __name__ == '__main__':
    unittest.main()
