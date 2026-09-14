#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Isolated physical slot-order trial using Abralia's production input/rendering.

Stop the ordinary backend first. Double-tap the physical mode key to enter
Agent Mode, hold it to arm navigation, then tap violet Escape to switch order.
The optional private JSONL command file accepts {"command":"add","project":"A"}
(also B/C) and {"command":"stop"}; it never changes normal keyboard bindings.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import time

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.device import DeviceDriver, Route
from abralia.backend.render import Renderer, within_frame_peak
from abralia.interaction import ControlId, Edge, EventType
from abralia.rgb import Srgb8
from sort_model import SortModel

SORT_BINDING = 90
SORT_COLOR = Srgb8(160, 64, 255)


class SortBroker(Broker):
    def __init__(self, *, clock=time.monotonic):
        super().__init__(BrokerConfig(background_brightness_percent=50), clock=clock)
        self.sort_model = SortModel()
        self.demo_allocations = {}
        for _ in range(4):
            for letter in 'ABC':
                self.add_agent(letter)

    def add_agent(self, letter):
        if letter not in ('A', 'B', 'C'):
            raise ValueError('project_must_be_A_B_or_C')
        project = f'Project {letter}'
        siblings = sum(s.project == project for s in self.sort_model.ordered_slots())
        model_slot = self.sort_model.add(project, f'{letter}{siblings + 1}')
        caller = Caller(f'sort-demo:{model_slot.slot_id}', surface='simulated')
        result = self.call(caller, 'acquire_slot', {'label': model_slot.label, 'idempotency_key': 'acquire'})
        if result['status'] != 'accepted':
            raise RuntimeError('demo_allocation_failed')
        self.demo_allocations[model_slot.slot_id] = result['allocation']['slot_id']
        self.apply_sort()
        self.event('demo_agent_added', self.slots[self.demo_allocations[model_slot.slot_id]])
        return model_slot

    def apply_sort(self):
        # Stable allocation identity/ownership is independent of physical order.
        for position, model_slot in enumerate(self.sort_model.ordered_slots(), 1):
            slot = self.slots[self.demo_allocations[model_slot.slot_id]]
            color = self.sort_model.color(model_slot)
            if slot.position != position or slot.identity_color != color:
                slot.display_position, slot.identity_color = position, color
                self.identity_colors[slot.caller.caller_id] = color
                slot.revision += 1
        self.page = min(self.page, self.page_count - 1)
        self._layout_changed()
        self.page_revision += 1
        self.cursor_revision += 1
        self.knob_selection_revision += 1
        if self.selection_preview_active() and self.candidate() is None:
            visible = self.visible_slots()
            self._set_cursor(visible[0].slot_token if visible else None)

    def toggle_sort(self):
        self.sort_model.toggle_sort()
        self.apply_sort()
        self._navigation_activity()
        self.event('demo_sort_changed', order=self.sort_model.mode)

    def demo_snapshot(self):
        return {**self.sort_model.snapshot(), 'active': self.active,
                'navigation_active': self.navigation_active, 'knob_mode': self.knob_mode,
                'current_page': self.page + 1, 'selected_slot': self.selected,
                'candidate_slot': self.candidate().slot_id if self.candidate() else None}


class SortRenderer(Renderer):
    def frame(self, broker):
        frame = super().frame(broker)
        if not broker.active or not broker.navigation_active or broker.visible_keyboard_frame():
            return frame
        colors = dict(frame.payload.colors)
        peak = max(max(c.red, c.green, c.blue)
                   for c in (frame.payload.background, *colors.values()))
        colors['ESC'] = within_frame_peak(SORT_COLOR, peak)
        return replace(frame, payload=replace(frame.payload, colors=colors))


class SortDriver(DeviceDriver):
    def __init__(self, broker, profile, mode, **kwargs):
        super().__init__(broker, profile, mode, **kwargs)
        escape = self.profile.element_by_id.get('ESC')
        if escape is None or escape.matrix is None or not escape.rgb_capable:
            raise ValueError('demo_requires_physical_escape_metadata')
        self.sort_control = ControlId.key(*escape.matrix)
        self.renderer = SortRenderer(self.profile, fps=broker.config.fps)

    def build_routes(self):
        routes = super().build_routes()
        if self.broker.active and self.broker.navigation_active and not self.broker.visible_keyboard_frame():
            routes = {binding: route for binding, route in routes.items() if route.control != self.sort_control}
            routes[SORT_BINDING] = Route(self.sort_control, 'demo_sort',
                                        navigation_revision=self.broker.navigation_revision,
                                        layout_revision=self.broker.layout_revision)
        return routes

    def handle_event(self, event):
        if event.event_type is EventType.CONTROL_EDGE and event.binding_id == SORT_BINDING:
            route = self.routes.get(SORT_BINDING)
            if (route and event.control_id == route.control and event.edge is Edge.UP
                    and event.binding_generation == self.generation
                    and self.broker.active and self.broker.navigation_active
                    and route.navigation_revision == self.broker.navigation_revision
                    and route.layout_revision == self.broker.layout_revision
                    and not self.broker.visible_keyboard_frame()):
                self.broker.toggle_sort()
            return
        super().handle_event(event)


