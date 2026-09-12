# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from question_focus_experiment import FocusEvidence, run


class QuestionFocusExperimentTests(unittest.TestCase):
    def test_pickup_and_dispatch_do_not_claim_native_focus_or_answer(self):
        task = str(UUID(int=1))
        evidence = FocusEvidence(task)
        events = [{"sequence": i, "kind": kind, "caller_id": "codex:" + task}
                  for i, kind in enumerate(("call_picked_up", "focus_dispatched_unverified", "slot_released"), 1)]
        evidence.observe({"events": events})
        summary = evidence.summary()
        self.assertTrue(summary['physical_pickup_received'])
        self.assertTrue(summary['task_open_dispatched_unverified'])
        self.assertIn('requires', summary['native_focus'])
        self.assertIn('requires', summary['native_answer'])

    def test_log_filters_other_agents_and_private_fields_and_deduplicates(self):
        task = str(UUID(int=1))
        evidence = FocusEvidence(task)
        snapshot = {"events": [
            {"sequence": 1, "kind": "set_slot_state", "caller_id": "another", "summary": "private"},
            {"sequence": 2, "kind": "call_picked_up", "caller_id": "codex:" + task,
             "slot_id": 1, "slot_token": "private", "summary": "private"}],
            "slots": [{"caller_id": "codex:" + task, "slot_id": 1, "page": 1, "f_key": "F1",
                       "visible": True, "state": "idle", "summary": "private", "slot_token": "private"}]}
        rows = evidence.observe(snapshot)
        self.assertNotIn('private', str(rows))
        self.assertNotIn('another', str(rows))
        self.assertEqual(evidence.observe(snapshot), [])

    def test_interrupt_stops_owned_backend_and_records_no_unobserved_success(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(thread_id=str(UUID(int=1)), project=directory,
                profile='builtin:keychron-v3-8k-ansi-encoder-effect25', mode='simulated', timeout=10,
                output=Path(directory)/'trace.jsonl')
            with patch('question_focus_experiment.BrokerService') as factory, contextlib.redirect_stdout(io.StringIO()):
                service = factory.return_value
                service.stop_event.is_set.return_value = False
                service.submit.side_effect = KeyboardInterrupt
                service.cleanup_error = service.worker_error = None
                result = run(args)
            service.close.assert_called_once()
            self.assertEqual(result, 2)
            self.assertIn('"physical_pickup_received": false', args.output.read_text())


if __name__ == '__main__':
    unittest.main()
