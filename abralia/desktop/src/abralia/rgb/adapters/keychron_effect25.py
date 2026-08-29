# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Abralia/Keychron effect-25 Raw HID adapter."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from ..colors import BLACK, Hsv8, to_hsv8
from ..compatibility import AdapterCapabilities
from ..errors import CapabilityError, EffectUnavailableError, TransportError
from ..key_lookup import LiveKeymapAddressSpace
from ..led_mapper import DeviceFrame, LedColor
from ..profiles import EncoderDirection, EncoderPosition
from ..transport import (
    HidApiTransport,
    HidDeviceInfo,
    RawHidTransport,
    enumerate_hid_devices,
)
from .base import AdapterHealth, DeviceSnapshot

KEYCHRON_VID = 0x3434
V3_8K_PID = 0x0F30
RAW_USAGE_PAGE = 0xFF60
RAW_USAGE = 0x61
EFFECT_25 = 25
EXPECTED_LED_COUNT = 87
MAX_COLORS_PER_PACKET = 9
MAX_KEYMAP_BYTES_PER_PACKET = 28
MATRIX_ROWS = 6
MATRIX_COLUMNS = 17
ENCODER_COUNT = 1


class EffectSelectionPolicy(IntEnum):
    AUTO_SELECT = 0
    REQUIRE_SELECTED = 1


class FrameOperation(IntEnum):
    AWAIT = 0
    DIRECT = 1
    BEGIN = 2
    COMMIT = 3


class FrameState(IntEnum):
    AWAITING = 0
    DIRECT = 1
    GUARDED = 2


class FrameResult(IntEnum):
    OK = 0
    BUSY = 1
    INVALID_STATE = 2


class FrameFlags(IntFlag):
    ACTIVE_VALID = 1 << 0
    PENDING_VALID = 1 << 1
    BACK_BUFFER_FREE = 1 << 2
    TRANSITION_QUEUED = 1 << 3


@dataclass(frozen=True, slots=True)
class KeychronEffect25Snapshot:
    effect: int
    brightness: int
    per_key_type: int
    colors: tuple[Hsv8, ...]
    frame_state: FrameState


