# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Layout compatibility and scene compilation layer from the RGB diagram."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .colors import BLACK, Color, Srgb8, to_srgb8, weighted_average
from .errors import CapabilityError, MappingError
from .profiles import LayoutProfile, PhysicalElement, RegionTarget
from .scene import (
    AbstractScene,
    Canvas,
    CanvasSceneRequest,
    MappingStrategy,
    PhysicalSceneRequest,
    ResolvedScene,
    ResolvedVisual,
    SemanticSceneRequest,
)


@dataclass(frozen=True, slots=True)
class MappingReport:
    uncovered_cells: tuple[tuple[int, int], ...] = ()
    uncovered_elements: tuple[str, ...] = ()
    merged_elements: tuple[str, ...] = ()
    duplicated_cells: tuple[tuple[int, int], ...] = ()
    large_key_selections: tuple[tuple[str, int, int], ...] = ()
    unrenderable_controls: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    per_key_rgb: bool
    independent_brightness: bool
    guarded_frames: bool
    local_animation: bool = False
    lease_timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TemporaryBinding:
    region: str
    slot: str
    elements: tuple[str, ...]
    owner: str
    priority: int
    expires_at: float | None


@dataclass(slots=True)
class TemporaryBindingOverlay:
    _bindings: dict[tuple[str, str, str], TemporaryBinding] = field(
        default_factory=dict
    )

    def bind(self, binding: TemporaryBinding) -> None:
        self._bindings[(binding.region, binding.slot, binding.owner)] = binding

    def clear_owner(self, owner: str) -> None:
        self._bindings = {
            key: value for key, value in self._bindings.items() if value.owner != owner
        }

    def clear(self) -> None:
        self._bindings.clear()

    def resolve(self, region: str, slot: str) -> tuple[TemporaryBinding, ...]:
        now = time.monotonic()
        return tuple(
            binding
            for binding in self._bindings.values()
            if binding.region == region
            and binding.slot == slot
            and (binding.expires_at is None or binding.expires_at > now)
        )


class CapabilityValidator:
    def validate(self, profile: LayoutProfile, adapter: AdapterCapabilities) -> None:
        required = profile.capabilities
        for name in (
            "per_key_rgb",
            "independent_brightness",
            "guarded_frames",
            "local_animation",
        ):
            if getattr(required, name) and not getattr(adapter, name):
                raise CapabilityError(
                    f"Profile requires unsupported capability {name!r}."
                )


class GenericRegionSlotResolver:
    def __init__(self, overlay: TemporaryBindingOverlay | None = None):
        self.overlay = overlay or TemporaryBindingOverlay()

    def resolve(
        self, profile: LayoutProfile, request: SemanticSceneRequest
    ) -> tuple[str, ...]:
        temporary = self.overlay.resolve(request.region, request.slot)
        if temporary:
            selected = max(temporary, key=lambda item: item.priority)
            return selected.elements
        slots = profile.semantic_bindings.get(request.region, {})
        if request.slot not in slots:
            raise MappingError(
                f"UNBOUND semantic slot {request.region!r}/{request.slot!r}."
            )
        return slots[request.slot]


def _intersection_area(
    element: PhysicalElement, column: int, row: int, origin_x: float, origin_y: float
) -> float:
    cell_x1, cell_y1 = origin_x + column, origin_y + row
    cell_x2, cell_y2 = cell_x1 + 1.0, cell_y1 + 1.0
    geometry = element.geometry
    x_overlap = max(
        0.0, min(geometry.x + geometry.width, cell_x2) - max(geometry.x, cell_x1)
    )
    y_overlap = max(
        0.0, min(geometry.y + geometry.height, cell_y2) - max(geometry.y, cell_y1)
    )
    return x_overlap * y_overlap


