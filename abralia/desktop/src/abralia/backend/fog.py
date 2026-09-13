# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Deterministic host-side fog particles in keyboard-profile physical units.

Lifecycle and placement identity come from the broker. This module owns only
motion and light: it has no device, binding, thread-view or notification policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from collections.abc import Iterable, Mapping

from abralia.rgb import Srgb8
from abralia.rgb.colors import LinearRgb, linear_to_srgb8, to_linear_rgb


def _seed(key: str, part: int = 0) -> float:
    """Stable across Python processes and independent of registration order."""
    data = hashlib.blake2s(f"{key}:{part}".encode(), digest_size=8).digest()
    return int.from_bytes(data, "big") / (1 << 64)


def _color(value: Srgb8 | str) -> Srgb8:
    if isinstance(value, Srgb8):
        return value
    if isinstance(value, str):
        value = value.removeprefix("#")
        if len(value) == 6:
            try:
                return Srgb8(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))
            except ValueError:
                pass
    raise ValueError("fog identity_color must be Srgb8 or six hexadecimal digits")


@dataclass(slots=True)
class FogBody:
    key: str
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    phase: float
    color: Srgb8
    opacity: float
    quiet: bool

    @property
    def collision_radius(self) -> float:
        return self.radius * .4


class FogField:
    """Synchronize visual records, then sample a shared or individual fog body.

    ``update`` accepts mappings containing ``orb_key``, ``identity_color``,
    ``opacity`` and optional ``quiet``. Use an allocation generation in the key:
    changing a page or display position must not change it. Missing keys vanish
    immediately. Quiet bodies retain their tint with restrained visual density.

    ``sample`` combines light in linear RGB before returning an sRGB pixel.
    ``ceiling`` bounds the emitted fog, not the supplied background; at zero
    density the original background is returned exactly. The renderer retains
    ownership of reserved-key masks and its established whole-frame peak.
    """

    STEP = 1 / 60
    MAX_ELAPSED = .25

    def __init__(self, bounds: tuple[float, float, float, float], *, speed: float = .45,
                 radius: float = 2.2):
        if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
            raise ValueError("fog bounds must contain four finite coordinates")
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            raise ValueError("fog bounds must have positive width and height")
        if not math.isfinite(speed) or speed < 0 or not math.isfinite(radius) or radius <= 0:
            raise ValueError("fog speed and radius must be finite, with speed >= 0 and radius > 0")
        self.bounds = tuple(float(v) for v in bounds)
        self.speed = speed
        # A narrow profile still has room for its smaller collision core.
        self.radius = min(radius, (right - left) * .6, (bottom - top) * .6)
        self.bodies: dict[str, FogBody] = {}
        self._last_time: float | None = None
        self._remainder = 0.
        self.time = 0.

    def update(self, records: Iterable[Mapping], now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("fog time must be finite")
        parsed = {}
        for record in records:
            key = record["orb_key"]
            opacity = record["opacity"]
            if not isinstance(key, str) or not key:
                raise ValueError("fog orb_key must be a nonempty string")
            if key in parsed:
                raise ValueError("duplicate fog orb_key")
            if not isinstance(opacity, (int, float)) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
                raise ValueError("fog opacity must be finite and within 0..1")
            parsed[key] = (_color(record["identity_color"]), float(opacity), bool(record.get("quiet", False)))
        # Validate the complete snapshot before modifying the current field.
        self.bodies = {key:body for key, body in self.bodies.items() if key in parsed}
        for key in sorted(parsed):
            color, opacity, quiet = parsed[key]
            if key not in self.bodies:
                self.bodies[key] = self._spawn(key, color, opacity, quiet)
            else:
                body = self.bodies[key]
                body.color, body.opacity, body.quiet = color, opacity, quiet
        self.bodies = dict(sorted(self.bodies.items()))
        elapsed = 0. if self._last_time is None else max(0., min(self.MAX_ELAPSED, now - self._last_time))
        self._last_time = now if self._last_time is None else max(now, self._last_time)
        self._remainder += elapsed
        # Bounded fixed steps preserve ordinary frame-cadence independence and
        # avoid teleporting or expensive catch-up after suspend/debugger stalls.
        steps = min(16, int((self._remainder + 1e-10) / self.STEP))
        for _ in range(steps):
            self.time += self.STEP
            self._step()
        self._remainder = max(0., self._remainder - steps * self.STEP)

    def _spawn(self, key: str, color: Srgb8, opacity: float, quiet: bool) -> FogBody:
        left, top, right, bottom = self.bounds
        core = self.radius * .4
        # Choose the least crowded deterministic candidate. Existing bodies
        # never move simply because another notification was registered.
        candidates = [(left + core + _seed(key, 2 * i) * (right - left - 2 * core),
                       top + core + _seed(key, 2 * i + 1) * (bottom - top - 2 * core))
                      for i in range(32)]
        def clearance(point):
            return min((math.hypot(point[0] - b.x, point[1] - b.y)
                        for b in self.bodies.values()), default=math.inf)
        x, y = max(candidates, key=clearance)
        phase = _seed(key, 100) * math.tau
        return FogBody(key, x, y, math.cos(phase) * self.speed, math.sin(phase) * self.speed,
                       self.radius, phase, color, opacity, quiet)

    def _bound(self, body: FogBody) -> None:
        left, top, right, bottom = self.bounds
        r = body.collision_radius
        if body.x < left + r:
            body.x, body.vx = left + r, abs(body.vx)
        elif body.x > right - r:
            body.x, body.vx = right - r, -abs(body.vx)
        if body.y < top + r:
            body.y, body.vy = top + r, abs(body.vy)
        elif body.y > bottom - r:
            body.y, body.vy = bottom - r, -abs(body.vy)

    def _step(self) -> None:
        bodies = list(self.bodies.values())
        for body in bodies:
            bend = .16 * math.sin(self.time * .31 + body.phase) * self.STEP
            c, s = math.cos(bend), math.sin(bend)
            body.vx, body.vy = body.vx * c - body.vy * s, body.vx * s + body.vy * c
            body.x += body.vx * self.STEP
            body.y += body.vy * self.STEP
            self._bound(body)
        # Soft visual envelopes overlap; only the 40% cores repel. Resolve a
        # bounded number of passes even when a small layout is overcrowded.
        for _ in range(3):
            for i, a in enumerate(bodies):
                for b in bodies[i + 1:]:
                    dx, dy = b.x - a.x, b.y - a.y
                    distance = math.hypot(dx, dy)
                    limit = a.collision_radius + b.collision_radius
                    if distance >= limit:
                        continue
                    if distance < 1e-12:
                        angle = _seed(a.key + "\0" + b.key) * math.tau
                        nx, ny = math.cos(angle), math.sin(angle)
                    else:
                        nx, ny = dx / distance, dy / distance
                    overlap = (limit - distance) * .5
                    a.x, a.y = a.x - nx * overlap, a.y - ny * overlap
                    b.x, b.y = b.x + nx * overlap, b.y + ny * overlap
                    closing = (a.vx - b.vx) * nx + (a.vy - b.vy) * ny
                    if closing > 0:
                        a.vx, a.vy = a.vx - closing * nx, a.vy - closing * ny
                        b.vx, b.vy = b.vx + closing * nx, b.vy + closing * ny
                    self._bound(a)
                    self._bound(b)
        for body in bodies:
            magnitude = math.hypot(body.vx, body.vy)
            if magnitude > 1e-12:
                body.vx, body.vy = body.vx * self.speed / magnitude, body.vy * self.speed / magnitude
            else:
                body.vx, body.vy = math.cos(body.phase) * self.speed, math.sin(body.phase) * self.speed

    def _contribution(self, body: FogBody, x: float, y: float, spread: float = 1.):
        dx, dy = (x - body.x) / (body.radius * spread), (y - body.y) / (body.radius * .82 * spread)
        r = math.hypot(dx, dy)
        if body.opacity <= 0 or r > 3:
            return None
        angle = math.atan2(dy, dx)
        t = self.time
        turbulence = (.09 * math.sin(3.2 * dx + 2.7 * dy + .6 * t + body.phase)
                      + .055 * math.sin(5.1 * dy - 2.2 * dx - .43 * t + body.phase))
        warped = max(0., r + turbulence * min(1., r))
        volume = math.exp(-warped * warped / .62)
        shell = .16 * math.exp(-((warped - .7) / .16) ** 2)
        # A broad trailing wisp follows velocity analytically, without a frame
        # history buffer. It has the same task tint as the main body.
        velocity = max(math.hypot(body.vx, body.vy), 1e-12)
        tail_x = dx + .58 * body.vx / velocity
        tail_y = dy + .58 * body.vy / velocity
        trail = .13 * math.exp(-(tail_x * tail_x + tail_y * tail_y) / .65)
        shading = .78 + .22 * (.5 + .5 * math.cos(angle + .8))
        density = (volume * shading + shell + trail) * body.opacity * (.4 if body.quiet else 1.)
        # A small white component retains a luminous core without washing out
        # the task's identity color at the orb's center.
        core = .05 * math.exp(-r * r / .07)
        linear = to_linear_rgb(body.color)
        tint = tuple(channel + (1 - channel) * core for channel in (linear.red, linear.green, linear.blue))
        return density, tint

    def _layer(self, x: float, y: float, orb_key: str | None, exclude_key: str | None,
               spread: float, spread_key: str | None):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("fog sampling coordinates must be finite")
        if not math.isfinite(spread) or spread <= 0:
            raise ValueError("fog spread must be finite and positive")
        contributions = []
        for key, body in self.bodies.items():
            if key == exclude_key or (orb_key is not None and key != orb_key):
                continue
            expanding = spread_key if spread_key is not None else orb_key
            value = self._contribution(body, x, y, spread if key == expanding else 1.)
            if value is not None and value[0] > 0:
                contributions.append(value)
        density = math.fsum(weight for weight, _ in contributions)
        if density <= 0:
            return LinearRgb(0., 0., 0.), 0.
        color = LinearRgb(*(min(1., math.fsum(weight * tint[i] for weight, tint in contributions) / density)
                            for i in range(3)))
        return color, -math.expm1(-1.8 * density)

    def sample_layer(self, x: float, y: float, *, orb_key: str | None = None,
                     exclude_key: str | None = None, spread: float = 1.,
                     spread_key: str | None = None) -> tuple[Srgb8, float]:
        """Return tint/alpha; spread_key enlarges one body's light in the mix.

        With orb_key this can sample a single forming body. With spread_key,
        all bodies still mix together and spread=1 has the exact steady-field
        endpoint, avoiding an order-dependent alpha-compositing transition.
        """
        linear, alpha = self._layer(x, y, orb_key, exclude_key, spread, spread_key)
        return linear_to_srgb8(linear), alpha

    def sample(self, x: float, y: float, background: Srgb8, *, orb_key: str | None = None,
               exclude_key: str | None = None, ceiling: int = 255, spread: float = 1.,
               spread_key: str | None = None) -> Srgb8:
        if type(ceiling) is not int or not 0 <= ceiling <= 255:
            raise ValueError("fog ceiling must be an integer within 0..255")
        color, alpha = self._layer(x, y, orb_key, exclude_key, spread, spread_key)
        if alpha == 0:
            return background
        # Map full-range sRGB emission to the allowed encoded frame peak first;
        # then use linear-light alpha composition against the existing pixel.
        emitted = linear_to_srgb8(color)
        bounded = Srgb8(*(round(c * ceiling / 255) for c in (emitted.red, emitted.green, emitted.blue)))
        emission, base = to_linear_rgb(bounded), to_linear_rgb(background)
        return linear_to_srgb8(LinearRgb(*(min(1., max(0., a * (1 - alpha) + b * alpha))
            for a, b in zip((base.red, base.green, base.blue), (emission.red, emission.green, emission.blue)))))
