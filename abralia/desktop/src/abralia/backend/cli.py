# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Terminal entry point for the persistent backend and project setup."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import signal
from uuid import uuid4

from .core import BrokerConfig
from .ipc import BrokerClient
from .project import disable_project, enable_project, inspect_project
from .service import BrokerService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "status", "enable-project", "inspect-project", "disable-project", "fixtures", "clear-fixtures", "simulate", "register-desktop", "register-tasks", "release-tasks", "set-background"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name not in ("inspect-project", "disable-project"):
            command.add_argument("--socket")
        if name in ("serve", "enable-project"):
            command.add_argument("--registered-thread-id")
        if name == "serve":
            command.add_argument("--profile", required=True)
            command.add_argument("--mode", choices=("hardware", "simulated"), default="simulated")
            command.add_argument("--settings", type=Path, help="JSON object of host policy/appearance overrides")
            command.add_argument("--log", type=Path, help="optional diagnostic log; excludes question text and tokens")
            command.add_argument("--observe-codex", action=argparse.BooleanOptionalAction, default=True,
                                 help="observe registered Codex sessions without model status calls")
            command.add_argument("--codex-home", type=Path, help="Codex session storage directory")
            command.add_argument("--codex-hook-journal", type=Path, help="metadata-only hook journal; defaults to the configured Abralia probe")
            command.add_argument("--desktop-thread-id", action="append", default=[], help="explicitly register a known desktop task for focus and native question hints; repeatable")
        elif name == "enable-project":
            command.add_argument("--python", help="interpreter in the environment where abralia-desktop[backend] is installed")
        elif name == "status":
            command.add_argument("--compact", action="store_true")
        elif name == "fixtures":
            command.add_argument("--count", type=int, required=True)
        elif name == "register-desktop":
            command.add_argument("--thread-id", required=True)
        elif name in ('register-tasks', 'release-tasks'):
            targets = command.add_mutually_exclusive_group(required=True)
            targets.add_argument('--thread-id', action='append', help='task UUID; repeat to target several tasks')
            targets.add_argument('--all', dest='all_project', action='store_true',
                                 help='all indexed project tasks for register; every slot and recovery reservation for release')
        elif name == "set-background":
            command.add_argument("--percent", type=float, required=True)
        elif name == "simulate":
            command.add_argument("event", choices=("toggle", "next_page", "previous_page", "select", "mute", "pickup",
                                                   "navigation_hold", "page_previous", "page_next", "first_agent",
                                                   "last_agent", "slot_previous", "slot_next", "confirm"))
            command.add_argument("--position", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "enable-project":
            result = enable_project(args.project, python=args.python, endpoint=args.socket,
                                    registered_thread_id=args.registered_thread_id)
        elif args.command == "inspect-project":
            result = inspect_project(args.project)
        elif args.command == "disable-project":
            result = disable_project(args.project)
        elif args.command == "serve":
            overrides = json.loads(args.settings.read_text()) if args.settings else {}
            config = BrokerConfig(**overrides)
            handlers = [logging.StreamHandler()]
            if args.log:
                handlers.append(logging.FileHandler(args.log))
            logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(message)s")
            service = BrokerService(args.project, args.profile, mode=args.mode, config=config,
                                    endpoint=args.socket, registered_thread_id=args.registered_thread_id,
                                    desktop_thread_ids=args.desktop_thread_id, observe_codex=args.observe_codex,
                                    codex_home=args.codex_home, codex_hook_journal=args.codex_hook_journal)
            old_term = signal.signal(signal.SIGTERM, lambda *_: service.stop_event.set())
            try:
                with service:
                    print(json.dumps({"event": "BACKEND_READY", "mode": args.mode,
                        "project": service.project, "socket": str(service.endpoint), "profile": args.profile}), flush=True)
                    try:
                        while not service.stop_event.wait(.5):
                            pass
                    except KeyboardInterrupt:
                        pass
            finally:
                signal.signal(signal.SIGTERM, old_term)
            print(json.dumps({"event": "BACKEND_STOPPED", "cleanup_error": service.cleanup_error,
                              "worker_error": service.worker_error}), flush=True)
            return 1 if service.cleanup_error or service.worker_error else 0
        else:
            message = {"type": "admin", "action": {"status": "status", "fixtures": "seed_fixtures",
                "clear-fixtures": "clear_fixtures", "simulate": "input", "register-desktop": "register_desktop", "set-background": "set_background",
                "register-tasks":"register_tasks", "release-tasks":"release_tasks"}[args.command]}
            if args.command == "fixtures":
                message["count"] = args.count
            elif args.command == "simulate":
                message.update(event=args.event, position=args.position)
            elif args.command == "register-desktop":
                message["thread_id"] = args.thread_id
            elif args.command in ('register-tasks', 'release-tasks'):
                message.update(thread_ids=args.thread_id, all_project=args.all_project, idempotency_key=str(uuid4()))
            elif args.command == "set-background":
                message["percent"] = args.percent
            with BrokerClient(args.project, endpoint=args.socket, role="admin") as client:
                result = client.request(message)
            if args.command == "status" and args.compact:
                result.pop("rendered_keys", None)
                result["events"] = result.get("events", [])[-8:]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status", "accepted") == "accepted" else 1
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
