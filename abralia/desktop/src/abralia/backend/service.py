# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Persistent backend: bounded IPC queue feeding exactly one device worker."""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from copy import copy, deepcopy
from dataclasses import replace
import fcntl
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import logging
import os
from pathlib import Path
import queue
import socket
import socketserver
import stat
import threading
import time
from uuid import UUID, uuid4

from .core import Broker, BrokerConfig, Caller, text_argument
from .device import DeviceDriver
from .ipc import MAX_REQUEST, socket_path
from .recovery import AllocationRecovery, proof_digest, MAX_RECORDS
from .task_catalog import codex_project_tasks, requested_task_ids
from .project_policy import ProjectPolicy, default_state_dir, project_identity, read_private_json, write_private_json
from .project_registry import ProjectRegistry

LOG = logging.getLogger(__name__)


class BrokerService:
    def __init__(self, project: str | Path, profile: str, *, mode="simulated",
                 config: BrokerConfig | None = None, endpoint: str | Path | None = None,
                 registered_thread_id: str | None = None, desktop_thread_ids=(),
                 observe_codex=False, codex_home=None, codex_hook_journal=None, recovery_owner_check=None,
                 state_dir: str | Path | None = None, shared: bool = False):
        if type(shared) is not bool:
            raise ValueError('shared_must_be_boolean')
        self.shared = shared
        self.project = str(Path(project).resolve())
        if not Path(self.project).is_dir() or mode not in ("simulated", "hardware"):
            raise ValueError("existing project directory and hardware/simulated mode required")
        self.profile = profile
        self.mode = mode
        self.config = config or BrokerConfig()
        self.codex_home = codex_home
        self.endpoint = Path(endpoint or socket_path(project))
        self.project_id = project_identity(self.project)
        self.state_dir = (Path(state_dir or os.environ.get('ABRALIA_STATE_DIR') or default_state_dir()) if shared
                          else Path(state_dir) if state_dir is not None else None)
        self.registry = ProjectRegistry(self.state_dir) if shared else None
        self.enrolled_projects = {}
        self.connection_projects = {}
        self.hook_records = {}
        self.hook_counters = {}
        self.next_registry_refresh = 0.
        production_endpoint = self.endpoint.resolve() == socket_path(project).resolve()
        policy_root = self.state_dir or (default_state_dir() if production_endpoint else None)
        policy_path = (policy_root / 'project-policies' / f'{self.project_id}.json' if policy_root is not None
                       else self.endpoint.with_suffix('.policy.json'))
        self.project_policy = ProjectPolicy(self.project, policy_path, state_dir=policy_root)
        self.info_path = self.endpoint.with_suffix('.info.json')
        self.info_published = False
        self.registered_thread_id = str(UUID(registered_thread_id)) if registered_thread_id else None
        self.desktop_threads = {str(UUID(value)) for value in desktop_thread_ids}
        self.broker = Broker(self.config)
        self.recovery = AllocationRecovery(self.broker, self.project, self.endpoint.with_suffix('.slots.json'),
                                           grace_seconds=self.config.recovery_grace_seconds,
                                           owner_check=recovery_owner_check, shared=shared,
                                           project_validator=self._recovery_project_allowed if shared else None)
        self.connection_proofs = {}
        self.connection_owners = {}
        self.connection_terminals = {}
        self.connection_terminal_callers = {}
        self.terminal_task_sources = {}
        self.commands = queue.Queue(maxsize=256)
        self.stop_event = threading.Event()
        self.started = Future()
        self.listener_ready = threading.Event()
        self.connections: dict[str, set[str]] = {}
        self.last_seen: dict[str, float] = {}
        self.driver = None
        self.server = None
        self.worker = None
        self.listener = None
        self.lock_file = None
        self.cleanup_error = None
        self.worker_error = None
        self.observer = None
        if observe_codex:
            from .codex_observer import CodexObserver
            self.observer = CodexObserver(self, codex_home=codex_home, hook_journal=codex_hook_journal)

    def _recovery_project_allowed(self, caller):
        row = next((p for p in self.registry.list() if p['project_id'] == caller.project_id), None)
        return bool(row and caller.project_path == row['path'] and caller.project_generation == row['generation'])

    def _refresh_projects(self):
        if not self.shared:
            return
        rows = {p['project_id']: p for p in self.registry.list()}
        for identity, row in rows.items():
            if self.enrolled_projects.get(identity) != row:
                policy = ProjectPolicy(row['path'], self.state_dir / 'project-policies' / f'{identity}.json', state_dir=self.state_dir)
                self.broker.set_project_muted(policy.load(), identity)
        for slot in list(self.broker.slots.values()):
            row = rows.get(slot.caller.project_id)
            if row is None or slot.caller.project_generation != row['generation']:
                self.broker._release(slot)
                self.recovery.forget_reservation(slot.caller.caller_id)
                self.recovery.proofs.pop(slot.caller.caller_id, None)
        for owner, record in list(self.recovery.pending.items()):
            row = rows.get(record['caller'].get('project_id'))
            if row is None or record['caller'].get('project_generation') != row['generation']:
                self.recovery.forget_reservation(owner)
        self.enrolled_projects = rows
        self.next_registry_refresh = time.monotonic() + 1.

    def _connection_project(self, connection, *, cwd=None):
        if not self.shared:
            return None
        self._refresh_projects()
        binding = self.connection_projects.get(connection)
        row = self.enrolled_projects.get(binding['project_id']) if binding else None
        if row is None or row['generation'] != binding['generation']:
            raise ValueError('project_not_enabled')
        if cwd is not None:
            resolved = self.registry.resolve(cwd)
            if resolved is None or resolved['project_id'] != row['project_id']:
                raise ValueError('caller_project_mismatch')
        return row

    def _project_snapshot(self, row=None) -> dict:
        connected = set().union(*self.connections.values()) if self.connections else set()
        slots = list(self.broker.slots.values()) if row is None else [s for s in self.broker.slots.values() if s.caller.project_id == row['project_id']]
        identity = row['project_id'] if row else self.project_id
        root = row['path'] if row else self.project
        return {'project_id': identity, 'path': root, 'name': Path(root).name or root,
                'muted': self.broker.project_mutes.get(identity, False) if row else self.broker.project_muted,
                'task_count': len(slots),
                'connected_count': sum(slot.agent_attached and slot.caller.caller_id in connected
                                       for slot in slots),
                'hooks_received': self.hook_counters.get(identity, {}).get('count', 0),
                'last_hook_at': self.hook_counters.get(identity, {}).get('last_at'),
                'observed_task_count': sum(bool(s.observation) for s in slots)}

    def _project_snapshots(self):
        return [self._project_snapshot(p) for p in self.enrolled_projects.values()] if self.shared else [self._project_snapshot()]

    def _publish_info(self):
        try:
            backend_version = package_version('abralia-desktop')
        except PackageNotFoundError:
            backend_version = 'unknown'
        write_private_json(self.info_path, {
            'version': 1, 'backend_version': backend_version, 'pid': os.getpid(),
            'project': self.project, 'project_id': self.project_id,
            'endpoint': str(self.endpoint.resolve()), 'backend_epoch': self.broker.epoch,
            'profile': str(self.profile), **({'shared': True} if self.shared else {})})
        self.info_published = True

    def _remove_info(self):
        if not self.info_published:
            return
        try:
            data = read_private_json(self.info_path)
            if data and data.get('backend_epoch') == self.broker.epoch and data.get('pid') == os.getpid():
                self.info_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass  # Never delete an edited, replaced, or unsafe metadata entry.
        self.info_published = False

    def submit(self, command: dict) -> dict:
        if self.stop_event.is_set():
            return {"status": "skipped", "reason": "backend_stopping"}
        future = Future()
        try:
            self.commands.put_nowait((command, future))
        except queue.Full:
            return {"status": "skipped", "reason": "backend_busy"}
        try:
            return future.result(timeout=6)
        except TimeoutError:
            future.cancel()
            return {"status": "skipped", "reason": "backend_timeout", "retry": "reuse_idempotency_key"}

    def _recovery_checkpoint(self):
        state = {key:copy(value) for key,value in vars(self.broker).items()}
        state['slots'] = {number:copy(slot) for number,slot in self.broker.slots.items()}
        # Project color registries are nested and updated when a new sibling
        # arrives; a shallow checkpoint would retain an uncommitted color.
        if '_project_colors' in state:
            state['_project_colors'] = deepcopy(state['_project_colors'])
        return (state, self.recovery.pending.copy(), {k:v.copy() for k,v in self.recovery.proofs.items()},
                self.recovery.restored, self.recovery.deadline, self.recovery.window_started)

    def _rollback_recovery(self, checkpoint):
        state, pending, proofs, restored, deadline, window_started = checkpoint
        vars(self.broker).clear()
        vars(self.broker).update(state)
        self.recovery.pending, self.recovery.proofs, self.recovery.restored = pending, proofs, restored
        self.recovery.deadline, self.recovery.window_started = deadline, window_started
        self.broker.event('recovery_commit_failed')
        return {'status':'rejected','reason':'recovery_storage_unavailable','backend_epoch':self.broker.epoch}

    def _commit_gap(self, gap):
        checkpoint = self._recovery_checkpoint()
        if not self.broker.close_gap(gap):
            return False
        self.recovery.save()
        if self.recovery.error:
            self._rollback_recovery(checkpoint)
            self.broker.cancel_gap_hold('recovery_storage_unavailable')
            return False
        # The confirmation flash starts after durable commit, even on slow storage.
        now = self.broker.clock()
        self.broker.gap_feedback.update(started_at=now, until=now + self.config.gap_close_flash_seconds)
        return True

    def _commit_sort(self):
        """Publish reordered positions/colors only after their durable save."""
        checkpoint = self._recovery_checkpoint()
        if not self.broker.toggle_sort():
            return False
        self.recovery.save()
        if self.recovery.error:
            self._rollback_recovery(checkpoint)
            return False
        return True

    def _caller(self, message: dict, connection: str, role: str, fallback: str | None) -> Caller:
        if role != "agent":
            raise ValueError("admin_connections_cannot_impersonate_model_calls")
        metadata = message.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("invalid_metadata")
        thread_id = metadata.get("thread_id")
        project = None
        if self.shared:
            cwd = metadata.get('cwd')
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                raise ValueError('native_project_context_required')
            project = self._connection_project(connection, cwd=cwd)
        if thread_id:
            if not isinstance(thread_id, str):
                raise ValueError("invalid_thread_id")
            thread_id = str(UUID(thread_id))
            source = "codex_metadata"
        elif fallback and fallback == self.registered_thread_id:
            thread_id, source = fallback, "registered_test_connection"
        else:
            raise ValueError("caller_identity_unavailable")
        desktop_registered = thread_id in self.desktop_threads
        caller = Caller("codex:" + thread_id, thread_id, source,
                        "codex_desktop" if desktop_registered else "unknown",
                        "registered_by_host" if desktop_registered else "unknown",
                        project['project_id'] if project else None, project['path'] if project else None,
                        project['generation'] if project else None)
        allocation = self.broker.slots.get(self.broker.owners.get(caller.caller_id, -1))
        if (self.shared and allocation is None and message.get('operation') == 'acquire_slot'
                and Path(cwd).resolve() != Path(project['path'])):
            # Also protect callers still running an older MCP bridge. Passive
            # ancestor routing does not authorize creating a new task under it.
            raise ValueError('native_workspace_requires_exact_enrollment')
        if allocation:
            if (allocation.caller.project_id, allocation.caller.project_generation) != (caller.project_id, caller.project_generation):
                raise ValueError('caller_project_mismatch')
            if not desktop_registered and allocation.caller.surface_source in ("agent_reported", "registered_by_host"):
                caller = replace(caller, surface=allocation.caller.surface,
                                 surface_source=allocation.caller.surface_source)
            allocation.caller = caller
        pending = self.recovery.pending.get(caller.caller_id)
        if self.shared and pending and pending['caller'].get('project_id') != caller.project_id:
            raise ValueError('caller_project_mismatch')
        self.connections.setdefault(connection, set()).add(caller.caller_id)
        self.broker.connected(caller.caller_id)
        return caller

    def _attach_terminal(self, slot, connection):
        """Associate a CLI root with its native client, never its subagents.

        These records are session-local. Slot recovery retains placement but
        cannot turn an old process/pane hint into a fresh focus authority.
        """
        context = self.connection_terminals.get(connection)
        if not context or slot.caller.surface not in ('codex_cli', 'unknown'):
            return
        thread_id = slot.caller.thread_id
        source = self.terminal_task_sources.get(thread_id)
        if source not in ('cli', 'vscode'):
            return
        if context['owner']['kind'] == 'codex_editor' and source != 'vscode':
            return
        attachments = self.broker.terminal_attachments
        caller_id = slot.caller.caller_id
        self.connection_terminal_callers.setdefault(connection, set()).add(caller_id)
        existing = attachments.get(caller_id)
        competing = any(other != connection and caller_id in registered
                        and other in self.connections
                        and self.connection_terminals.get(other, {}).get('owner') != context['owner']
                        for other, registered in self.connection_terminal_callers.items())
        if competing:
            # The same task can be open in several clients. No arbitrary
            # last-writer preference for two different live destinations.
            attachments.pop(caller_id, None)
            self.broker.navigation_results[slot.slot_token] = {
                'status': 'unavailable', 'reason': 'multiple_live_terminal_clients'}
            return
        same_client_tasks = {task for connected, tasks in self.connection_terminal_callers.items()
                             if connected in self.connections
                             and self.connection_terminals.get(connected, {}).get('owner') == context['owner']
                             for task in tasks if task in self.broker.owners}
        if len(same_client_tasks) > 1 and context['provider'] != 'vscode':
            # Native root kind does not prove which of several roots sharing a
            # client is displayed. Never infer a resume from last tool-call order.
            for task in same_client_tasks:
                attachments.pop(task, None)
                target = self.broker.slots[self.broker.owners[task]]
                self.broker.navigation_results[target.slot_token] = {
                    'status': 'unavailable', 'reason': 'multiple_tasks_in_terminal_client'}
            return
        if existing and existing['context'] == context and existing['slot_token'] == slot.slot_token:
            return
        attachments[caller_id] = {'slot_token': slot.slot_token, 'context': deepcopy(context),
                                  'connection': connection, 'generation': str(uuid4())}
        self.broker.navigation_results.pop(slot.slot_token, None)
        self.broker.event('terminal_attached', slot, provider=context['provider'])

    def _drop_connection(self, connection: str):
        self.connection_terminals.pop(connection, None)
        self.connection_terminal_callers.pop(connection, None)
        for caller_id, attachment in list(self.broker.terminal_attachments.items()):
            if attachment.get('connection') == connection:
                self.broker.terminal_attachments.pop(caller_id, None)
        self.connection_projects.pop(connection, None)
        self.connection_proofs.pop(connection, None)
        self.connection_owners.pop(connection, None)
        owners = self.connections.pop(connection, set())
        self.last_seen.pop(connection, None)
        remaining = set().union(*self.connections.values()) if self.connections else set()
        for owner in owners - remaining:
            self.broker.disconnected_at(owner)
        # A bridge reload can briefly overlap its previous connection. Rebind
        # surviving native clients without waiting for another model tool call.
        for other, tasks in list(self.connection_terminal_callers.items()):
            if other not in self.connections:
                continue
            for caller_id in tuple(tasks):
                slot = self.broker.slots.get(self.broker.owners.get(caller_id))
                if slot:
                    self._attach_terminal(slot, other)

    def _apply_hook_record(self, slot):
        record = self.hook_records.get(slot.caller.thread_id)
        if record is None or record['project_id'] != slot.caller.project_id or record['generation'] != slot.caller.project_generation:
            return
        self.broker.observe_codex(slot.slot_token, {
            'source': 'codex_hook', 'thread_id': slot.caller.thread_id,
            'execution': record['execution'], 'turn_id': record.get('turn_id'), 'mode': None,
            'last_hook': record['last_hook'], 'questions': list(record['questions'].values()),
            'question_evidence': 'native hook invocation/response; UI visibility unverified', 'error': None})

    def _handle_hook(self, connection, message):
        if not self.shared or message.get('type') != 'hook_event':
            return {'status': 'rejected', 'reason': 'unsupported_hook_operation'}
        cwd = message.get('cwd')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError('native_project_context_required')
        project = self._connection_project(connection, cwd=cwd)
        thread_id = str(UUID(message.get('thread_id', '')))
        slot = self.broker.slots.get(self.broker.owners.get('codex:' + thread_id))
        if slot and (slot.caller.project_id != project['project_id'] or slot.caller.project_generation != project['generation']):
            raise ValueError('caller_project_mismatch')
        event = message.get('event')
        if event not in ('PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'SessionStart', 'SessionEnd'):
            raise ValueError('unsupported_hook_event')
        fields = {}
        for name in ('turn_id', 'tool_name', 'tool_use_id', 'output_kind', 'event_id'):
            value = message.get(name)
            if value is not None:
                if not isinstance(value, str) or len(value) > 256:
                    raise ValueError('invalid_hook_metadata')
                fields[name] = value
        count = message.get('question_count', 0)
        if type(count) is not int or not 0 <= count <= 32:
            raise ValueError('invalid_hook_question_count')
        old = self.hook_records.get(thread_id)
        if old and (old['project_id'], old['generation']) != (project['project_id'], project['generation']):
            old = None
        record = deepcopy(old) if old else {'project_id': project['project_id'], 'generation': project['generation'],
                                          'questions': {}, 'execution': 'unknown', 'turn_id': None}
        if fields.get('event_id') and record.get('last_event_id') == fields['event_id']:
            return {'status': 'accepted', 'replayed': True}
        record['last_event_id'] = fields.get('event_id')
        if 'turn_id' in fields:
            record['turn_id'] = fields['turn_id']
        if event in ('UserPromptSubmit', 'PreToolUse'):
            record['execution'] = 'running'
        question_id = fields.get('tool_use_id')
        tool = fields.get('tool_name')
        if tool in ('request_user_input', 'request_user_input_async') and question_id:
            existing = record['questions'].get(question_id)
            if existing is None or existing['stage'] in ('invoked', 'accepted'):
                question = dict(existing) if existing else {
                    'request_id': question_id, 'tool': tool, 'turn_id': record['turn_id'],
                    'question_count': count, 'stage': 'invoked', 'accepted_observed': False, 'reply_observed': False}
                if event == 'PostToolUse':
                    output = fields.get('output_kind')
                    stage = {'accepted': 'accepted', 'answers': 'response_received',
                             'returned_empty': 'returned_empty', 'failed': 'tool_ended_without_acceptance'}.get(output)
                    if stage:
                        question['stage'] = stage
                        question['accepted_observed'] |= stage == 'accepted'
                        question['reply_observed'] |= stage == 'response_received'
                        if stage == 'accepted':
                            record['execution'] = 'running'
                record['questions'][question_id] = question
        if event in ('Stop', 'SessionEnd'):
            record['execution'] = 'idle' if event == 'Stop' else 'interrupted'
            for question in record['questions'].values():
                if question['stage'] in ('invoked', 'accepted'):
                    question['stage'] = 'turn_ended_unconfirmed'
        while len(record['questions']) > 128:
            record['questions'].pop(next(iter(record['questions'])))
        record['last_hook'] = {'hook_event_name': event, **fields}
        self.hook_records[thread_id] = record
        while len(self.hook_records) > 1024:
            self.hook_records.pop(next(iter(self.hook_records)))
        counter = self.hook_counters.setdefault(project['project_id'], {'count': 0, 'last_at': None})
        counter.update(count=counter['count'] + 1, last_at=time.time())
        if slot:
            self._apply_hook_record(slot)
        return {'status': 'accepted', 'project_id': project['project_id'], 'allocation_present': slot is not None,
                'backend_epoch': self.broker.epoch}

    def _handle(self, command: dict) -> dict:
        connection = command["connection"]
        message = command["message"]
        kind = message.get("type")
        # Only the socket handler produces these facts. They are not fields
        # accepted from model arguments or from the IPC message body.
        self.terminal_task_sources.update(command.get('terminal_sources', {}))
        if command.get('handshake'):
            project = None
            if self.shared and command['role'] in ('agent', 'hook'):
                self._refresh_projects()
                candidate = message.get('project')
                if not isinstance(candidate, str) or not Path(candidate).is_absolute():
                    raise ValueError('project_not_enabled')
                project = self.registry.resolve(candidate)
                if project is None or project['path'] != str(Path(candidate).resolve()):
                    raise ValueError('project_not_enabled')
                supplied_generation = message.get('project_generation')
                if supplied_generation is not None and supplied_generation != project['generation']:
                    raise ValueError('stale_project_generation')
                self.connection_projects[connection] = project
            key = message.get('recovery_key')
            claims = message.get('resume_callers', [])
            if not isinstance(claims, list) or len(claims) > MAX_RECORDS:
                raise ValueError('invalid_resume_callers')
            for caller_id in claims:
                if not isinstance(caller_id, str) or not caller_id.startswith('codex:') or caller_id != 'codex:' + str(UUID(caller_id[6:])):
                    raise ValueError('invalid_resume_caller')
            restored = {}
            if key is not None:
                if command['role'] != 'agent':
                    raise ValueError('admin_cannot_resume_slots')
                proof = proof_digest(key)
                owner = message.get('recovery_owner')
                if command.get('owner_verified'):
                    self.connection_proofs[connection] = proof
                    self.connection_owners[connection] = owner
                    # Old bridges already supply native process lifetime proof.
                    # Their provider can be recovered from process ancestry;
                    # new bridges additionally supply bounded native pane IDs.
                    from .client_lifetime import normalize_terminal_context
                    candidate = message.get('terminal_context')
                    terminal = normalize_terminal_context(candidate, verify=False)
                    if terminal and terminal.get('owner') == owner:
                        self.connection_terminals[connection] = terminal
                    checkpoint = self._recovery_checkpoint()
                    restored = self.recovery.resume(claims, proof, project_id=project['project_id'] if project else None,
                                                    validated_owner=owner)
                    self.recovery.save()
                    if restored and self.recovery.error:
                        return self._rollback_recovery(checkpoint)
            elif claims:
                raise ValueError('resume_requires_live_bridge_proof')
            self.connections.setdefault(connection, set()).update(restored)
            self.last_seen[connection] = time.monotonic()
            for owner in restored:
                self.broker.connected(owner)
                slot = self.broker.slots.get(self.broker.owners.get(owner))
                if slot:
                    self._attach_terminal(slot, connection)
            self.recovery.save()
            return {'status':'accepted','backend_epoch':self.broker.epoch,'restored_tokens':restored}
        if command.get('role') == 'observer':
            if kind == 'codex_observation_targets':
                return {'status':'accepted','targets':[
                    {'token':s.slot_token,'thread_id':s.caller.thread_id, 'project': s.caller.project_path or self.project}
                    for s in self.broker.slots.values() if s.caller.thread_id and s.caller.caller_id.startswith('codex:')]}
            if kind == 'codex_observation':
                observation = message['observation']
                record = self.hook_records.get(observation.get('thread_id'))
                if record:
                    for question in observation.get('questions', []):
                        if question.get('stage') not in ('invoked', 'accepted') and question.get('request_id') in record['questions']:
                            record['questions'][question['request_id']] = dict(question)
                accepted = self.broker.observe_codex(message['token'], message['observation'])
                return {'status':'accepted' if accepted else 'skipped'}
            return {'status':'rejected','reason':'unsupported_observer_message'}
        if kind == "disconnect":
            self._drop_connection(connection)
            return {"status": "accepted"}
        if self.shared and command.get('role') in ('agent', 'hook'):
            self._connection_project(connection)
        if command.get('role') == 'hook':
            return self._handle_hook(connection, message)
        self.last_seen[connection] = time.monotonic()
        self.connections.setdefault(connection, set())
        if kind == "ping":
            for owner in self.connections[connection]:
                self.broker.connected(owner)
                slot = self.broker.slots.get(self.broker.owners.get(owner))
                if slot:
                    self._attach_terminal(slot, connection)
            return {"status": "accepted", "backend_epoch": self.broker.epoch}
        if kind == "call":
            stable = message.get('operation') in ('acquire_slot', 'release_slot')
            checkpoint = self._recovery_checkpoint() if stable else None
            try:
                caller = self._caller(message, connection, command["role"], command.get("fallback"))
            except ValueError as error:
                if str(error) != 'native_workspace_requires_exact_enrollment':
                    raise
                return {'status':'rejected','reason':str(error),
                        'project_context':{
                            'native_workspace':str(Path(message['metadata']['cwd']).resolve()),
                            'enrolled_root':self.connection_projects[connection]['path'],'match':'ancestor'},
                        'hint':'Use Add project in Abralia for the exact native workspace, or refresh the '
                               'Abralia MCP client and call enable_self. An enabled parent folder is not '
                               'used for a new task registration.'}
            self.recovery.expire()
            result = self.broker.call(caller, message.get("operation"), message.get("arguments"))
            if self.shared:
                native_workspace = str(Path(message['metadata']['cwd']).resolve())
                result['project_context'] = {
                    'native_workspace': native_workspace, 'enrolled_root': caller.project_path,
                    'match': 'exact' if native_workspace == caller.project_path else 'ancestor'}
            if result.get('status') == 'accepted':
                if caller.caller_id in self.broker.owners:
                    self._attach_terminal(self.broker.slots[self.broker.owners[caller.caller_id]], connection)
                    self.recovery.forget_reservation(caller.caller_id)
                    if message.get('operation') == 'acquire_slot':
                        self.recovery.attach(caller.caller_id, self.connection_proofs.get(connection), self.connection_owners.get(connection))
                        if self.shared:
                            self._apply_hook_record(self.broker.slots[self.broker.owners[caller.caller_id]])
                self.recovery.save()  # Record allocation/release before acknowledging it.
                # Even an unverified newcomer can shift verified agents when
                # grouped by project. Roll back the entire layout on failure.
                if stable and self.recovery.error:
                    result = self._rollback_recovery(checkpoint)
            result['slot_recovery'] = {'client_verified':connection in self.connection_proofs, **self.recovery.status()}
            if 'allocation' in result:
                slot = self.broker.slots.get(result['allocation']['slot_id'])
                if slot:
                    result['allocation']['navigation_target'] = self.broker.navigation_target(slot)
                result['allocation']['agent_connected'] = bool(slot and slot.agent_attached and
                    any(slot.caller.caller_id in owners for owners in self.connections.values()))
            return result
        if kind != "admin" or command["role"] != "admin":
            return {"status": "rejected", "reason": "unsupported_message"}
        action = message.get("action")
        if self.shared and action in ('register_project', 'remove_project'):
            if message.get('expected_epoch') != self.broker.epoch:
                raise ValueError('stale_backend_epoch')
            if action == 'register_project':
                registered = self.registry.enroll(message.get('path'))
            else:
                identity = message.get('project_id')
                if not isinstance(identity, str) or identity not in {p['project_id'] for p in self.registry.list()}:
                    raise ValueError('project_not_enabled')
                self.registry.remove(identity)
                registered = None
            self._refresh_projects()
            self.recovery.save()
            return {'status': 'accepted', 'backend_epoch': self.broker.epoch,
                    'project': registered, 'projects': self._project_snapshots(), 'shared': True}
        if action == 'set_project_muted':
            if message.get('expected_epoch') != self.broker.epoch:
                raise ValueError('stale_backend_epoch')
            self._refresh_projects()
            project = self.enrolled_projects.get(message.get('project_id')) if self.shared else None
            if self.shared and project is None or not self.shared and message.get('project_id') != self.project_id:
                raise ValueError('project_registration_mismatch')
            muted = message.get('muted')
            if type(muted) is not bool:
                raise ValueError('project_muted_must_be_boolean')
            try:
                policy = (ProjectPolicy(project['path'], self.state_dir / 'project-policies' / f"{project['project_id']}.json", state_dir=self.state_dir)
                          if project else self.project_policy)
                policy.save(muted)
            except (OSError, ValueError):
                return {'status': 'rejected', 'reason': 'project_policy_storage_unavailable',
                        'backend_epoch': self.broker.epoch}
            changed = self.broker.set_project_muted(muted, project['project_id'] if project else None)
            return {'status': 'accepted', 'backend_epoch': self.broker.epoch,
                    'changed': changed, 'project': project['path'] if project else self.project,
                    'projects': self._project_snapshots(),
                    'delivery': 'queued' if self.mode == 'hardware' else 'simulated'}
        if action == 'select_device':
            if message.get('expected_epoch') != self.broker.epoch:
                raise ValueError('stale_backend_epoch')
            select_device = getattr(self.driver, 'select_device', None)
            if self.mode != 'hardware' or not callable(select_device):
                return {'status': 'rejected', 'reason': 'device_selection_unavailable'}
            return select_device(message.get('fingerprint'), message.get('device_id'), message.get('profile_id'))
        if action in ('register_tasks', 'release_tasks'):
            if message.get('expected_epoch') != self.broker.epoch:
                raise ValueError('stale_backend_epoch')
            self._refresh_projects()
            project = self.enrolled_projects.get(message.get('project_id')) if self.shared else None
            if self.shared and project is None:
                raise ValueError('project_not_enabled')
            target_root = project['path'] if project else self.project
            key = text_argument(message.get('idempotency_key'), 'idempotency_key', 128)
            ids = requested_task_ids(message.get('thread_ids'), message.get('all_project', False))
            cache_key = (('host-admin', project['project_id'], project['generation'], key) if project else ('host-admin', key))
            fingerprint = json.dumps([action, sorted(ids) if ids is not None else None])
            cached = self.broker.idempotency.get(cache_key)
            if cached:
                if cached[0] != fingerprint:
                    raise ValueError('idempotency_conflict')
                return {**cached[1], 'replayed':True}
            # Validate the complete registration batch before touching any slot.
            tasks = codex_project_tasks(target_root, codex_home=self.codex_home, thread_ids=ids) if action == 'register_tasks' else None
            if self.shared and ids is not None:
                for task_id in ids:
                    owner = 'codex:' + task_id
                    existing = self.broker.slots.get(self.broker.owners.get(owner))
                    pending = self.recovery.pending.get(owner)
                    if (existing and existing.caller.project_id != project['project_id'] or
                            pending and pending['caller'].get('project_id') != project['project_id']):
                        raise ValueError('caller_project_mismatch')
            checkpoint = self._recovery_checkpoint()
            results = []
            if tasks is not None:
                for task in tasks:
                    slot, created = self.broker.register_host_task(task['thread_id'], task['label'],
                        project_id=project['project_id'] if project else None,
                        project_path=project['path'] if project else None,
                        project_generation=project['generation'] if project else None)
                    pending = self.recovery.pending.get(slot.caller.caller_id)
                    if pending:
                        self.recovery.proofs[slot.caller.caller_id] = {p['proof']:p['owner'] for p in pending['leases']}
                        self.recovery.forget_reservation(slot.caller.caller_id)
                    results.append({'thread_id': task['thread_id'], 'slot_id':slot.slot_id,
                                    **self.broker.location(slot),
                                    'identity_color':slot.identity_color, 'label':slot.label,
                                    'registration_source':'host' if slot.host_registered else 'agent',
                                    'agent_attached':slot.agent_attached, 'created':created})
            else:
                owners = (list(dict.fromkeys([*self.broker.owners, *self.recovery.pending])) if ids is None
                          else ['codex:' + task_id for task_id in ids])
                if self.shared and ids is None:
                    owners = [owner for owner in owners if
                        (self.broker.slots.get(self.broker.owners.get(owner)) and
                         self.broker.slots[self.broker.owners[owner]].caller.project_id == project['project_id']) or
                        self.recovery.pending.get(owner, {}).get('caller', {}).get('project_id') == project['project_id']]
                for owner in owners:
                    slot = self.broker.slots.get(self.broker.owners.get(owner))
                    pending = owner in self.recovery.pending
                    results.append({'thread_id':slot.caller.thread_id if slot else owner.removeprefix('codex:'),
                                    'slot_id':slot.slot_id if slot else None, 'released':bool(slot or pending)})
                    if slot:
                        self.broker._release(slot)
                    self.recovery.forget_reservation(owner)
                    self.recovery.proofs.pop(owner, None)
            self.recovery.save()
            if self.recovery.error:
                return self._rollback_recovery(checkpoint)
            result = {'status':'accepted', 'backend_epoch':self.broker.epoch,
                    'delivery':'queued' if self.mode == 'hardware' else 'simulated',
                    'tasks':results, 'allocated_slots':len(self.broker.slots),
                    'page_count':self.broker.page_count}
            self.broker.idempotency[cache_key] = (fingerprint, result)
            while len(self.broker.idempotency) > self.config.idempotency_capacity:
                self.broker.idempotency.popitem(last=False)
            return result
        if action == "set_background":
            self.broker.set_background_brightness(message.get("percent"))
            return {"status": "accepted", "background_brightness_percent": self.broker.config.background_brightness_percent,
                    "delivery": "queued" if self.mode == "hardware" else "simulated"}
        if action == "register_desktop":
            thread_id = str(UUID(message.get("thread_id", "")))
            self.desktop_threads.add(thread_id)
            return {"status": "accepted", "thread_id": thread_id, "surface_source": "registered_by_host"}
        if action == 'developer_probe':
            # Serve verified connection metadata without another HID handle or
            # even a new firmware query on the rendering/heartbeat worker.
            if (message.get('expected_epoch') != self.broker.epoch
                    or message.get('fingerprint') != getattr(self.driver, 'selected_device_fingerprint', None)
                    or message.get('fingerprint') is None):
                return {'status': 'rejected', 'reason': 'selected_device_changed'}
            from abralia.developer.probe import cached_driver_report
            capabilities = cached_driver_report(self.driver)
            if capabilities is None:
                return {'status': 'skipped', 'reason': 'verified_capabilities_unavailable'}
            return {'status': 'accepted', 'backend_epoch': self.broker.epoch, 'capabilities': capabilities}
        if action == "status":
            self._refresh_projects()
            result = self.broker.admin_snapshot()
            device_selection = self.mode == 'hardware' and callable(getattr(self.driver, 'select_device', None))
            result['admin_operations'] = ['register_tasks', 'release_tasks', 'set_project_muted']
            result['admin_operations'].append('developer_probe')
            if device_selection:
                result['admin_operations'].append('select_device')
            result['gui_capabilities'] = {'version': 1, 'project_mute': True,
                                          'device_selection': device_selection, 'project_registry': self.shared}
            result['shared'] = self.shared
            if self.shared:
                result['admin_operations'] += ['register_project', 'remove_project']
            result['project'] = self.project
            result['profile'] = str(self.profile)
            result['mode'] = self.mode
            result['projects'] = self._project_snapshots()
            result['selected_device_id'] = getattr(self.driver, 'selected_device_id', None)
            result['selected_device_fingerprint'] = getattr(self.driver, 'selected_device_fingerprint', None)
            result['codex_observer'] = {'enabled':self.observer is not None,
                                        'error':self.observer.error if self.observer else None}
            result['navigation_input'] = self.driver.navigation_input_status()
            result['slot_recovery'] = self.recovery.status()
            connected = set().union(*self.connections.values()) if self.connections else set()
            for item in result['slots']:
                item['agent_connected'] = item['agent_attached'] and item['caller_id'] in connected
            if self.driver.last_payload:
                result["rendered_keys"] = {key: color.to_json() for key, color in self.driver.last_payload.colors.items()}
            return {"status": "accepted", **result}
        if action == "seed_fixtures":
            count = message.get("count", 0)
            if type(count) is not int or not 1 <= count <= 120:
                raise ValueError("fixture count must be in 1...120")
            for i in range(count):
                caller = Caller(f"fixture:{i}", surface="simulated")
                r = self.broker.call(caller, "acquire_slot", {"label": f"SIMULATED agent {i + 1}", "idempotency_key": "fixture-acquire"})
                token = r["allocation"]["slot_token"]
                self.broker.call(caller, "set_slot_state", {"slot_token": token, "state": "progressing", "idempotency_key": "fixture-progress"})
            return {"status": "accepted", "fixtures": count}
        if action == "clear_fixtures":
            for slot in list(self.broker.slots.values()):
                if slot.caller.caller_id.startswith("fixture:"):
                    self.broker._release(slot)
            return {"status": "accepted"}
        if action == "input" and self.mode == "simulated":
            event = message.get("event")
            if event == "toggle":
                self.broker.set_active(not self.broker.active)
            elif event == 'navigation_hold':
                self.broker.toggle_navigation()
            elif event in ('page_previous', 'page_next', 'first_agent', 'last_agent', 'slot_previous', 'slot_next'):
                self.broker.navigate(event)
            elif event == 'confirm':
                candidate = self.broker.overview_candidate()
                if candidate:
                    self.broker.confirm_candidate(candidate.slot_token, self.broker.cursor_revision)
            elif event in ("next_page", "previous_page"):
                self.broker.turn_page(1 if event == "next_page" else -1)
            elif event == "select":
                position = message.get("position")
                if type(position) is not int or not 1 <= position <= 12:
                    raise ValueError("position must be in 1...12")
                slot = self.broker.slots.get(self.broker.page * 12 + position)
                if slot:
                    self.broker.select_slot(slot.slot_token)
            elif event in ("mute", "pickup"):
                slot = self.broker.pending_target()
                if slot:
                    self.broker.act_on_call(event, slot.slot_token, slot.notification.notification_id)
            else:
                raise ValueError("unknown simulated input")
            self.broker.step()
            return {"status": "accepted", "simulation": True}
        return {"status": "rejected", "reason": "unsupported_admin_action"}

    def _run_worker(self):
        last_sequence = 0
        try:
            if self.shared:
                self._refresh_projects()
            else:
                self.broker.set_project_muted(self.project_policy.load())
            self.driver = DeviceDriver(self.broker, self.profile, self.mode, gap_committer=self._commit_gap,
                                       sort_committer=self._commit_sort)
            if self.mode == 'hardware':
                from .gui_devices import saved_device_selection
                selected = saved_device_selection(self.state_dir)
                if (selected and selected['profile_id'].removeprefix('builtin:')
                        == self.driver.profile.device_profile.profile_id.removeprefix('builtin:')):
                    self.driver.selected_device_id = selected['id']
                    self.driver.selected_device_fingerprint = selected['fingerprint']
            self.driver.start()
            self.started.set_result(True)
            while not self.stop_event.is_set():
                if self.listener_ready.is_set() and not self.recovery.window_started:
                    self.recovery.begin_window()
                # Bound command work between HID service calls; no socket handler touches HID.
                for _ in range(16):
                    try:
                        command, future = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        result = self._handle(command)
                    except (ValueError, TypeError, KeyError) as error:
                        result = {"status": "rejected", "reason": str(error)}
                    future.set_result(result)
                now = time.monotonic()
                if self.shared and now >= self.next_registry_refresh:
                    self._refresh_projects()
                for connection, seen in list(self.last_seen.items()):
                    if now - seen > 20:
                        self._drop_connection(connection)
                self.broker.step()
                self.recovery.expire()
                self.recovery.save()
                self.driver.tick()
                for event in self.broker.events:
                    if event["sequence"] > last_sequence:
                        # Event records exclude tokens, summaries and question text.
                        LOG.info("%s", json.dumps(event))
                        last_sequence = event["sequence"]
                self.stop_event.wait(.003)
        except Exception as error:
            self.worker_error = str(error)
            self.broker.delivery = "failed"
            self.broker.device_error = self.worker_error
            if not self.started.done():
                self.started.set_exception(error)
            else:
                LOG.exception("Backend worker stopped")
            self.stop_event.set()
        finally:
            if self.driver:
                try:
                    self.driver.close()
                except Exception as error:
                    self.cleanup_error = str(error)
                    LOG.error("Device cleanup failed: %s", error)
            while not self.commands.empty():
                _, future = self.commands.get_nowait()
                if not future.done():
                    future.set_result({"status": "skipped", "reason": "backend_stopped"})

    def start(self):
        parent = self.endpoint.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
            raise ValueError("socket directory must be private and owned by the current user")
        if len(os.fsencode(self.endpoint)) >= 104:
            raise ValueError("Unix socket path is too long for macOS")
        lock_path = self.endpoint.with_suffix(".lock")
        self.lock_file = os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.endpoint.exists() or self.endpoint.is_symlink():
                info = self.endpoint.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError("refusing to remove an unexpected socket-path entry")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(.2)
                    try:
                        probe.connect(str(self.endpoint))
                    except ConnectionRefusedError:
                        self.endpoint.unlink()
                    else:
                        raise RuntimeError("backend already running")
            self.recovery.load()
            self.worker = threading.Thread(target=self._run_worker, name="abralia-device-worker")
            self.worker.start()
            self.started.result(timeout=15)
            service = self

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    connection = str(uuid4())
                    role, fallback = "agent", None
                    self.request.settimeout(20)
                    try:
                        line = self.rfile.readline(MAX_REQUEST + 1)
                        hello = json.loads(line)
                        if (len(line) > MAX_REQUEST or not isinstance(hello, dict) or hello.get("type") != "hello"
                                or not service.shared and hello.get("project") != service.project):
                            raise ValueError("project_registration_mismatch")
                        role = hello.get("role", "agent")
                        if role not in (('agent', 'admin', 'hook') if service.shared else ('agent', 'admin')):
                            raise ValueError("invalid_connection_role")
                        fallback = hello.get("registered_thread_id")
                        if fallback is not None and fallback != service.registered_thread_id:
                            raise ValueError("unregistered_test_connection")
                        if role == 'agent' and hello.get('terminal_context') is None and hello.get('recovery_owner'):
                            # Native process discovery can block. Keep it on the
                            # socket handler, never the HID/heartbeat worker.
                            from .client_lifetime import capture_terminal_context
                            hello['terminal_context'] = capture_terminal_context(hello['recovery_owner'], environment={})
                        owner_verified = (role == 'agent' and hello.get('recovery_key') is not None
                                          and service.recovery.owner_check(hello.get('recovery_owner')))
                        source_cache = {}
                        def native_sources(thread_ids):
                            from .codex_context import native_task_source
                            result = {}
                            for thread_id in thread_ids:
                                try:
                                    thread_id = str(UUID(thread_id))
                                    source = source_cache.get(thread_id)
                                    if source is None:
                                        source = native_task_source(thread_id, codex_home=service.codex_home)
                                    if source != 'unknown':
                                        source_cache[thread_id] = result[thread_id] = source
                                except (ValueError, OSError, TypeError, AttributeError):
                                    pass
                            return result
                        claims = hello.get('resume_callers')
                        sources = native_sources([c[6:] for c in claims if isinstance(c, str) and c.startswith('codex:')]) if (
                            owner_verified and isinstance(claims, list) and len(claims) <= MAX_RECORDS) else {}
                        accepted = service.submit({'connection':connection, 'role':role,
                                                   'message':hello, 'handshake':True,
                                                   'owner_verified': bool(owner_verified), 'terminal_sources': sources})
                        self.wfile.write(json.dumps(accepted).encode() + b"\n")
                        self.wfile.flush()
                        if accepted.get('status') != 'accepted':
                            return
                        while not service.stop_event.is_set():
                            line = self.rfile.readline(MAX_REQUEST + 1)
                            if not line:
                                break
                            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                                raise ValueError("request_too_large")
                            message = json.loads(line)
                            if not isinstance(message, dict):
                                raise ValueError("message_must_be_object")
                            metadata = message.get('metadata')
                            sources = native_sources([metadata.get('thread_id')]) if (
                                owner_verified and message.get('type') == 'call' and isinstance(metadata, dict)) else {}
                            result = service.submit({"connection": connection, "role": role,
                                                     "fallback": fallback, "message": message,
                                                     'terminal_sources': sources})
                            self.wfile.write(json.dumps(result, allow_nan=False).encode() + b"\n")
                            self.wfile.flush()
                    except (ValueError, OSError) as error:
                        try:
                            self.wfile.write(json.dumps({"status": "rejected", "reason": str(error)}).encode() + b"\n")
                        except OSError:
                            pass
                    finally:
                        service.submit({"connection": connection, "message": {"type": "disconnect"}})

            class Server(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True
                block_on_close = False
                request_queue_size = 32

                def __init__(self, *args):
                    self.connection_limit = threading.BoundedSemaphore(64)
                    super().__init__(*args)

                def process_request(self, request, client_address):
                    if not self.connection_limit.acquire(blocking=False):
                        self.shutdown_request(request)
                        return
                    try:
                        super().process_request(request, client_address)
                    except Exception:
                        self.connection_limit.release()
                        raise

                def process_request_thread(self, request, client_address):
                    try:
                        super().process_request_thread(request, client_address)
                    finally:
                        self.connection_limit.release()

            self.server = Server(str(self.endpoint), Handler)
            os.chmod(self.endpoint, 0o600)
            self.listener = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
            self.listener.start()
            self.listener_ready.set()
            self._publish_info()
            if self.observer:
                self.observer.start()
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        self.stop_event.set()
        observer_error = None
        if self.observer:
            try:
                self.observer.close()
            except Exception as error:
                observer_error = error
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.endpoint.unlink(missing_ok=True)
        if self.worker:
            self.worker.join(timeout=12)
            if self.worker.is_alive():
                raise RuntimeError("device worker did not stop; cleanup remains unverified")
        self._remove_info()
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None
        if observer_error:
            raise observer_error

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