class RectangularSceneRasterizer:
    def rasterize(
        self,
        profile: LayoutProfile,
        request: CanvasSceneRequest,
    ) -> tuple[dict[str, Srgb8], MappingReport]:
        try:
            target = profile.regions[request.target]
        except KeyError as error:
            raise MappingError(f"Unknown canvas target {request.target!r}.") from error
        if request.strategy not in target.strategies:
            raise MappingError(
                f"Target {target.region_id!r} does not support {request.strategy.value!r}."
            )
        renderable = [
            element_id
            for element_id in target.elements
            if profile.element_by_id[element_id].rgb_capable
        ]
        if not renderable:
            raise CapabilityError(
                f"Canvas target {target.region_id!r} has no RGB-renderable controls."
            )
        if request.strategy is MappingStrategy.GEOMETRY_RESAMPLE:
            return self._geometry(profile, target, request.canvas)
        if request.strategy is MappingStrategy.ROW_KEY_INDEX:
            return self._rows(profile, target, request.canvas, anchored=False)
        return self._rows(profile, target, request.canvas, anchored=True)

    def _geometry(
        self, profile: LayoutProfile, target: RegionTarget, canvas: Canvas
    ) -> tuple[dict[str, Srgb8], MappingReport]:
        by_id = profile.element_by_id
        elements = [
            by_id[element_id]
            for element_id in target.elements
            if by_id[element_id].rgb_capable
        ]
        origin_x = min(element.geometry.x for element in elements)
        origin_y = min(element.geometry.y for element in elements)
        colors: dict[str, Srgb8] = {}
        used_by_cell: dict[tuple[int, int], int] = {}
        merged: list[str] = []
        large: list[tuple[str, int, int]] = []

        for element in elements:
            overlaps: list[tuple[Color, float, int, int]] = []
            for row in range(canvas.height):
                for column in range(canvas.width):
                    area = _intersection_area(element, column, row, origin_x, origin_y)
                    if area > 0:
                        overlaps.append(
                            (canvas.color_at(row, column), area, row, column)
                        )
            if not overlaps:
                continue
            if element.geometry.width >= target.large_key_threshold_u:
                point_x = (
                    element.led_point.x
                    if element.led_point is not None
                    else element.geometry.x + element.geometry.width / 2
                )
                point_y = (
                    element.led_point.y
                    if element.led_point is not None
                    else element.geometry.y + element.geometry.height / 2
                )
                selected = min(
                    overlaps,
                    key=lambda item: math.dist(
                        (origin_x + item[3] + 0.5, origin_y + item[2] + 0.5),
                        (point_x, point_y),
                    ),
                )
                colors[element.element_id] = to_srgb8(selected[0])
                used_by_cell[(selected[2], selected[3])] = (
                    used_by_cell.get((selected[2], selected[3]), 0) + 1
                )
                large.append((element.element_id, selected[2], selected[3]))
            else:
                colors[element.element_id] = weighted_average(
                    [(item[0], item[1]) for item in overlaps]
                )
                if len(overlaps) > 1:
                    merged.append(element.element_id)
                for _color, _area, row, column in overlaps:
                    used_by_cell[(row, column)] = used_by_cell.get((row, column), 0) + 1

        all_cells = {
            (row, column)
            for row in range(canvas.height)
            for column in range(canvas.width)
        }
        uncovered_elements = tuple(
            element.element_id
            for element in elements
            if element.element_id not in colors
        )
        return colors, MappingReport(
            uncovered_cells=tuple(sorted(all_cells - set(used_by_cell))),
            uncovered_elements=uncovered_elements,
            merged_elements=tuple(merged),
            duplicated_cells=tuple(
                sorted(cell for cell, count in used_by_cell.items() if count > 1)
            ),
            large_key_selections=tuple(large),
            unrenderable_controls=target.compatibility_issues,
        )

    def _rows(
        self,
        profile: LayoutProfile,
        target: RegionTarget,
        canvas: Canvas,
        *,
        anchored: bool,
    ) -> tuple[dict[str, Srgb8], MappingReport]:
        if not target.rows:
            raise MappingError(f"Target {target.region_id!r} has no row metadata.")
        if canvas.height != len(target.rows):
            raise MappingError("Canvas height must equal the target row count.")
        if anchored and len(target.anchored_rows) != len(target.rows):
            raise MappingError("Anchored-row target metadata is incomplete.")
        colors: dict[str, Srgb8] = {}
        uncovered: list[tuple[int, int]] = []
        for row_index, elements in enumerate(target.rows):
            if len(elements) > canvas.width:
                raise MappingError(
                    f"Canvas row {row_index} is shorter than the target row."
                )
            for column, element_id in enumerate(elements):
                if element_id is None:
                    continue
                element = profile.element_by_id[element_id]
                if anchored and element.geometry.width != 1.0:
                    raise MappingError(
                        f"Anchored row {row_index} crosses non-1U element {element_id!r}."
                    )
                if element.rgb_capable:
                    colors[element_id] = to_srgb8(canvas.color_at(row_index, column))
            uncovered.extend(
                (row_index, column) for column in range(len(elements), canvas.width)
            )
        return colors, MappingReport(
            uncovered_cells=tuple(uncovered),
            unrenderable_controls=target.compatibility_issues,
        )


