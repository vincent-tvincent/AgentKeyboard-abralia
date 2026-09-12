# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Physical mode-key hold recognition; no keycode lookup or synthesized input."""

# Profile element IDs identify physical positions, independent of OS keycodes.
PICKUP_KEY = 'SCREENSHOT'
MUTE_KEY = 'SCROLL_LOCK'
PAGE_KEYS = ('1', '2', '3', '4', '5', '6', '7', '8', '9', '0')

NAVIGATION_KEYS = {
    'PAGE_UP': 'page_previous', 'PAGE_DOWN': 'page_next',
    'HOME': 'first_agent', 'END': 'last_agent',
    'UP': 'page_previous', 'DOWN': 'page_next',
    'LEFT': 'slot_previous', 'RIGHT': 'slot_next',
}

# Movement direction is not positive/negative action semantics. Group by role.
NAVIGATION_COLOR_ROLES = {
    'page_previous': 'page', 'page_next': 'page',
    'slot_previous': 'slot', 'slot_next': 'slot',
    'first_agent': 'boundary', 'last_agent': 'boundary',
}


class ModeKeyHold:
    def __init__(self, position, hold_seconds=.8, double_tap_seconds=.3):
        self.position = position
        self.hold_seconds = hold_seconds
        self.double_tap_seconds = double_tap_seconds
        self.reset()

    def reset(self):
        self.down = None
        self.started_at = None
        self.duration = None
        self.short_up_at = None
        self.double_press = False
        self.fired = False
        self.other = None
        self.last_sample_at = None

    def sample(self, matrix, now):
        row, column = self.position
        pressed = bool(matrix[row] & (1 << column))
        if self.last_sample_at is not None and now - self.last_sample_at > .25:
            uncertain_press = bool(self.down or pressed)
            self.reset()
            self.down, self.fired, self.last_sample_at = pressed, uncertain_press, now
            return False  # A stalled read cannot establish how long the key was held.
        self.last_sample_at = now
        other = tuple(value & ~(1 << column) if i == row else value for i, value in enumerate(matrix))
        if self.other is not None and other != self.other:
            self.short_up_at = None
        self.other = other  # Ephemeral state only; never logged.
        if self.down is None:
            self.down = pressed
            return False  # Do not invent a DOWN when attaching mid-hold.
        if pressed and not self.down:
            self.started_at, self.duration, self.fired = now, None, False
            self.double_press = self.short_up_at is not None and now - self.short_up_at < self.double_tap_seconds
            self.short_up_at = None
        elif not pressed and self.down:
            self.duration = None if self.started_at is None else now - self.started_at
            self.short_up_at = (now if self.duration is not None and self.duration < self.double_tap_seconds
                                and not self.double_press and not self.fired else None)
        self.down = pressed
        return self._fire(now)

    def _fire(self, now):
        duration = now - self.started_at if self.down and self.started_at is not None else self.duration
        if not self.fired and not self.double_press and duration is not None and duration >= self.hold_seconds:
            self.fired = True
            return True
        return False

    def release_event(self, now):
        """Return (new hold, suppress short action) for a captured UP event."""
        if self.last_sample_at is not None and now - self.last_sample_at > .25 and self.down:
            self.fired = True
            return False, True
        fresh = self._fire(now)
        return fresh, self.fired
