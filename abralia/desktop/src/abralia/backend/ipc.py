# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Private local transport shared by the terminal client and STDIO bridge."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import select
import threading
import secrets
import time
from uuid import UUID
from .client_lifetime import capture_client_owner, capture_terminal_context

MAX_REQUEST = 65536
MAX_RESPONSE = 4 * 1024 * 1024


class JsonLineReader:
    """Bound incomplete frames without timing out a quiet established stream."""

    def __init__(self, connection, stop, *, frame_timeout=20):
        self.connection = connection
        self.stop = stop
        self.frame_timeout = frame_timeout
        self.buffer = bytearray()

    def readline(self, *, idle_timeout=None):
        idle_deadline = time.monotonic() + idle_timeout if idle_timeout is not None else None
        frame_deadline = None
        while not self.stop.is_set():
            end = self.buffer.find(b'\n')
            if end >= 0:
                if end + 1 > MAX_REQUEST:
                    raise ValueError('request_too_large')
                line = bytes(self.buffer[:end + 1])
                del self.buffer[:end + 1]
                return line
            if len(self.buffer) >= MAX_REQUEST:
                raise ValueError('request_too_large')
            now = time.monotonic()
            if self.buffer and frame_deadline is None:
                frame_deadline = now + self.frame_timeout
            deadline = frame_deadline if frame_deadline is not None else idle_deadline
            if deadline is not None and now >= deadline:
                raise TimeoutError('request_read_timeout')
            delay = min(.25, max(0, deadline - now)) if deadline is not None else .25
            ready, _, _ = select.select([self.connection], [], [], delay)
            if not ready:
                continue
            data = self.connection.recv(MAX_REQUEST + 1 - len(self.buffer))
            if not data:
                if self.buffer:
                    raise ValueError('incomplete_request')
                return b''
            self.buffer.extend(data)
        return b''


def shared_socket_path(runtime_dir: str | Path | None = None) -> Path:
    """The app's one keyboard broker; independent of project enrollment."""
    root = runtime_dir or os.environ.get('ABRALIA_RUNTIME_DIR') or f'/tmp/abralia-{os.getuid()}'
    return Path(root) / 'shared.sock'


def socket_path(project: str | Path) -> Path:
    # macOS sockaddr_un paths are short; do not place the socket in a long checkout path.
    slug = hashlib.blake2s(os.fsencode(Path(project).resolve()), digest_size=8).hexdigest()
    return Path(f"/tmp/abralia-{os.getuid()}") / f"{slug}.sock"