def private_file(path, *, output=False):
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL) if output else (os.O_RDONLY | os.O_CREAT)
    fd = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_mode & 0o077
                or (hasattr(os, 'getuid') and metadata.st_uid != os.getuid())):
            raise ValueError('artifact_must_be_private_regular_file')
        return os.fdopen(fd, 'w' if output else 'rb')
    except BaseException:
        os.close(fd)
        raise


class Commands:
    """Bounded append-only input; partial JSON lines wait for their newline."""
    def __init__(self, stream):
        self.stream = stream
        self.buffer = b''

    def poll(self):
        size = os.fstat(self.stream.fileno()).st_size
        if size > 1024 * 1024 or size < self.stream.tell():
            raise ValueError('command_file_size_or_truncation')
        self.buffer += self.stream.read(4096)
        lines = self.buffer.split(b'\n')
        self.buffer = lines.pop()
        if len(self.buffer) > 4096:
            raise ValueError('command_line_too_long')
        result = []
        for line in lines:
            if len(line) > 4096:
                raise ValueError('command_line_too_long')
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                value = None
            result.append(value)
        return result


def apply_command(broker, command, mode):
    if not isinstance(command, dict):
        raise ValueError('invalid_command')
    action = command.get('command')
    if action == 'add' and set(command) == {'command', 'project'}:
        broker.add_agent(command['project'])
    elif action == 'stop' and set(command) == {'command'}:
        return False
    elif action == 'toggle_sort' and set(command) == {'command'} and mode == 'simulated':
        broker.toggle_sort()
    else:
        raise ValueError('invalid_or_disallowed_command')
    return True


def run(args, *, driver_factory=SortDriver):
    broker = SortBroker()
    last_sequence = broker.sequence
    with ExitStack() as files:
        output = files.enter_context(private_file(args.events, output=True)) if args.events else None
        commands = Commands(files.enter_context(private_file(args.command_file))) if args.command_file else None

        def emit(kind, **fields):
            line = json.dumps({'kind': kind, **fields}, separators=(',', ':'))
            print(line, flush=True)
            if output:
                output.write(line + '\n'); output.flush()

        def observe(event):
            if event.event_type in (EventType.MODE_CHANGED, EventType.CONTROL_EDGE):
                emit('input', event=event.event_type.name, sequence=event.sequence,
                     binding_id=event.binding_id, generation=event.binding_generation,
                     edge=event.edge.name if event.event_type is EventType.CONTROL_EDGE else None)

        driver = driver_factory(broker, args.profile, args.mode, event_observer=observe)
        fault = cleanup_error = None
        old_term = signal.getsignal(signal.SIGTERM)

        def terminate(*_):
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, terminate)
            driver.start()
            emit('ready', mode=args.mode, profile=args.profile,
                 background_percent=50, keyboard_upper_bound='preserved',
                 instructions='Double-tap mode key, hold to navigate, tap violet Escape to change order.')
            deadline, last_view, running = time.monotonic() + args.duration, None, True
            while running and time.monotonic() < deadline:
                if commands:
                    for command in commands.poll():
                        try:
                            running = apply_command(broker, command, args.mode)
                        except (ValueError, TypeError):
                            emit('command_rejected')
                        if not running:
                            break
                if not running:
                    break
                broker.step()
                driver.tick()
                if broker.delivery in ('suspended', 'failed'):
                    fault = 'device_suspended'
                    break
                for row in broker.events:
                    if row['sequence'] > last_sequence:
                        emit('backend_event', event=row['kind'], slot_id=row.get('slot_id'))
                        last_sequence = row['sequence']
                view = broker.demo_snapshot()
                if view != last_view:
                    frame = driver.renderer.frame(broker).payload
                    samples = {key: [color.red, color.green, color.blue]
                               for key, color in frame.colors.items() if key in ('ESC', *[f'F{i}' for i in range(1, 13)])}
                    emit('layout', **view, rendered_keys=samples)
                    last_view = view
                time.sleep(.003)
        except KeyboardInterrupt:
            emit('interrupted')
        except Exception as error:
            fault = type(error).__name__
        finally:
            try:
                driver.close()
            except Exception as error:
                cleanup_error = type(error).__name__
            signal.signal(signal.SIGTERM, old_term)
            emit('closed', fault=fault, cleanup_error=cleanup_error,
                 restoration='driver_close_completed' if cleanup_error is None else 'unverified')
        return 1 if fault or cleanup_error else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--mode', choices=('simulated', 'hardware'), default='simulated')
    parser.add_argument('--duration', type=float, default=120)
    parser.add_argument('--command-file', type=Path)
    parser.add_argument('--events', type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 0 < args.duration <= 1800:
        parser.error('--duration must be greater than 0 and at most 1800 seconds')
    if args.command_file and args.events and args.command_file.absolute() == args.events.absolute():
        parser.error('--command-file and --events must differ')
    try:
        return run(args)
    except (OSError, ValueError) as error:
        print(f'Demo setup failed: {type(error).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
