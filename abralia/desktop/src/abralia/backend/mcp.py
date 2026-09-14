# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""STDIO MCP adapter. No HID imports or physical input synthesis in this process."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import json
from pathlib import Path
import threading
from typing import Any, Literal
from uuid import UUID

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from .ipc import BrokerClient, shared_socket_path
from .codex_context import native_task_cwd
from .project_registry import ProjectRegistry
from .project_policy import project_identity

INSTRUCTIONS = (
    "Abralia connects enabled projects to the app's shared agent keyboard. Read the abralia skill. "
    "Enable or register with Abralia only after the user explicitly asks to use it for this task or current project. "
    "Use enable_self when enrollment is needed, or acquire_slot for an enabled project. "
    "Never enable or register proactively or to repair a skipped lighting call. "
    "Report your harness as codex_desktop, codex_cli, or unknown. "
    "Terminal/editor pickup uses verified native process context when available; no model-supplied focus targets. "
    "A window-only focus result is not proof a particular pane or native question is visible. "
    "Task identity comes from the harness; no user registration command is needed. "
    "Keep your slot token; update status and request attention only when useful. "
    "Inspect get_status for a healthy codex_observer before asking a native question. "
    "When available, it handles question attention automatically; do not send a duplicate report. "
    "Otherwise follow the skill's explicit question-report fallback and clear it after the answer. "
    "Pickup opens a task, not a verified answer-key mapping. Mute/pickup release both call controls. "
    "Missing service is a normal skipped result; continue work. "
    "Custom notification animations are optional: if no idea comes immediately, omit animation and use the default. "
    "Do not spend time brainstorming, researching or iterating on an effect. "
    "show_keyboard_frame requests a static keymap guide after pickup; Escape closes it in Agent Mode. "
    "Use stable idempotency keys for retries. Do not release immediately after requesting an unread completion call."
)


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str


def codex_metadata(meta: Any) -> dict:
    if meta is None:
        return {}
    data = meta.model_dump(by_alias=True, exclude_none=True) if hasattr(meta, "model_dump") else meta
    if not isinstance(data, dict):
        return {}
    value = data.get("x-codex-turn-metadata", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict) or not value.get("thread_id"):
        return {}
    try:
        return {"thread_id": str(UUID(value["thread_id"]))}
    except (ValueError, TypeError, AttributeError):
        return {}


