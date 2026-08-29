# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Synchronous RGB and Host Interaction standby coordination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .interaction import (
    DeviceEvent,
    EventType,
    HostInteractionProtocolClient,
    Response,
    StatusFlags,
)
from .rgb import RgbController


class SharedKeyboardState(Enum):
    RGB_SUSPENDED = "rgb_suspended"
    STANDBY = "standby"
    ACTIVE = "active"


class RgbProducerLifecycle(Protocol):
    def suspend(self) -> None: ...

    def resume_standby(self, *, restart: bool) -> None: ...

    def resume_active(self, *, restart: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class CoordinatorTransition:
    previous: SharedKeyboardState
    current: SharedKeyboardState
    restart_animation: bool


class SharedKeyboardCoordinator:
    """Coordinate producer lifecycle without merging either controller API."""

    def __init__(
        self,
        rgb: RgbController,
        interaction: HostInteractionProtocolClient,
        producer: RgbProducerLifecycle,
    ):
        self.rgb = rgb
        self.interaction = interaction
        self.producer = producer
        self.state = SharedKeyboardState.RGB_SUSPENDED
        self.rgb_effect25_selected = False
        self.interaction_active = False
        self._initialized = False

    @staticmethod
    def _active_from_status(status: Response) -> bool:
        active_flags = (
            StatusFlags.MANUAL_ACTIVE
            | StatusFlags.FORCE_ALL
            | StatusFlags.FORCE_SELECTED
        )
        return bool(status.status_flags & active_flags)

    def initialize(self, status: Response | None = None) -> CoordinatorTransition:
        current = status or self.interaction.get_status()
        self.rgb_effect25_selected = bool(
            current.status_flags & StatusFlags.RGB_EFFECT_25_SELECTED
        )
        self.interaction_active = self._active_from_status(current)
        if not self.rgb_effect25_selected:
            transition = self._enter_suspended()
        else:
            self.rgb.resume_output()
            transition = (
                self._enter_active()
                if self.interaction_active
                else self._enter_standby()
            )
        self._initialized = True
        return transition

    def handle_event(self, event: DeviceEvent) -> CoordinatorTransition | None:
        if not self._initialized:
            raise RuntimeError("SharedKeyboardCoordinator must be initialized.")
        if event.event_type is EventType.RGB_EFFECT_CHANGED:
            selected = event.rgb_effect25_selected
            if selected == self.rgb_effect25_selected:
                return None
            self.rgb_effect25_selected = selected
            if not self.rgb_effect25_selected:
                self.interaction_active = False
                return self._enter_suspended()
            self.interaction_active = False
            self.rgb.resume_output()
            return self._enter_standby()
        if event.event_type is EventType.MODE_CHANGED:
            active = event.mode_active
            if active == self.interaction_active:
                return None
            self.interaction_active = active
            if not self.rgb_effect25_selected:
                return None
            if self.interaction_active:
                return self._enter_active()
            return self._enter_standby()
        if event.event_type is EventType.QUEUE_OVERFLOW:
            self.interaction_active = False
            return self._enter_suspended()
        return None

    def suspend_for_session_loss(self) -> CoordinatorTransition:
        self.interaction_active = False
        return self._enter_suspended()

    def _transition(
        self, state: SharedKeyboardState, callback: str
    ) -> CoordinatorTransition:
        previous = self.state
        self.state = state
        changed = not self._initialized or previous is not state
        if changed:
            if callback == "suspend":
                self.producer.suspend()
            elif callback == "standby":
                self.producer.resume_standby(restart=True)
            else:
                self.producer.resume_active(restart=True)
        return CoordinatorTransition(
            previous,
            state,
            changed and state is not SharedKeyboardState.RGB_SUSPENDED,
        )

    def _enter_suspended(self) -> CoordinatorTransition:
        transition = self._transition(SharedKeyboardState.RGB_SUSPENDED, "suspend")
        self.rgb.suspend_output()
        return transition

    def _enter_standby(self) -> CoordinatorTransition:
        return self._transition(SharedKeyboardState.STANDBY, "standby")

    def _enter_active(self) -> CoordinatorTransition:
        return self._transition(SharedKeyboardState.ACTIVE, "active")