class SceneCompiler:
    def __init__(
        self,
        *,
        rasterizer: RectangularSceneRasterizer | None = None,
        resolver: GenericRegionSlotResolver | None = None,
        validator: CapabilityValidator | None = None,
    ):
        self.rasterizer = rasterizer or RectangularSceneRasterizer()
        self.resolver = resolver or GenericRegionSlotResolver()
        self.validator = validator or CapabilityValidator()

    def compile(
        self,
        scene: AbstractScene,
        profile: LayoutProfile,
        capabilities: AdapterCapabilities,
    ) -> tuple[ResolvedScene, MappingReport]:
        if scene.expired:
            return ResolvedScene(scene.scene_id, ()), MappingReport()
        self.validator.validate(profile, capabilities)
        payload = scene.payload
        report = MappingReport()
        if isinstance(payload, PhysicalSceneRequest):
            background = to_srgb8(payload.background)
            unknown = set(payload.colors) - set(profile.element_by_id)
            if unknown:
                raise MappingError(f"Unknown elements: {sorted(unknown)!r}")
            unsupported = {
                element_id
                for element_id in payload.colors
                if not profile.element_by_id[element_id].rgb_capable
            }
            if unsupported:
                raise CapabilityError(
                    f"Elements have no RGB address: {sorted(unsupported)!r}"
                )
            if payload.complete:
                colors = {
                    element.element_id: to_srgb8(
                        payload.colors.get(element.element_id, background)
                    )
                    for element in profile.rgb_elements
                }
            else:
                colors = {}
                for element_id, color in payload.colors.items():
                    if not profile.element_by_id[element_id].rgb_capable:
                        raise CapabilityError(
                            f"Element {element_id!r} has no RGB address."
                        )
                    colors[element_id] = to_srgb8(color)
        elif isinstance(payload, CanvasSceneRequest):
            mapped, report = self.rasterizer.rasterize(profile, payload)
            target = profile.regions[payload.target]
            colors = {
                element_id: BLACK
                for element_id in target.elements
                if profile.element_by_id[element_id].rgb_capable
            }
            colors.update(mapped)
        else:
            elements = self.resolver.resolve(profile, payload)
            colors = {}
            for element_id in elements:
                try:
                    element = profile.element_by_id[element_id]
                except KeyError as error:
                    raise MappingError(
                        f"Semantic resolution returned unknown element {element_id!r}."
                    ) from error
                if not element.rgb_capable:
                    raise CapabilityError(f"Element {element_id!r} has no RGB address.")
                colors[element_id] = to_srgb8(payload.color)

        return (
            ResolvedScene(
                scene.scene_id,
                tuple(
                    ResolvedVisual(
                        element_id=element_id,
                        color=color,
                        owner=scene.owner,
                        priority=scene.priority,
                        provenance=type(payload).__name__,
                    )
                    for element_id, color in colors.items()
                ),
            ),
            report,
        )