class Bridge:
    def __init__(self, project: str | None = None, endpoint=None, registered_thread_id=None,
                 *, registry=None, codex_home=None):
        self.client = (BrokerClient(project, endpoint=endpoint, registered_thread_id=registered_thread_id,
                                    recover_slots=True) if project is not None else None)
        self.clients = {}
        self.client_generations = {}
        self.enable_requests = {}  # This bridge lifetime; not a persisted permission receipt.
        self.clients_lock = threading.Lock()
        self.endpoint = endpoint or shared_socket_path()
        self.registry = registry or (ProjectRegistry() if project is None else None)
        self.codex_home = codex_home
        self.stop = threading.Event()
        self.heartbeat = None

    def start(self):
        def beat():
            while not self.stop.wait(5):
                # A surviving bridge can resume only its previously owned callers.
                # Retry the read-only ping once after a dead backend socket closes.
                with self.clients_lock:
                    clients = [self.client] if self.client else list(self.clients.values())
                for client in clients:
                    if self.stop.is_set():
                        break
                    with self.clients_lock:
                        if client is not self.client and client not in self.clients.values():
                            continue
                        result = client.request({"type": "ping"})
                        if result.get('reason') in ('backend_unavailable', 'backend_stopping', 'backend_stopped'):
                            client.request({"type": "ping"})
        self.heartbeat = threading.Thread(target=beat, name="abralia-bridge-heartbeat", daemon=True)
        self.heartbeat.start()

    def close(self):
        self.stop.set()
        if self.heartbeat:
            self.heartbeat.join(timeout=9)
        with self.clients_lock:
            for client in ([self.client] if self.client else list(self.clients.values())):
                client.close()
            self.clients.clear()
            self.client_generations.clear()
            self.enable_requests.clear()

    def call(self, meta, operation: str, arguments: dict) -> dict:
        enabling = operation == 'enable_self'
        if enabling:
            if not isinstance(arguments, dict) or set(arguments) - {'label', 'harness', 'idempotency_key'}:
                return {'status': 'rejected', 'reason': 'unexpected_argument'}
            for name, maximum in (('label', 80), ('idempotency_key', 128)):
                value = arguments.get(name)
                if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                    return {'status': 'rejected', 'reason': f'{name} must be nonempty text of at most {maximum} characters'}
            if arguments.get('harness') not in (None, 'codex_desktop', 'codex_cli', 'unknown'):
                return {'status': 'rejected', 'reason': 'unsupported_harness'}
        metadata = codex_metadata(meta)
        if not metadata and not (self.client and self.client.registered_thread_id):
            return {"status": "skipped", "reason": "caller_identity_unavailable",
                    "hint": "This client supplied no usable Codex thread metadata. A maintainer may configure an explicit test connection."}
        client = self.client
        project_created = False
        project = None
        cwd = None
        if client is None:
            try:
                cwd = native_task_cwd(metadata['thread_id'], codex_home=self.codex_home)
                project = self.registry.resolve(cwd)
            except (OSError, ValueError, TypeError):
                return {'status': 'skipped', 'reason': 'native_project_unavailable',
                        'hint': 'Abralia could not verify this native task workspace; continue your main task.'}
            if enabling:
                key = (metadata['thread_id'], arguments['idempotency_key'])
                fingerprint = json.dumps(arguments, sort_keys=True)
                with self.clients_lock:
                    try:
                        # An explicit enable names the native workspace itself.
                        # An enrolled ancestor (especially a home directory)
                        # must not absorb a different project's activation.
                        project = self.registry.resolve_exact(cwd)
                    except (OSError, ValueError, TypeError):
                        return {'status': 'skipped', 'reason': 'project_registry_unavailable', 'slot_acquired': False}
                    previous = self.enable_requests.get(key)
                    if previous:
                        if previous['fingerprint'] != fingerprint:
                            return {'status': 'rejected', 'reason': 'idempotency_conflict'}
                        if project is None or (project['project_id'], project.get('generation')) != previous['project']:
                            return {'status': 'rejected', 'reason': 'stale_enable_request',
                                    'project_enabled': project is not None, 'slot_acquired': False,
                                    'hint': 'The enrollment changed after this request. A new explicit user request requires a new key.'}
                        project_created = previous['created']
                    else:
                        if len(self.enable_requests) >= 4096:
                            return {'status': 'skipped', 'reason': 'enable_request_capacity', 'slot_acquired': False}
                        if project is None:
                            try:
                                project = self.registry.enroll(cwd)
                                project_created = True
                            except (OSError, ValueError, TypeError):
                                return {'status': 'skipped', 'reason': 'project_enable_failed',
                                        'project_enabled': False, 'slot_acquired': False}
                        self.enable_requests[key] = {'fingerprint': fingerprint, 'created': project_created,
                            'project': (project['project_id'], project.get('generation'))}
            try:
                enabled = {row['project_id']: row.get('generation') for row in self.registry.list()}
            except (OSError, ValueError, TypeError):
                return {'status': 'skipped', 'reason': 'project_registry_unavailable',
                        **(self._enable_result(project, project_created, False) if enabling else {})}
            with self.clients_lock:
                for identity, existing in list(self.clients.items()):
                    if identity not in enabled or self.client_generations.get(identity) != enabled[identity]:
                        existing.close()
                        del self.clients[identity]
                        self.client_generations.pop(identity, None)
            if project is None:
                return {'status': 'skipped', 'reason': 'project_not_enabled',
                        'hint': 'This project is not enabled in the Abralia app; continue your main task.'}
            metadata['cwd'] = str(cwd)
            with self.clients_lock:
                client = self.clients.get(project['project_id'])
                generation = project.get('generation')
                if client is not None and self.client_generations.get(project['project_id']) != generation:
                    # Disabling then re-enabling a project must not reuse its
                    # previous connection proof or resurrect the old slots.
                    client.close()
                    del self.clients[project['project_id']]
                    client = None
                if client is None:
                    if len(self.clients) >= 64:
                        return {'status': 'skipped', 'reason': 'project_connection_limit',
                                **(self._enable_result(project, project_created, False) if enabling else {})}
                    client = BrokerClient(project['path'], endpoint=self.endpoint,
                                          recover_slots=True, timeout=2, project_generation=generation)
                    self.clients[project['project_id']] = client
                    self.client_generations[project['project_id']] = generation
        result = client.request({"type": "call", "metadata": metadata,
                                 "operation": 'acquire_slot' if enabling else operation, "arguments": arguments})
        if cwd is not None:
            result = {**result, 'project_context': {
                'native_workspace': str(cwd), 'enrolled_root': project['path'],
                'match': 'exact' if str(cwd) == project['path'] else 'ancestor'}}
        if enabling:
            if project is None:
                project = {'project_id': project_identity(client.project), 'path': client.project,
                           'name': Path(client.project).name, 'scope': 'legacy_project_local'}
            result = {**result, **self._enable_result(project, project_created,
                       result.get('status') == 'accepted' and isinstance(result.get('allocation'), dict))}
        return result

    @staticmethod
    def _enable_result(project, created, acquired):
        return {'enabled_project': {key: project[key] for key in ('project_id', 'path', 'name', 'scope') if key in project},
                'project_enabled': True, 'project_created': created, 'slot_acquired': acquired}


