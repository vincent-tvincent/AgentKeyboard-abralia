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

from .ipc import BrokerClient

INSTRUCTIONS = (
    "Abralia controls this project's agent keyboard. Read the project-local abralia skill. "
    "Self-register with acquire_slot: report your harness as codex_desktop, codex_cli, or unknown. "
    "Task identity comes from the harness; no user registration command is needed. "
    "Keep your slot token; update status and request attention only when useful. "
    "Inspect get_status for a healthy codex_observer before asking a native question. "
    "When available, it handles question attention automatically; do not send a duplicate report. "
    "Otherwise follow the skill's explicit question-report fallback and clear it after the answer. "
    "Pickup opens a task, not a verified answer-key mapping. Mute/pickup release both call controls. "
    "Missing service is a normal skipped result; continue work. "
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
    def __init__(self, project: str, endpoint=None, registered_thread_id=None):
        self.client = BrokerClient(project, endpoint=endpoint, registered_thread_id=registered_thread_id, recover_slots=True)
        self.stop = threading.Event()
        self.heartbeat = None

    def start(self):
        def beat():
            while not self.stop.wait(5):
                # A surviving bridge can resume only its previously owned callers.
                # Retry the read-only ping once after a dead backend socket closes.
                result = self.client.request({"type": "ping"})
                if result.get('reason') in ('backend_unavailable', 'backend_stopping', 'backend_stopped'):
                    self.client.request({"type": "ping"})
        self.heartbeat = threading.Thread(target=beat, name="abralia-bridge-heartbeat", daemon=True)
        self.heartbeat.start()

    def close(self):
        self.stop.set()
        if self.heartbeat:
            self.heartbeat.join(timeout=9)
        self.client.close()

    def call(self, meta, operation: str, arguments: dict) -> dict:
        metadata = codex_metadata(meta)
        if not metadata and not self.client.registered_thread_id:
            return {"status": "skipped", "reason": "caller_identity_unavailable",
                    "hint": "This client supplied no usable Codex thread metadata. A maintainer may configure an explicit test connection."}
        return self.client.request({"type": "call", "metadata": metadata,
                                    "operation": operation, "arguments": arguments})


def create_server(bridge: Bridge) -> MCPServer:
    @asynccontextmanager
    async def lifespan(server):
        bridge.start()
        try:
            yield {}
        finally:
            await anyio.to_thread.run_sync(bridge.close)

    server = MCPServer("abralia_experiment", instructions=INSTRUCTIONS, version="0.3.0",
                       lifespan=lifespan, log_level="WARNING")
    mutation = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    async def call(ctx: Context, operation: str, arguments: dict):
        return await anyio.to_thread.run_sync(bridge.call, ctx.request_context.meta, operation, arguments)

    @server.tool(annotations=mutation)
    async def acquire_slot(label: str, idempotency_key: str, ctx: Context,
                           harness: Literal["codex_desktop", "codex_cli", "unknown"] | None = None) -> dict[str, Any]:
        """Register yourself and claim your stable slot. Report your actual harness: codex_desktop enables pickup/question hints; codex_cli or unknown keeps status/notifications only. Task identity comes from MCP metadata, never a model-supplied ID. Returns ownership token, page, F-key and registration provenance."""
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
                               ctx: Context, summary: str | None = None) -> dict[str, Any]:
        """Request or withdraw a call for your slot. Updates do not replay the onset or override a user's mute."""
        return await call(ctx, "set_notification", {"slot_token": slot_token, "enabled": enabled,
            "summary": summary, "idempotency_key": idempotency_key})

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--socket")
    parser.add_argument("--registered-thread-id", help="explicit single-task fallback, only with matching backend registration")
    args = parser.parse_args()
    bridge = Bridge(str(Path(args.project).resolve()), args.socket, args.registered_thread_id)
    create_server(bridge).run(transport="stdio")


if __name__ == "__main__":
    main()
