# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import unittest

from abralia.coordinator import SharedKeyboardCoordinator, SharedKeyboardState
from abralia.interaction import (
    ControlId,
    DeviceEvent,
    EventFlags,
    EventType,
    Opcode,
    ResetReason,
    Response,
    Result,
    StatusFlags,
)


class FakeRgbController:
    def __init__(self) -> None:
        self.suspend_count = 0
        self.resume_count = 0

    def suspend_output(self) -> None:
        self.suspend_count += 1

    def resume_output(self) -> None:
        self.resume_count += 1


class FakeInteractionClient:
    def __init__(self, status: Response) -> None:
        self.status = status

    def get_status(self) -> Response:
        return self.status


class FakeProducer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    def suspend(self) -> None:
        self.calls.append(("suspend", None))

    def resume_standby(self, *, restart: bool) -> None:
        self.calls.append(("standby", restart))

    def resume_active(self, *, restart: bool) -> None:
        self.calls.append(("active", restart))


def response(flags: StatusFlags) -> Response:
    return Response(
        verb=0x08,
        opcode=Opcode.GET_STATUS,
        result=Result.OK,
        session_token=1,
        binding_generation=0,
        status_flags=flags,
        binding_count=0,
        forced_control_count=0,
        queued_event_count=0,
        last_reset_reason=ResetReason.NONE,
        force_generation=0,
        heartbeat_sequence=0,
    )


def event(event_type: EventType, state: bool) -> DeviceEvent:
    return DeviceEvent(
        event_type=event_type,
        session_token=1,
        sequence=1,
        binding_generation=0,
        binding_id=0,
        control_id=ControlId(0),
        edge_or_state=int(state),
        flags=EventFlags(0),
        timestamp_ms=1,
    )


class SharedKeyboardCoordinatorTests(unittest.TestCase):
    def make_coordinator(
        self, flags: StatusFlags
    ) -> tuple[SharedKeyboardCoordinator, FakeRgbController, FakeProducer]:
        rgb = FakeRgbController()
        producer = FakeProducer()
        coordinator = SharedKeyboardCoordinator(
            rgb, FakeInteractionClient(response(flags)), producer
        )
        return coordinator, rgb, producer

    def test_initial_effect25_state_resumes_desktop_controlled_standby(self) -> None:
        coordinator, rgb, producer = self.make_coordinator(
            StatusFlags.SESSION_VALID | StatusFlags.RGB_EFFECT_25_SELECTED
        )

        transition = coordinator.initialize()

        self.assertEqual(transition.current, SharedKeyboardState.STANDBY)
        self.assertTrue(transition.restart_animation)
        self.assertEqual(rgb.resume_count, 1)
        self.assertEqual(producer.calls, [("standby", True)])

    def test_effect_change_suspends_then_requires_new_activation(self) -> None:
        coordinator, rgb, producer = self.make_coordinator(
            StatusFlags.SESSION_VALID
            | StatusFlags.RGB_EFFECT_25_SELECTED
            | StatusFlags.MANUAL_ACTIVE
        )
        coordinator.initialize()

        unavailable = coordinator.handle_event(
            event(EventType.RGB_EFFECT_CHANGED, False)
        )
        mode_off = coordinator.handle_event(event(EventType.MODE_CHANGED, False))
        available = coordinator.handle_event(event(EventType.RGB_EFFECT_CHANGED, True))
        active = coordinator.handle_event(event(EventType.MODE_CHANGED, True))

        assert unavailable is not None
        assert available is not None
        assert active is not None
        self.assertEqual(unavailable.current, SharedKeyboardState.RGB_SUSPENDED)
        self.assertIsNone(mode_off)
        self.assertEqual(available.current, SharedKeyboardState.STANDBY)
        self.assertEqual(active.current, SharedKeyboardState.ACTIVE)
        self.assertEqual(rgb.suspend_count, 1)
        self.assertEqual(rgb.resume_count, 2)
        self.assertEqual(
            producer.calls,
            [
                ("active", True),
                ("suspend", None),
                ("standby", True),
                ("active", True),
            ],
        )

    def test_repeated_state_events_do_not_restart_the_producer(self) -> None:
        coordinator, _rgb, producer = self.make_coordinator(
            StatusFlags.SESSION_VALID | StatusFlags.RGB_EFFECT_25_SELECTED
        )
        coordinator.initialize()
        coordinator.handle_event(event(EventType.RGB_EFFECT_CHANGED, True))
        coordinator.handle_event(event(EventType.MODE_CHANGED, False))
        self.assertEqual(producer.calls, [("standby", True)])

    def test_session_loss_suspends_without_destroying_producer(self) -> None:
        coordinator, _rgb, producer = self.make_coordinator(
            StatusFlags.SESSION_VALID | StatusFlags.RGB_EFFECT_25_SELECTED
        )
        coordinator.initialize()

        transition = coordinator.suspend_for_session_loss()

        self.assertEqual(transition.current, SharedKeyboardState.RGB_SUSPENDED)
        self.assertEqual(producer.calls[-1], ("suspend", None))


if __name__ == "__main__":
    unittest.main()
