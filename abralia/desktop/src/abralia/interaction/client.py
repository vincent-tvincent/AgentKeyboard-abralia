# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Complete low-level client and high-level Host Interaction controller."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from .errors import (
    FirmwareRejectedError,
    KeycodeLookupError,
    ProtocolError,
    SessionError,
)
from .keymap import (
    KeycodeMatch,
    ViaKeymapReader,
)
from .protocol import (
    BindingEntry,
    BindingPolicy,
    Capabilities,
    ControlId,
    ControlKind,
    DeviceEvent,
    ForceScope,
    Opcode,
    Response,
    Result,
    ack_event_packet,
    begin_binding_replace_packet,
    begin_force_scope_packet,
    claim_session_packet,
    clear_bindings_packet,
    clear_force_scope_packet,
    commit_bindings_packet,
    commit_force_scope_packet,
    get_capabilities_packet,
    get_status_packet,
    is_device_event,
    keepalive_packet,
    parse_capabilities,
    parse_device_event,
    parse_response,
    release_session_packet,
    response_matches,
    write_bindings_packet,
    write_force_controls_packet,
)
from .transport import HidApiInteractionTransport, InteractionTransport

if TYPE_CHECKING:
    from abralia.layout import ResolvedCompatibilityLayout, ResolvedRegion


@dataclass(frozen=True, slots=True)
class ConfiguredBinding:
    entry: BindingEntry
    policy: BindingPolicy


