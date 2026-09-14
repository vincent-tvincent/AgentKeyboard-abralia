# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Private allocation records. Live bridge proof is required before display."""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from uuid import UUID

from .core import Allocation, Caller, HARNESSES
from .client_lifetime import owner_alive

MAX_RECORDS = 1024
MAX_BYTES = 4 * 1024 * 1024


def proof_digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid_recovery_proof')
    return hashlib.blake2s(value.encode()).hexdigest()


class AllocationRecovery:
    def __init__(self, broker, project, path, *, grace_seconds=30, owner_check=None,
                 shared=False, project_validator=None):
        self.broker = broker
        self.project = str(Path(project).resolve())
        self.path = Path(path)
        self.grace_seconds = grace_seconds
        self.pending = {}
        self.proofs = {}
        self.deadline = None
        self.window_started = False
        self.error = None
        self.saved = None
        self.restored = 0
        self.owner_check = owner_check or owner_alive
        self.shared = shared
        self.project_validator = project_validator

    def _validate(self, data):
        if not isinstance(data, dict):
            raise ValueError('invalid_recovery_document')
        version = data.get('version')
        valid_versions = (3,) if self.shared else (1, 2)
        if type(version) is not int or version not in valid_versions or data.get('project') != self.project:
            raise ValueError('recovery_project_or_version_mismatch')
        records = data.get('allocations')
        if not isinstance(records, list) or len(records) > MAX_RECORDS:
            raise ValueError('invalid_recovery_allocations')
        slots, positions, owners, colors, proof_owners = set(), set(), set(), set(), {}
        for record in records:
            if type(record.get('host_registered', False)) is not bool:
                raise ValueError('invalid_recovery_registration_source')
            caller = Caller(**record['caller'])
            if self.shared and (not caller.project_id or not caller.project_path):
                raise ValueError('recovery_caller_project_required')
            if (not caller.thread_id or caller.caller_id != 'codex:' + str(UUID(caller.thread_id))
                    or caller.surface not in HARNESSES):
                raise ValueError('invalid_recovery_caller')
            number = record['slot_id']
            if type(number) is not int or not 1 <= number <= 1000000 or number in slots or caller.caller_id in owners:
                raise ValueError('duplicate_or_invalid_recovery_slot')
            position = record.get('display_position', number if version == 1 else None)
            if type(position) is not int or not 1 <= position <= 1000000 or position in positions:
                raise ValueError('duplicate_or_invalid_recovery_position')
            record['display_position'] = position
            color = record['identity_color']
            if not isinstance(color, str) or len(color) != 6 or any(c not in '0123456789ABCDEF' for c in color) or color in colors:
                raise ValueError('duplicate_or_invalid_recovery_color')
            label = record['label']
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError('invalid_recovery_label')
            proofs = record['leases']
            if not isinstance(proofs, list) or not 1 <= len(proofs) <= 128:
                raise ValueError('invalid_recovery_proofs')
            for lease in proofs:
                proof = lease['proof']
                if not isinstance(proof, str) or len(proof) != 64 or any(c not in '0123456789abcdef' for c in proof):
                    raise ValueError('invalid_recovery_proofs')
                owner = lease['owner']
                if (not isinstance(owner, dict) or owner.get('kind') not in ('codex_gui','codex_tui','codex_editor')
                        or type(owner.get('pid')) is not int or owner['pid'] <= 1
                        or any(not isinstance(owner.get(k), str) or len(owner[k]) > 1024
                               for k in ('started','executable','tty'))):
                    raise ValueError('invalid_recovery_owner')
                if proof in proof_owners and proof_owners[proof] != owner:
                    raise ValueError('inconsistent_recovery_owner')
                proof_owners[proof] = owner
            slots.add(number); positions.add(position); owners.add(caller.caller_id); colors.add(color)
        return [r for r in records if self.project_validator is None or self.project_validator(Caller(**r['caller']))]

    def load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            self.saved = self._encode([])
            return
        except OSError:
            self.error = 'recovery_state_unreadable'
            return
        try:
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
                    raise ValueError('unsafe_recovery_file')
                data = json.load(stream)
                records = self._validate(data)
            self.pending = {r['caller']['caller_id']: r for r in records}
            self.broker.reserved_slots = {r['slot_id'] for r in records}
            self.broker.reserved_slot_owners = {r['slot_id']:r['caller']['caller_id'] for r in records}
            self.broker.reserved_positions = {r['display_position']:r['caller']['caller_id'] for r in records}
            self.broker.identity_colors.update({r['caller']['caller_id']:r['identity_color'] for r in records})
            self.saved = self._encode(records) if data['version'] == 2 and not self.shared else None
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            self.error = 'invalid_recovery_state'

    def _encode(self, records):
        return json.dumps({'version':3 if self.shared else 2, 'project':self.project,
                           'allocations':sorted(records, key=lambda r:r['slot_id'])}, sort_keys=True, separators=(',', ':'))

    def attach(self, caller_id, proof, owner):
        if proof:
            existing = self.proofs.setdefault(caller_id, {})
            if proof not in existing and len(existing) >= 128:
                existing.pop(next(iter(existing)))
            existing[proof] = owner

    def forget_reservation(self, caller_id):
        record = self.pending.pop(caller_id, None)
        if record:
            self.broker.reserved_slots.discard(record['slot_id'])
            self.broker.reserved_slot_owners.pop(record['slot_id'], None)
            self.broker.reserved_positions.pop(record['display_position'], None)

    def resume(self, caller_ids, proof, *, project_id=None, validated_owner=None):
        result = {}
        checked = {}
        def alive(owner):
            if validated_owner is not None:
                return owner == validated_owner
            key = json.dumps(owner, sort_keys=True)
            if key not in checked:
                checked[key] = self.owner_check(owner)
            return checked[key]
        self.expire()
        for caller_id in caller_ids:
            slot = self.broker.slots.get(self.broker.owners.get(caller_id))
            if slot and self.shared and slot.caller.project_id != project_id:
                continue
            if slot and proof in self.proofs.get(caller_id, {}):
                if alive(self.proofs[caller_id][proof]):
                    slot.agent_attached = True
                    result[caller_id] = slot.slot_token
                    continue
            record = self.pending.get(caller_id)
            if record and self.shared and record['caller'].get('project_id') != project_id:
                continue
            lease = next((p for p in record['leases'] if secrets.compare_digest(proof, p['proof'])), None) if record else None
            if lease is None or not alive(lease['owner']):
                continue
            number = record['slot_id']
            position = record['display_position']
            if (number in self.broker.slots or caller_id in self.broker.owners
                    or self.broker.slot_at_position(position) is not None):
                continue
            slot = Allocation(number, secrets.token_hex(24), Caller(**record['caller']),
                              record['label'], identity_color=record['identity_color'],
                              host_registered=record.get('host_registered', False), display_position=position)
            self.broker.slots[number] = slot
            self.broker.owners[caller_id] = number
            self.broker._layout_changed()
            self.proofs[caller_id] = {p['proof']:p['owner'] for p in record['leases']}
            self.forget_reservation(caller_id)
            self.broker.connected(caller_id)
            self.broker.event('slot_restored', slot)
            self.restored += 1
            result[caller_id] = slot.slot_token
        return result

    def expire(self):
        if self.deadline is not None and self.broker.clock() >= self.deadline:
            changed = bool(self.pending or self.broker.reserved_positions or self.broker.reserved_slots)
            self.pending.clear()
            self.broker.reserved_slots.clear()
            self.broker.reserved_slot_owners.clear()
            self.broker.reserved_positions.clear()
            if changed:
                self.broker._layout_changed()
            self.deadline = None

    def begin_window(self):
        self.window_started = True
        self.deadline = self.broker.clock() + self.grace_seconds if self.pending else None

    def save(self):
        if self.error:
            return
        records = list(self.pending.values())
        active = set()
        for slot in self.broker.slots.values():
            proofs = self.proofs.get(slot.caller.caller_id)
            if not proofs or not slot.caller.caller_id.startswith('codex:'):
                continue
            active.add(slot.caller.caller_id)
            records.append({'slot_id':slot.slot_id, 'caller':asdict(slot.caller), 'label':slot.label,
                            'display_position':slot.position,
                            'identity_color':slot.identity_color,
                            'host_registered':slot.host_registered,
                            'leases':[{'proof':p,'owner':owner} for p,owner in sorted(proofs.items())]})
        encoded = self._encode(records)
        if encoded == self.saved:
            return
        temporary = None
        try:
            if len(records) > MAX_RECORDS or len(encoded.encode()) > MAX_BYTES:
                raise ValueError('recovery_capacity_exceeded')
            if self.path.is_symlink():
                raise ValueError('unsafe_recovery_file')
            fd, temporary = tempfile.mkstemp(prefix='.abralia-slots-', dir=self.path.parent)
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.saved = encoded
            self.proofs = {owner:proofs for owner,proofs in self.proofs.items() if owner in active}
        except (OSError, ValueError):
            self.error = 'recovery_state_write_failed'
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def status(self):
        return {'pending_clients':len(self.pending), 'restored_slots':self.restored, 'error':self.error}
