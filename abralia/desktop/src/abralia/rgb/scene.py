# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Diagram-aligned scene builders and hardware-neutral scene models."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from .colors import BLACK, Color, Hsv8, Srgb8, parse_color, to_srgb8
from .errors import MappingError


class MappingStrategy(str, Enum):
    GEOMETRY_RESAMPLE = "geometry_resample"
    ROW_KEY_INDEX = "row_key_index"
    ANCHORED_ROW_GRID = "anchored_row_grid"


@dataclass(frozen=True, slots=True)
class Canvas:
    width: int
    height: int
    cells: tuple[Color, ...]

    def __post_init__(self) -> None:
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or self.width <= 0
            or self.height <= 0
        ):
            raise MappingError("Canvas width and height must be positive.")
        if len(self.cells) != self.width * self.height:
            raise MappingError("Canvas cell count does not match width × height.")
        if any(not isinstance(color, (Srgb8, Hsv8)) for color in self.cells):
            raise MappingError("Canvas cells must be Srgb8 or Hsv8 values.")

    def color_at(self, row: int, column: int) -> Color:
        return self.cells[row * self.width + column]


@dataclass(frozen=True, slots=True)
class PhysicalSceneRequest:
    colors: Mapping[str, Color]
    background: Color = BLACK
    complete: bool = True


@dataclass(frozen=True, slots=True)
class CanvasSceneRequest:
    canvas: Canvas
    target: str
    strategy: MappingStrategy


@dataclass(frozen=True, slots=True)
class SemanticSceneRequest:
    region: str
    slot: str
    color: Color


ScenePayload: TypeAlias = (
    PhysicalSceneRequest | CanvasSceneRequest | SemanticSceneRequest
)


@dataclass(frozen=True, slots=True)
class AbstractScene:
    scene_id: str
    payload: ScenePayload
    owner: str
    priority: int = 0
    expires_at: float | None = None
    created_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() >= self.expires_at


@dataclass(frozen=True, slots=True)
class ResolvedVisual:
    element_id: str
    color: Srgb8
    owner: str
    priority: int
    provenance: str


@dataclass(frozen=True, slots=True)
class ResolvedScene:
    scene_id: str
    visuals: tuple[ResolvedVisual, ...]


@dataclass(frozen=True, slots=True)
class PhysicalFrame:
    colors: Mapping[str, Srgb8]


class PhysicalSceneBuilder:
    def build(
        self,
        scene_id: str,
        colors: Mapping[str, Color | Mapping[str, object]],
        *,
        background: Color | Mapping[str, object] = BLACK,
        owner: str = "caller",
        priority: int = 0,
        expires_at: float | None = None,
    ) -> AbstractScene:
        return AbstractScene(
            scene_id=scene_id,
            payload=PhysicalSceneRequest(
                {key: parse_color(value) for key, value in colors.items()},
                parse_color(background),
                True,
            ),
            owner=owner,
            priority=priority,
            expires_at=expires_at,
        )


class PhysicalOverlaySceneBuilder:
    """Build an explicit sparse overlay; the mapped device frame remains complete."""

    def build(
        self,
        scene_id: str,
        colors: Mapping[str, Color | Mapping[str, object]],
        *,
        owner: str,
        priority: int = 0,
        expires_at: float | None = None,
    ) -> AbstractScene:
        return AbstractScene(
            scene_id=scene_id,
            payload=PhysicalSceneRequest(
                {key: parse_color(value) for key, value in colors.items()},
                BLACK,
                False,
            ),
            owner=owner,
            priority=priority,
            expires_at=expires_at,
        )


class RectangularSceneBuilder:
    def build(
        self,
        scene_id: str,
        canvas: Canvas,
        *,
        target: str,
        strategy: MappingStrategy | str,
        owner: str = "caller",
        priority: int = 0,
        expires_at: float | None = None,
    ) -> AbstractScene:
        return AbstractScene(
            scene_id=scene_id,
            payload=CanvasSceneRequest(canvas, target, MappingStrategy(strategy)),
            owner=owner,
            priority=priority,
            expires_at=expires_at,
        )


class SemanticRegionSceneBuilder:
    """Typed extension point; v1 ships no built-in semantic vocabulary."""

    def build(
        self,
        scene_id: str,
        *,
        region: str,
        slot: str,
        color: Color | Mapping[str, object],
        owner: str,
        priority: int = 0,
        expires_at: float | None = None,
    ) -> AbstractScene:
        return AbstractScene(
            scene_id=scene_id,
            payload=SemanticSceneRequest(region, slot, parse_color(color)),
            owner=owner,
            priority=priority,
            expires_at=expires_at,
        )


def complete_frame(
    element_ids: tuple[str, ...], scene: PhysicalSceneRequest
) -> PhysicalFrame:
    unknown = set(scene.colors) - set(element_ids)
    if unknown:
        raise MappingError(f"Unknown physical elements: {sorted(unknown)!r}")
    background = to_srgb8(scene.background)
    return PhysicalFrame(
        {
            element_id: to_srgb8(scene.colors.get(element_id, background))
            for element_id in element_ids
        }
    )