class BrokerClient:
    def __init__(self, project: str | Path, *, endpoint: str | Path | None = None,
                 role: str = "agent", registered_thread_id: str | None = None, recover_slots: bool = False,
                 recovery_owner=None, timeout: float = 8, project_generation: str | None = None):
        self.project = str(Path(project).resolve())
        self.endpoint = str(endpoint or socket_path(project))
        self.role = role
        self.timeout = timeout
        self.project_generation = project_generation
        self.registered_thread_id = registered_thread_id
        self.lock = threading.Lock()
        self.socket = None
        self.reader = None
        self.epoch = None
        self.recovery_key = secrets.token_hex(32) if recover_slots and role == 'agent' else None
        self.recovery_owner = (recovery_owner if recovery_owner is not None else capture_client_owner()) if self.recovery_key else None
        self.terminal_context = capture_terminal_context(self.recovery_owner) if self.recovery_owner else None
        self.resume_claims = {}
        self.pending_releases = {}
        self.pending_acquires = set()

    def _connect(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        self.socket.connect(self.endpoint)
        self.reader = self.socket.makefile("rb")
        hello = {"type": "hello", "project": self.project, "role": self.role,
                 "registered_thread_id": self.registered_thread_id}
        if self.project_generation is not None:
            hello['project_generation'] = self.project_generation
        if self.recovery_key:
            hello.update(recovery_key=self.recovery_key, recovery_owner=self.recovery_owner,
                         resume_callers=sorted((set(self.resume_claims) | self.pending_acquires) - set(self.pending_releases)))
            if self.terminal_context is not None:
                hello['terminal_context'] = self.terminal_context
        response = self._exchange(hello)
        if response.get("status") != "accepted":
            raise OSError(response.get("reason", "registration rejected"))
        self.epoch = response["backend_epoch"]
        if 'restored_tokens' in response:
            self.resume_claims = dict(response['restored_tokens'])
            self.pending_acquires.difference_update(self.resume_claims)

    def _message_caller(self, message):
        metadata = message.get('metadata') or {}
        if not isinstance(metadata, dict):
            return None
        try:
            return 'codex:' + str(UUID(metadata.get('thread_id') or self.registered_thread_id))
        except (TypeError, ValueError, AttributeError):
            return None

    def _remember(self, message, response):
        if not self.recovery_key or message.get('type') != 'call':
            return
        caller = self._message_caller(message)
        if caller is None:
            return
        if message.get('operation') == 'acquire_slot' and response.get('status') == 'rejected':
            self.pending_acquires.discard(caller)
        if response.get('status') != 'accepted':
            return
        allocation = response.get('allocation')
        if message.get('operation') == 'acquire_slot':
            self.pending_releases.pop(caller, None)
        if (isinstance(allocation, dict) and isinstance(allocation.get('slot_token'), str)
                and caller not in self.pending_releases
                and (message.get('operation') == 'acquire_slot' or caller in self.resume_claims or caller in self.pending_acquires)):
            self.resume_claims[caller] = allocation['slot_token']
            self.pending_acquires.discard(caller)
        if message.get('operation') == 'release_slot':
            token = (message.get('arguments') or {}).get('slot_token')
            if self.resume_claims.get(caller) == token:
                self.resume_claims.pop(caller, None)
            self.pending_releases.pop(caller, None)

    def _exchange(self, message: dict) -> dict:
        encoded = json.dumps(message, allow_nan=False).encode() + b"\n"
        if len(encoded) > MAX_REQUEST:
            raise ValueError("request_too_large")
        self.socket.sendall(encoded)
        line = self.reader.readline(MAX_RESPONSE + 1)
        if not line or len(line) > MAX_RESPONSE or not line.endswith(b"\n"):
            raise OSError("backend disconnected or sent an invalid response")
        return json.loads(line)

    def request(self, message: dict) -> dict:
        with self.lock:
            caller = self._message_caller(message) if message.get('type') == 'call' else None
            if self.recovery_key and caller is not None:
                if message.get('operation') == 'acquire_slot':
                    self.pending_acquires.add(caller)
                args = message.get('arguments')
                if message.get('operation') == 'release_slot' and isinstance(args, dict):
                    token = args.get('slot_token')
                    if token and self.resume_claims.get(caller) == token:
                        self.pending_releases[caller] = token
                        self.resume_claims.pop(caller)
            try:
                if self.socket is None:
                    self._connect()
                if self.role == 'admin' and message.get('action') in ('register_tasks', 'release_tasks'):
                    # Retain the original epoch on the caller's request object,
                    # so a retry cannot release a fresh backend's allocations.
                    message.setdefault('expected_epoch', self.epoch)
                response = self._exchange(message)
                self._remember(message, response)
                if response.get('reason') in ('backend_stopping', 'backend_stopped', 'connection_expired'):
                    self._close()
                return response
            except (OSError, ValueError) as error:
                self._close()
                return {"status": "skipped", "reason": "backend_unavailable", "detail": str(error)}

    def _close(self):
        if self.reader:
            self.reader.close()
            self.reader = None
        if self.socket:
            self.socket.close()
            self.socket = None

    def close(self):
        with self.lock:
            self._close()
            self.resume_claims.clear()
            self.pending_releases.clear()
            self.pending_acquires.clear()
            if self.recovery_key:
                self.recovery_key = secrets.token_hex(32)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