@dataclass(frozen=True, slots=True)
class BindingUpdate:
    controls: tuple[ControlId, ...]
    binding_generation: int
    response: Response
    keycode_matches: tuple[KeycodeMatch, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivationUpdate:
    scope: ForceScope | None
    controls: tuple[ControlId, ...]
    force_generation: int
    response: Response
    keycode_matches: tuple[KeycodeMatch, ...] = ()


def _next_generation(current: int) -> int:
    value = (current + 1) & 0xFFFF
    return value or 1


T = TypeVar("T")


def _chunks(values: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class HostInteractionProtocolClient:
    """Exact synchronous representation of every firmware protocol v1 command."""

    def __init__(self, transport: InteractionTransport):
        self.transport = transport
        self.session_token = 0
        self.capabilities: Capabilities | None = None
        self._heartbeat_sequence = 0
        self._next_heartbeat_at = 0.0
        self._events: deque[DeviceEvent] = deque()
        self._unexpected_reports: deque[bytes] = deque(maxlen=64)

    @classmethod
    def open_keychron_v3_8k(cls) -> HostInteractionProtocolClient:
        return cls(HidApiInteractionTransport.open_keychron_v3_8k())

    def _capture_report(self, report: bytes) -> None:
        if is_device_event(report):
            event = parse_device_event(report)
            if self.session_token == 0 or event.session_token == self.session_token:
                self._events.append(event)
            return
        self._unexpected_reports.append(report)

    def _capture_unmatched(self) -> None:
        while True:
            report = self.transport.pop_unmatched()
            if report is None:
                return
            self._capture_report(report)

    def _transact(self, packet: bytes, opcode: Opcode) -> Response:
        report = self.transport.transact(
            packet, lambda value: response_matches(value, opcode)
        )
        self._capture_unmatched()
        response = parse_response(report, opcode)
        if response.result is not Result.OK:
            raise FirmwareRejectedError(
                f"{opcode.name} failed with {response.result.name}.", response
            )
        return response

    def get_capabilities(self, *, refresh: bool = False) -> Capabilities:
        if self.capabilities is not None and not refresh:
            return self.capabilities
        report = self.transport.transact(
            get_capabilities_packet(),
            lambda value: response_matches(value, Opcode.GET_CAPABILITIES),
        )
        self._capture_unmatched()
        capabilities = parse_capabilities(report)
        if capabilities.response.result is not Result.OK:
            raise FirmwareRejectedError(
                "GET_CAPABILITIES was rejected.", capabilities.response
            )
        self.capabilities = capabilities
        return capabilities

    def get_status(self) -> Response:
        return self._transact(
            get_status_packet(self.session_token), Opcode.GET_STATUS
        )

    def claim_session(self, token: int | None = None) -> Response:
        claimed = token if token is not None else (secrets.randbits(32) or 1)
        response = self._transact(
            claim_session_packet(claimed), Opcode.CLAIM_SESSION
        )
        if response.session_token != claimed:
            raise ProtocolError("Firmware acknowledged a different session token.")
        self.session_token = claimed
        self._heartbeat_sequence = response.heartbeat_sequence
        self._next_heartbeat_at = time.monotonic() + 1.0
        self._events.clear()
        return response

    def _require_session(self) -> int:
        if self.session_token == 0:
            raise SessionError("A live Host Interaction session is required.")
        return self.session_token

    def keepalive(self, sequence: int | None = None) -> Response:
        token = self._require_session()
        if sequence is None:
            sequence = (self._heartbeat_sequence + 1) & 0xFFFF
        response = self._transact(
            keepalive_packet(token, sequence), Opcode.KEEPALIVE
        )
        self._heartbeat_sequence = sequence
        self._next_heartbeat_at = time.monotonic() + 1.0
        return response

    def release_session(self) -> Response:
        token = self._require_session()
        response = self._transact(
            release_session_packet(token), Opcode.RELEASE_SESSION
        )
        self.session_token = 0
        self._heartbeat_sequence = 0
        self._next_heartbeat_at = 0.0
        self._events.clear()
        return response

    def begin_binding_replace(self, generation: int) -> Response:
        return self._transact(
            begin_binding_replace_packet(self._require_session(), generation),
            Opcode.BEGIN_BINDING_REPLACE,
        )

    def write_bindings(
        self,
        generation: int,
        policy: BindingPolicy,
        entries: Sequence[BindingEntry],
    ) -> Response:
        return self._transact(
            write_bindings_packet(
                self._require_session(), generation, policy, entries
            ),
            Opcode.WRITE_BINDINGS,
        )

    def commit_bindings(self, generation: int) -> Response:
        return self._transact(
            commit_bindings_packet(self._require_session(), generation),
            Opcode.COMMIT_BINDINGS,
        )

    def clear_bindings(self, generation: int) -> Response:
        return self._transact(
            clear_bindings_packet(self._require_session(), generation),
            Opcode.CLEAR_BINDINGS,
        )

    def begin_force_scope(
        self,
        *,
        binding_generation: int,
        force_generation: int,
        scope: ForceScope,
        lease_ms: int,
    ) -> Response:
        return self._transact(
            begin_force_scope_packet(
                self._require_session(),
                binding_generation=binding_generation,
                force_generation=force_generation,
                scope=scope,
                lease_ms=lease_ms,
            ),
            Opcode.BEGIN_FORCE_SCOPE,
        )

    def write_force_controls(
        self, force_generation: int, controls: Sequence[ControlId]
    ) -> Response:
        return self._transact(
            write_force_controls_packet(
                self._require_session(), force_generation, controls
            ),
            Opcode.WRITE_FORCE_KEYS,
        )

    def commit_force_scope(self, force_generation: int) -> Response:
        return self._transact(
            commit_force_scope_packet(self._require_session(), force_generation),
            Opcode.COMMIT_FORCE_SCOPE,
        )

    def clear_force_scope(self) -> Response:
        return self._transact(
            clear_force_scope_packet(self._require_session()),
            Opcode.CLEAR_FORCE_SCOPE,
        )

    def ack_event(self, event_sequence: int) -> Response:
        return self._transact(
            ack_event_packet(self._require_session(), event_sequence),
            Opcode.ACK_EVENT,
        )

    def read_event(
        self, timeout_ms: int = 0, *, acknowledge: bool = True
    ) -> DeviceEvent | None:
        self._capture_unmatched()
        if not self._events:
            report = self.transport.read(timeout_ms)
            if report:
                self._capture_report(report)
        if not self._events:
            return None
        event = self._events.popleft()
        if acknowledge:
            self.ack_event(event.sequence)
        return event

    def service(
        self, timeout_ms: int = 0, *, acknowledge: bool = True
    ) -> tuple[DeviceEvent, ...]:
        """Maintain heartbeat and receive/ACK events for up to timeout_ms."""

        if timeout_ms < 0:
            raise ProtocolError("Service timeout cannot be negative.")
        deadline = time.monotonic() + timeout_ms / 1000
        events: list[DeviceEvent] = []
        while True:
            now = time.monotonic()
            if self.session_token and now >= self._next_heartbeat_at:
                self.keepalive()

            if timeout_ms == 0:
                wait_ms = 0
            else:
                remaining_ms = max(0, int((deadline - now) * 1000))
                if remaining_ms == 0:
                    break
                heartbeat_ms = (
                    max(0, int((self._next_heartbeat_at - now) * 1000))
                    if self.session_token
                    else remaining_ms
                )
                wait_ms = min(remaining_ms, heartbeat_ms)

            event = self.read_event(wait_ms, acknowledge=acknowledge)
            if event is not None:
                events.append(event)
                continue
            if timeout_ms == 0 or time.monotonic() >= deadline:
                break
        return tuple(events)

    def reset_session(self) -> Response:
        """Clear manual/forced activation, bindings, events, and RGB reset state."""

        if self.session_token:
            self.release_session()
        return self.claim_session()

    @property
    def unexpected_reports(self) -> tuple[bytes, ...]:
        return tuple(self._unexpected_reports)

    def close(self) -> None:
        if self.session_token:
            try:
                self.release_session()
            except Exception:
                pass
        self.transport.close()

    def __enter__(self) -> HostInteractionProtocolClient:
        self.get_capabilities()
        self.claim_session()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


class HostInteractionController:
    """Layout-agnostic direct-ControlId and live-keycode binding API."""

    PAUSE_CONTROL = ControlId.key(0, 16)

    def __init__(
        self,
        client: HostInteractionProtocolClient,
        *,
        keymap_reader: ViaKeymapReader | None = None,
        compatibility: ResolvedCompatibilityLayout | None = None,
    ):
        self.client = client
        self.capabilities = client.get_capabilities()
        self.keymap_reader = keymap_reader or ViaKeymapReader(client.transport)
        self.compatibility = compatibility
        self._bindings: dict[ControlId, ConfiguredBinding] = {}

    @property
    def bindings(self) -> tuple[ConfiguredBinding, ...]:
        return tuple(self._bindings[key] for key in sorted(self._bindings))

    def resolve_region(self, region_id: str) -> ResolvedRegion:
        if self.compatibility is None:
            raise ProtocolError("No compatibility layout is configured.")
        return self.compatibility.region(region_id)

    def region_controls(self, region_id: str) -> tuple[ControlId, ...]:
        return self.resolve_region(region_id).controls

    def _validate_control(self, control_id: ControlId) -> None:
        if control_id == self.PAUSE_CONTROL:
            raise ProtocolError("The physical Pause control is reserved by firmware.")
        if control_id.kind is ControlKind.KEY:
            if (
                control_id.primary >= self.capabilities.matrix_rows
                or control_id.secondary >= self.capabilities.matrix_columns
            ):
                raise ProtocolError(f"Key control {control_id} is outside the matrix.")
            return
        if control_id.primary >= self.capabilities.encoder_count or control_id.secondary:
            raise ProtocolError(f"Encoder control {control_id} is outside capabilities.")

    def _replace(self, desired: dict[ControlId, ConfiguredBinding]) -> BindingUpdate:
        status = self.client.get_status()
        generation = _next_generation(status.binding_generation)
        if not desired:
            response = self.client.clear_bindings(generation)
            self._bindings = {}
            return BindingUpdate((), generation, response)

        try:
            self.client.begin_binding_replace(generation)
            grouped: dict[BindingPolicy, list[BindingEntry]] = defaultdict(list)
            for control_id in sorted(desired):
                self._validate_control(control_id)
                configured = desired[control_id]
                grouped[configured.policy].append(configured.entry)
            for policy, entries in grouped.items():
                for chunk in _chunks(entries, 3):
                    self.client.write_bindings(generation, policy, chunk)
            response = self.client.commit_bindings(generation)
        except Exception:
            # Protocol v1 has no binding-staging abort. A session reset is the
            # only safe way to discard an uncertain partial transaction.
            self._bindings.clear()
            try:
                self.client.reset_session()
            except Exception:
                pass
            raise
        self._bindings = dict(desired)
        return BindingUpdate(tuple(sorted(desired)), generation, response)

    def set_controls(
        self,
        controls: Iterable[ControlId | int],
        *,
        binding_id: int,
        policy: BindingPolicy = BindingPolicy(),
    ) -> BindingUpdate:
        resolved = tuple(
            sorted(
                {
                    control if isinstance(control, ControlId) else ControlId(control)
                    for control in controls
                }
            )
        )
        if not resolved:
            raise ProtocolError("At least one control is required.")
        desired = dict(self._bindings)
        for control_id in resolved:
            self._validate_control(control_id)
            desired[control_id] = ConfiguredBinding(
                BindingEntry(control_id, binding_id), policy
            )
        update = self._replace(desired)
        return BindingUpdate(resolved, update.binding_generation, update.response)

    def set_region_controls(
        self,
        region_id: str,
        *,
        binding_id: int,
        policy: BindingPolicy | None = None,
    ) -> BindingUpdate:
        return self.set_controls(
            self.region_controls(region_id),
            binding_id=binding_id,
            policy=policy or BindingPolicy(),
        )

    def replace_bindings(
        self, bindings: Iterable[ConfiguredBinding]
    ) -> BindingUpdate:
        """Atomically replace the complete table with caller-addressed controls."""

        desired: dict[ControlId, ConfiguredBinding] = {}
        for binding in bindings:
            control_id = binding.entry.control_id
            self._validate_control(control_id)
            if control_id in desired:
                raise ProtocolError(f"Duplicate control {control_id} in replacement.")
            desired[control_id] = binding
        return self._replace(desired)

    def set_keycode_controls(
        self,
        keycode: int | str,
        *,
        binding_id: int,
        policy: BindingPolicy = BindingPolicy(),
        layers: Sequence[int] | None = None,
        include_encoders: bool = True,
    ) -> BindingUpdate:
        matches = self.keymap_reader.resolve(
            keycode,
            self.capabilities,
            layers=layers,
            include_encoders=include_encoders,
        )
        controls = tuple(sorted({match.control_id for match in matches}))
        if not controls:
            raise KeycodeLookupError(
                f"Keycode {keycode!r} is not assigned to any queried control."
            )
        update = self.set_controls(controls, binding_id=binding_id, policy=policy)
        return BindingUpdate(
            controls, update.binding_generation, update.response, matches
        )

    def remove_controls(
        self, controls: Iterable[ControlId | int]
    ) -> BindingUpdate:
        resolved = tuple(
            sorted(
                {
                    control if isinstance(control, ControlId) else ControlId(control)
                    for control in controls
                }
            )
        )
        desired = dict(self._bindings)
        for control in resolved:
            desired.pop(control, None)
        update = self._replace(desired)
        return BindingUpdate(resolved, update.binding_generation, update.response)

    def remove_region_controls(self, region_id: str) -> BindingUpdate:
        return self.remove_controls(self.region_controls(region_id))

    def remove_keycode_controls(
        self,
        keycode: int | str,
        *,
        layers: Sequence[int] | None = None,
        include_encoders: bool = True,
    ) -> BindingUpdate:
        matches = self.keymap_reader.resolve(
            keycode,
            self.capabilities,
            layers=layers,
            include_encoders=include_encoders,
        )
        controls = tuple(sorted({match.control_id for match in matches}))
        if not controls:
            raise KeycodeLookupError(
                f"Keycode {keycode!r} is not assigned to any queried control."
            )
        update = self.remove_controls(controls)
        return BindingUpdate(
            controls, update.binding_generation, update.response, matches
        )

    def clear_bindings(self) -> BindingUpdate:
        return self._replace({})

    def _activate(
        self,
        scope: ForceScope,
        controls: Sequence[ControlId],
        lease_ms: int,
    ) -> ActivationUpdate:
        status = self.client.get_status()
        generation = _next_generation(status.force_generation)
        try:
            self.client.begin_force_scope(
                binding_generation=status.binding_generation,
                force_generation=generation,
                scope=scope,
                lease_ms=lease_ms,
            )
            if scope is ForceScope.SELECTED:
                if not controls:
                    raise ProtocolError(
                        "Selected activation requires at least one control."
                    )
                for chunk in _chunks(controls, 10):
                    self.client.write_force_controls(generation, chunk)
            response = self.client.commit_force_scope(generation)
        except Exception:
            # CLEAR_FORCE_SCOPE also discards an in-progress force transaction.
            try:
                self.client.clear_force_scope()
            except Exception:
                pass
            raise
        return ActivationUpdate(scope, tuple(controls), generation, response)

    def activate_all(self, *, lease_ms: int = 30_000) -> ActivationUpdate:
        return self._activate(ForceScope.ALL_CONFIGURED, (), lease_ms)

    def activate_controls(
        self, controls: Iterable[ControlId | int], *, lease_ms: int = 30_000
    ) -> ActivationUpdate:
        resolved = tuple(
            sorted(
                {
                    control if isinstance(control, ControlId) else ControlId(control)
                    for control in controls
                }
            )
        )
        for control in resolved:
            self._validate_control(control)
        return self._activate(ForceScope.SELECTED, resolved, lease_ms)

    def activate_region(
        self, region_id: str, *, lease_ms: int = 30_000
    ) -> ActivationUpdate:
        return self.activate_controls(
            self.region_controls(region_id), lease_ms=lease_ms
        )

    def activate_keycode_controls(
        self,
        keycode: int | str,
        *,
        lease_ms: int = 30_000,
        layers: Sequence[int] | None = None,
        include_encoders: bool = True,
    ) -> ActivationUpdate:
        matches = self.keymap_reader.resolve(
            keycode,
            self.capabilities,
            layers=layers,
            include_encoders=include_encoders,
        )
        controls = tuple(sorted({match.control_id for match in matches}))
        if not controls:
            raise KeycodeLookupError(
                f"Keycode {keycode!r} is not assigned to any queried control."
            )
        update = self.activate_controls(controls, lease_ms=lease_ms)
        return ActivationUpdate(
            update.scope,
            controls,
            update.force_generation,
            update.response,
            matches,
        )

    def deactivate_forced(self) -> ActivationUpdate:
        response = self.client.clear_force_scope()
        return ActivationUpdate(None, (), response.force_generation, response)

    def turn_off_all(self, *, reclaim_session: bool = False) -> Response:
        """Use RELEASE_SESSION to clear manual and forced mode plus all bindings."""

        response = self.client.release_session()
        self._bindings.clear()
        if reclaim_session:
            self.client.claim_session()
        return response