def create_server(bridge: Bridge) -> MCPServer:
    @asynccontextmanager
    async def lifespan(server):
        bridge.start()
        try:
            yield {}
        finally:
            await anyio.to_thread.run_sync(bridge.close)

    server = MCPServer("abralia", instructions=INSTRUCTIONS, version="0.3.0",
                       lifespan=lifespan, log_level="WARNING")
    mutation = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    async def call(ctx: Context, operation: str, arguments: dict):
        return await anyio.to_thread.run_sync(bridge.call, ctx.request_context.meta, operation, arguments)

    @server.tool(annotations=mutation)
    async def enable_self(label: str, idempotency_key: str, ctx: Context,
                          harness: Literal['codex_desktop', 'codex_cli', 'unknown'] | None = None) -> dict[str, Any]:
        """Only after the user explicitly asks to enable Abralia here: enable your verified native current project and claim your own slot. Never call proactively, for another project/task, or to repair skipped lighting. No project path or identity argument exists. Does not start the app or activate the keyboard. If the backend is unavailable, project_enabled can be true while slot_acquired is false. Legacy project-local connections keep their existing configuration."""
        arguments = {'label': label, 'idempotency_key': idempotency_key}
        if harness is not None:
            arguments['harness'] = harness
        return await call(ctx, 'enable_self', arguments)

    @server.tool(annotations=mutation)
    async def acquire_slot(label: str, idempotency_key: str, ctx: Context,
                           harness: Literal["codex_desktop", "codex_cli", "unknown"] | None = None) -> dict[str, Any]:
        """Only after an explicit user request to use Abralia: register yourself and claim your stable slot in an enabled project. Report your actual harness: codex_desktop for the Codex app, codex_cli for a terminal, unknown otherwise. Verified native context may enable terminal/editor window pickup regardless of the reported harness. Task identity and focus targets never come from model-supplied IDs. Window focus is not proof of pane/question visibility. Returns ownership token, page, F-key and registration provenance."""
        arguments = {"label": label, "idempotency_key": idempotency_key}
        if harness is not None:
            arguments["harness"] = harness
        return await call(ctx, "acquire_slot", arguments)

    @server.tool(annotations=mutation)
    async def release_slot(slot_token: str, idempotency_key: str, ctx: Context) -> dict[str, Any]:
        """Release your slot and its calls/questions. Do not release an unread notification you want retained."""
        return await call(ctx, "release_slot", {"slot_token": slot_token, "idempotency_key": idempotency_key})

    @server.tool(annotations=mutation)
    async def set_slot_state(slot_token: str, state: Literal["idle", "progressing", "error", "action_requested", "completed"],
                             idempotency_key: str, ctx: Context, summary: str | None = None,
                             progress: float | None = None) -> dict[str, Any]:
        """Report your semantic status without automatically notifying. Progress is optional and must be meaningful."""
        return await call(ctx, "set_slot_state", {"slot_token": slot_token, "state": state,
            "summary": summary, "progress": progress, "idempotency_key": idempotency_key})

    @server.tool(annotations=mutation)
    async def set_notification(slot_token: str, enabled: bool, idempotency_key: str,
                               ctx: Context, summary: str | None = None,
                               animation: dict[str, Any] | Literal['default'] | None = None) -> dict[str, Any]:
        """Request/withdraw your call. Omit animation unless a useful idea is already clear; do not spend time designing it. Optional description: duration_ms (1000..4000) and up to four ring/spot/sweep/pulse layers in normalized coordinates; approved palette only. 'default' forces ordinary breathing, omission uses the saved slot default. Unsupported clips fall back to breathing. Existing calls/retries never replay or unmute."""
        arguments = {"slot_token": slot_token, "enabled": enabled,
                     "summary": summary, "idempotency_key": idempotency_key}
        if animation is not None:
            arguments['animation'] = animation
        return await call(ctx, 'set_notification', arguments)

    @server.tool(annotations=mutation)
    async def set_notification_animation(slot_token: str, animation: dict[str, Any] | None,
                                         idempotency_key: str, ctx: Context) -> dict[str, Any]:
        """Optional default for future notifications, including observed questions; does not notify or change existing calls. Null restores ordinary breathing. If no idea is immediately clear, keep the default rather than brainstorm. Clip uses duration_ms and <=4 layers: ring(center,radius,width), spot(from,to,radius), sweep(axis,from,to,width), pulse(center,radius,pulses); each may use color, opacity, start_ms/end_ms. Consult the skill only when customization is useful."""
        return await call(ctx, 'set_notification_animation', {'slot_token': slot_token, 'animation': animation,
            'idempotency_key': idempotency_key})

    @server.tool(annotations=mutation)
    async def show_keyboard_frame(slot_token: str, colors: dict[str, str], idempotency_key: str,
                                  ctx: Context, summary: str | None = None) -> dict[str, Any]:
        """Request a static keymap guide; it appears only after user pickup in Agent Mode. Colors map physical key IDs from get_status.keyboard_frame_capabilities to palette names or six-digit RGB. Unspecified keys use idle white; Escape is reserved red and closes the guide. Highlighted keys pass through normally; mode double-tap remains available. This already creates its own notification—do not notify separately. Returns a frame ID for cleanup."""
        return await call(ctx, 'show_keyboard_frame', {'slot_token': slot_token, 'colors': colors,
            'summary': summary, 'idempotency_key': idempotency_key})

    @server.tool(annotations=mutation)
    async def clear_keyboard_frame(slot_token: str, frame_id: str, idempotency_key: str, ctx: Context) -> dict[str, Any]:
        """Withdraw your exact pending or visible guide. Stale frame IDs cannot close a replacement; this does not answer a native question."""
        return await call(ctx, 'clear_keyboard_frame', {'slot_token': slot_token, 'frame_id': frame_id,
            'idempotency_key': idempotency_key})

    @server.tool(annotations=mutation)
    async def report_question(slot_token: str, question_id: str, kind: Literal["single_choice", "free_text"],
                              idempotency_key: str, ctx: Context, options: list[QuestionOption] | None = None,
                              allow_other: bool = False) -> dict[str, Any]:
        """Prepare a call and answer-key hints BEFORE asking one native Codex question. Preserve the exact option order; pickup reveals hints. This tool does not display or answer the native question."""
        return await call(ctx, "report_question", {"slot_token": slot_token, "question_id": question_id,
            "kind": kind, "options": [o.model_dump() for o in options or []],
            "allow_other": allow_other, "idempotency_key": idempotency_key})

    @server.tool(annotations=mutation)
    async def clear_question(slot_token: str, question_id: str, idempotency_key: str,
                             ctx: Context, outcome: Literal["answered", "cancelled", "withdrawn", "skipped", "expired"] = "answered") -> dict[str, Any]:
        """Clear this exact question and restore its lighting/controls after answer, cancellation, withdrawal, skip, or expiry. Does not answer a native question or change approval state."""
        return await call(ctx, "clear_question", {"slot_token": slot_token, "question_id": question_id,
            "outcome": outcome, "idempotency_key": idempotency_key})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_status(ctx: Context, slot_token: str | None = None) -> dict[str, Any]:
        """Inspect service readiness and your own slot/call/question. No allocation; other agents' private state is omitted."""
        return await call(ctx, "get_status", {"slot_token": slot_token})

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="legacy project-local bridge; plugin mode resolves native task context")
    parser.add_argument("--socket")
    parser.add_argument("--registered-thread-id", help="explicit single-task fallback, only with matching backend registration")
    args = parser.parse_args(argv)
    if args.registered_thread_id and not args.project:
        parser.error('--registered-thread-id requires the explicit legacy --project test mode')
    bridge = Bridge(str(Path(args.project).resolve()) if args.project else None,
                    args.socket, args.registered_thread_id)
    create_server(bridge).run(transport="stdio")


if __name__ == "__main__":
    main()