class KeychronEffect25Adapter:
    adapter_id = "keychron-effect25-rawhid"
    adapter_version = 1

    def __init__(
        self,
        transport: RawHidTransport,
        device: HidDeviceInfo,
        *,
        effect_selection_policy: EffectSelectionPolicy = EffectSelectionPolicy.AUTO_SELECT,
    ):
        self.transport = transport
        self.device = device
        self.effect_selection_policy = effect_selection_policy
        self._sequence = 0
        self._last_frame: DeviceFrame | None = None
        self._brightness_ceiling = 255
        self._closed = False
        self._capabilities: AdapterCapabilities | None = None

    @classmethod
    def discover(cls) -> list[HidDeviceInfo]:
        return [
            device
            for device in enumerate_hid_devices()
            if device.vendor_id == KEYCHRON_VID
            and device.product_id == V3_8K_PID
            and device.usage_page == RAW_USAGE_PAGE
            and device.usage == RAW_USAGE
        ]

    @classmethod
    def open(cls, device: HidDeviceInfo) -> KeychronEffect25Adapter:
        return cls(HidApiTransport.open_path(device.path), device)

    def capabilities(self) -> AdapterCapabilities:
        if self._capabilities is not None:
            return self._capabilities
        protocol = self._transact(
            [0xA0], lambda report: len(report) >= 4 and report[0] == 0xA0
        )
        if protocol[1] == 0:
            raise CapabilityError("Keychron protocol version is unavailable.")
        if self._led_count() != EXPECTED_LED_COUNT:
            raise CapabilityError("Device does not expose the expected 87 LEDs.")
        try:
            self._frame_status()
        except TransportError as error:
            raise CapabilityError(
                "Abralia effect-25 frame control is unavailable."
            ) from error
        self._capabilities = AdapterCapabilities(True, True, True, True, 2.0)
        return self._capabilities

    def _transact(self, request: list[int], matcher: Callable[[bytes], bool]) -> bytes:
        return self.transport.transact(request, matcher)

    def _rgb(self, command: int, payload: list[int] | None = None) -> bytes:
        response = self._transact(
            [0xA8, command, *(payload or [])],
            lambda report: (
                len(report) >= 3 and report[0] == 0xA8 and report[1] == command
            ),
        )
        if response[2] != 0:
            raise TransportError(f"Keychron RGB command 0x{command:02X} was rejected.")
        return response

    def _led_count(self) -> int:
        return self._rgb(0x05)[3]

    def _effect(self) -> int:
        return self._via_get(0x02)

    def _brightness(self) -> int:
        return self._via_get(0x01)

    def _via_get(self, value_id: int) -> int:
        return self._transact(
            [0x08, 0x03, value_id],
            lambda report: (
                len(report) >= 4 and report[:3] == bytes([0x08, 0x03, value_id])
            ),
        )[3]

    def _via_set(self, value_id: int, value: int) -> None:
        self._transact(
            [0x07, 0x03, value_id, value],
            lambda report: (
                len(report) >= 4 and report[:3] == bytes([0x07, 0x03, value_id])
            ),
        )

    def _per_key_type(self) -> int:
        return self._rgb(0x07)[3]

    def _set_per_key_type(self, value: int) -> None:
        self._rgb(0x08, [value])

    def _get_colors(self, start: int, count: int) -> tuple[Hsv8, ...]:
        response = self._rgb(0x09, [start, count])
        return tuple(
            Hsv8(*response[3 + offset * 3 : 6 + offset * 3]) for offset in range(count)
        )

    def _set_colors(self, start: int, colors: tuple[Hsv8, ...]) -> None:
        payload = [start, len(colors)]
        for color in colors:
            payload.extend([color.hue, color.saturation, color.value])
        self._rgb(0x0A, payload)

    def _write_colors(self, colors: tuple[Hsv8, ...]) -> None:
        for start in range(0, len(colors), MAX_COLORS_PER_PACKET):
            self._set_colors(start, colors[start : start + MAX_COLORS_PER_PACKET])

    def _frame_status(self) -> tuple[FrameState, int, FrameFlags, FrameResult]:
        response = self._transact(
            [0x08, 0x00, 0x01],
            lambda report: len(report) >= 8 and report[:3] == bytes([0x08, 0x00, 0x01]),
        )
        try:
            status = (
                FrameState(response[3]),
                response[4],
                FrameFlags(response[6]),
                FrameResult(response[7]),
            )
        except ValueError as error:
            raise TransportError(
                "Effect-25 status contains an unknown value."
            ) from error
        if status[3] is not FrameResult.OK:
            raise TransportError(f"Effect-25 status failed with {status[3].name}.")
        return status

    def keymap_layer_count(self) -> int:
        response = self._transact(
            [0x11],
            lambda report: len(report) >= 2 and report[0] == 0x11,
        )
        if response[1] == 0:
            raise TransportError("VIA reported zero dynamic keymap layers.")
        return response[1]

    def keymap_address_space(self) -> LiveKeymapAddressSpace:
        """Return the complete V3 8K firmware control address space."""

        return LiveKeymapAddressSpace(MATRIX_ROWS, MATRIX_COLUMNS, ENCODER_COUNT)

    def read_matrix_keycodes(
        self,
        layers: tuple[int, ...],
        *,
        rows: int,
        columns: int,
    ) -> Mapping[tuple[int, int, int], int]:
        if (rows, columns) != (MATRIX_ROWS, MATRIX_COLUMNS):
            raise CapabilityError(
                f"The effect-25 adapter expects a {MATRIX_ROWS}x{MATRIX_COLUMNS} matrix."
            )
        layer_size = rows * columns * 2
        values: dict[tuple[int, int, int], int] = {}
        for layer in layers:
            encoded = bytearray()
            for local_offset in range(0, layer_size, MAX_KEYMAP_BYTES_PER_PACKET):
                size = min(MAX_KEYMAP_BYTES_PER_PACKET, layer_size - local_offset)
                offset = layer * layer_size + local_offset
                prefix = bytes([0x12, offset >> 8, offset & 0xFF, size])
                response = self._transact(
                    list(prefix),
                    lambda report, expected=prefix: (
                        len(report) >= 4 and report[:4] == expected
                    ),
                )
                encoded.extend(response[4 : 4 + size])
            for row in range(rows):
                for column in range(columns):
                    offset = (row * columns + column) * 2
                    values[(layer, row, column)] = (
                        encoded[offset] << 8 | encoded[offset + 1]
                    )
        return values

    def read_encoder_keycodes(
        self,
        layers: tuple[int, ...],
        positions: tuple[EncoderPosition, ...],
    ) -> Mapping[tuple[int, EncoderPosition], int]:
        values: dict[tuple[int, EncoderPosition], int] = {}
        for layer in layers:
            for position in positions:
                clockwise = int(position.direction is EncoderDirection.CLOCKWISE)
                prefix = bytes([0x14, layer, position.index, clockwise])
                response = self._transact(
                    list(prefix),
                    lambda report, expected=prefix: (
                        len(report) >= 6 and report[:4] == expected
                    ),
                )
                values[(layer, position)] = response[4] << 8 | response[5]
        return values

    def _frame_control(
        self, operation: FrameOperation, sequence: int = 0
    ) -> tuple[FrameState, int, FrameFlags, FrameResult]:
        response = self._transact(
            [0x07, 0x00, 0x01, int(operation), sequence],
            lambda report: len(report) >= 8 and report[:3] == bytes([0x07, 0x00, 0x01]),
        )
        status = (
            FrameState(response[3]),
            response[4],
            FrameFlags(response[6]),
            FrameResult(response[7]),
        )
        if status[3] is not FrameResult.OK:
            raise TransportError(f"Effect-25 operation failed with {status[3].name}.")
        return status

    def _wait_for(
        self,
        predicate: Callable[[tuple[FrameState, int, FrameFlags, FrameResult]], bool],
        description: str,
        timeout: float = 1.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._frame_status()
            if predicate(status):
                return
            time.sleep(0.005)
        raise TransportError(f"Timed out waiting for {description}.")

    def _ensure_guarded(self) -> None:
        self.capabilities()
        effect = self._effect()
        if (
            self.effect_selection_policy is EffectSelectionPolicy.REQUIRE_SELECTED
            and effect != EFFECT_25
        ):
            raise EffectUnavailableError(
                "Effect 25 must be selected before submitting an RGB frame."
            )
        status = self._frame_status()
        if status[0] is FrameState.GUARDED:
            return
        if effect != EFFECT_25:
            self._via_set(0x02, EFFECT_25)
        status = self._frame_status()
        if status[0] is not FrameState.AWAITING:
            self._frame_control(FrameOperation.AWAIT)
            self._wait_for(lambda item: item[0] is FrameState.AWAITING, "AWAITING")
        self._frame_control(FrameOperation.BEGIN)
        self._wait_for(
            lambda item: (
                item[0] is FrameState.GUARDED
                and bool(item[2] & FrameFlags.BACK_BUFFER_FREE)
            ),
            "GUARDED back buffer",
        )

    def _raise_effect_aware(self, error: TransportError) -> None:
        if (
            self.effect_selection_policy is EffectSelectionPolicy.REQUIRE_SELECTED
            and self._effect() != EFFECT_25
        ):
            raise EffectUnavailableError(
                "Effect 25 became unavailable during an RGB operation."
            ) from error
        raise error

    def snapshot(self) -> DeviceSnapshot:
        frame_state = self._frame_status()[0]
        if frame_state is FrameState.GUARDED:
            raise CapabilityError(
                "The device already has an active guarded RGB session; refusing to preempt it."
            )
        colors: list[Hsv8] = []
        for start in range(0, EXPECTED_LED_COUNT, MAX_COLORS_PER_PACKET):
            colors.extend(
                self._get_colors(
                    start, min(MAX_COLORS_PER_PACKET, EXPECTED_LED_COUNT - start)
                )
            )
        return DeviceSnapshot(
            self.adapter_id,
            KeychronEffect25Snapshot(
                self._effect(),
                self._brightness(),
                self._per_key_type(),
                tuple(colors),
                frame_state,
            ),
        )

    def submit_frame(self, frame: DeviceFrame, *, brightness_ceiling: int) -> int:
        if not 0 <= brightness_ceiling <= 255:
            raise ValueError("brightness_ceiling must be in 0...255.")
        if [item.address for item in frame.leds] != list(range(EXPECTED_LED_COUNT)):
            raise CapabilityError("Keychron frames must contain all 87 ordered LEDs.")
        self._ensure_guarded()
        try:
            hsv = tuple(to_hsv8(item.color) for item in frame.leds)
            self._write_colors(hsv)
            sequence = self._sequence
            self._frame_control(FrameOperation.COMMIT, sequence)
            self._wait_for(
                lambda item: (
                    item[1] == sequence
                    and bool(item[2] & FrameFlags.ACTIVE_VALID)
                    and not bool(item[2] & FrameFlags.PENDING_VALID)
                ),
                f"active frame {sequence}",
            )
            maximum = max((color.value for color in hsv), default=0)
            global_brightness = (
                brightness_ceiling if maximum == 0 else min(maximum, brightness_ceiling)
            )
            self._via_set(0x01, global_brightness)
        except TransportError as error:
            self._raise_effect_aware(error)
        self._sequence = (self._sequence + 1) & 0xFF
        self._last_frame = frame
        self._brightness_ceiling = brightness_ceiling
        return sequence

    def refresh(self) -> int:
        if self._last_frame is None:
            raise TransportError("No frame is available to refresh.")
        sequence = self._sequence
        try:
            self._frame_control(FrameOperation.COMMIT, sequence)
            self._wait_for(
                lambda item: (
                    item[1] == sequence
                    and bool(item[2] & FrameFlags.ACTIVE_VALID)
                    and not bool(item[2] & FrameFlags.PENDING_VALID)
                ),
                f"refreshed frame {sequence}",
            )
        except TransportError as error:
            self._raise_effect_aware(error)
        self._sequence = (self._sequence + 1) & 0xFF
        return sequence

    def clear(self) -> None:
        self.submit_frame(
            DeviceFrame(
                tuple(LedColor(index, BLACK) for index in range(EXPECTED_LED_COUNT))
            ),
            brightness_ceiling=self._brightness_ceiling,
        )

    def health(self) -> AdapterHealth:
        try:
            self._frame_status()
            return AdapterHealth(True, "effect-25 frame status available")
        except TransportError as error:
            return AdapterHealth(False, str(error))

    def restore(self, snapshot: DeviceSnapshot) -> None:
        if snapshot.adapter_id != self.adapter_id or not isinstance(
            snapshot.payload, KeychronEffect25Snapshot
        ):
            raise TransportError("Snapshot belongs to a different adapter.")
        state = snapshot.payload
        self._via_set(0x01, 0)
        current_frame_state = self._frame_status()[0]
        if current_frame_state is not FrameState.AWAITING:
            self._frame_control(FrameOperation.AWAIT)
            self._wait_for(
                lambda item: item[0] is FrameState.AWAITING, "AWAITING restore state"
            )
        self._write_colors(state.colors)
        self._set_per_key_type(state.per_key_type)
        self._via_set(0x02, state.effect)
        if state.effect == EFFECT_25 and state.frame_state is FrameState.DIRECT:
            self._frame_control(FrameOperation.DIRECT)
            self._wait_for(
                lambda item: item[0] is FrameState.DIRECT, "DIRECT restore state"
            )
        self._via_set(0x01, state.brightness)
        restored = self.snapshot()
        if restored != snapshot:
            raise TransportError("Restoration readback differs from the snapshot.")
        self._last_frame = None

    def restore_preserving_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot:
        """Restore RGB payload state without undoing the user's selected effect."""

        if snapshot.adapter_id != self.adapter_id or not isinstance(
            snapshot.payload, KeychronEffect25Snapshot
        ):
            raise TransportError("Snapshot belongs to a different adapter.")
        state = snapshot.payload
        selected_effect = self._effect()
        self._via_set(0x01, 0)
        current_frame_state = self._frame_status()[0]
        if current_frame_state is not FrameState.AWAITING:
            if selected_effect != EFFECT_25:
                raise TransportError(
                    "Effect-25 frame state did not reset after effect deselection."
                )
            self._frame_control(FrameOperation.AWAIT)
            self._wait_for(
                lambda item: item[0] is FrameState.AWAITING,
                "AWAITING handoff state",
            )
        self._write_colors(state.colors)
        self._set_per_key_type(state.per_key_type)
        self._via_set(0x01, state.brightness)
        expected = DeviceSnapshot(
            self.adapter_id,
            KeychronEffect25Snapshot(
                selected_effect,
                state.brightness,
                state.per_key_type,
                state.colors,
                FrameState.AWAITING,
            ),
        )
        restored = self.snapshot()
        if restored != expected:
            raise TransportError(
                "Effect-preserving restoration readback differs from the snapshot."
            )
        self._last_frame = None
        return restored

    def rebase_current_effect(self, snapshot: DeviceSnapshot) -> DeviceSnapshot:
        """Record a user-selected effect without changing RGB payload state."""

        if snapshot.adapter_id != self.adapter_id or not isinstance(
            snapshot.payload, KeychronEffect25Snapshot
        ):
            raise TransportError("Snapshot belongs to a different adapter.")
        state = snapshot.payload
        return DeviceSnapshot(
            self.adapter_id,
            KeychronEffect25Snapshot(
                self._effect(),
                state.brightness,
                state.per_key_type,
                state.colors,
                FrameState.AWAITING,
            ),
        )

    def close(self) -> None:
        if not self._closed:
            self.transport.close()
            self._closed = True
