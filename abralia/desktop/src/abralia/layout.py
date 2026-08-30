# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Shared user compatibility layouts for RGB and Host Interaction."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path

import jsonschema

from .device_profile import load_schema
from .interaction.protocol import ControlId, ControlKind
from .rgb.profiles import LayoutProfile, PhysicalElement, RegionTarget
from .rgb.scene import MappingStrategy


class CompatibilityLayoutError(ValueError):
    """A compatibility overlay or imported alias file is invalid."""


@dataclass(frozen=True, slots=True)
class ResolvedControl:
    control_id: ControlId
    element_id: str | None
    led_address: int | None
    source: str
    rgb_issue: str | None = None

    @property
    def display_label(self) -> str:
        return self.element_id or str(self.control_id)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "control_id": str(self.control_id),
            "source": self.source,
        }
        if self.control_id.kind is ControlKind.KEY:
            data["matrix"] = [self.control_id.primary, self.control_id.secondary]
        else:
            data["encoder"] = {
                "index": self.control_id.primary,
                "direction": (
                    "clockwise"
                    if self.control_id.kind is ControlKind.ENCODER_CW
                    else "counterclockwise"
                ),
            }
        data["element_id"] = self.element_id
        data["led_address"] = self.led_address
        data["rgb_issue"] = self.rgb_issue
        return data


@dataclass(frozen=True, slots=True)
class ResolvedRegion:
    region_id: str
    rows: tuple[tuple[ResolvedControl, ...], ...]
    strategies: frozenset[MappingStrategy]
    large_key_threshold_u: float = 4.0

    @property
    def controls(self) -> tuple[ControlId, ...]:
        return tuple(control.control_id for row in self.rows for control in row)

    @property
    def rgb_issues(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (control.display_label, control.rgb_issue)
            for row in self.rows
            for control in row
            if control.rgb_issue is not None
        )

    def to_region_target(self) -> RegionTarget:
        element_ids: list[str] = []
        for row in self.rows:
            for control in row:
                if (
                    control.element_id is not None
                    and control.element_id not in element_ids
                ):
                    element_ids.append(control.element_id)
        anchored_rows: tuple[str, ...] = ()
        if MappingStrategy.ANCHORED_ROW_GRID in self.strategies:
            anchored_rows = tuple(
                row[0].element_id for row in self.rows if row[0].element_id is not None
            )
        return RegionTarget(
            region_id=self.region_id,
            elements=tuple(element_ids),
            rows=tuple(
                tuple(control.element_id for control in row) for row in self.rows
            ),
            strategies=self.strategies,
            large_key_threshold_u=self.large_key_threshold_u,
            anchored_rows=anchored_rows,
            compatibility_issues=self.rgb_issues,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.region_id,
            "rows": [[control.to_dict() for control in row] for row in self.rows],
            "strategies": sorted(strategy.value for strategy in self.strategies),
            "large_key_threshold_u": self.large_key_threshold_u,
        }


@dataclass(frozen=True, slots=True)
class ResolvedCompatibilityLayout:
    profile_id: str
    matrix_aliases: Mapping[str, tuple[int, int]]
    alias_sources: Mapping[str, str]
    regions: Mapping[str, ResolvedRegion]

    def region(self, region_id: str) -> ResolvedRegion:
        try:
            return self.regions[region_id]
        except KeyError as error:
            raise CompatibilityLayoutError(
                f"Unknown compatibility region {region_id!r}."
            ) from error

    def apply_to_profile(self, profile: LayoutProfile) -> LayoutProfile:
        if profile.profile_id != self.profile_id:
            raise CompatibilityLayoutError(
                f"Compatibility layout targets {self.profile_id!r}, not {profile.profile_id!r}."
            )
        regions = dict(profile.regions)
        regions.update(
            {
                region_id: region.to_region_target()
                for region_id, region in self.regions.items()
            }
        )
        return replace(profile, regions=regions)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "matrix_aliases": {
                alias: {
                    "matrix": list(position),
                    "source": self.alias_sources[alias],
                }
                for alias, position in sorted(self.matrix_aliases.items())
            },
            "regions": [
                self.regions[region_id].to_dict() for region_id in sorted(self.regions)
            ],
        }


def _validate_schema(data: object, schema_name: str, source: str) -> None:
    try:
        jsonschema.Draft202012Validator(load_schema(schema_name)).validate(data)
    except jsonschema.ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path) or "document"
        raise CompatibilityLayoutError(f"{source}: {path}: {error.message}") from error


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CompatibilityLayoutError(f"Could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise CompatibilityLayoutError(f"Invalid JSON in {path}: {error}") from error


def _matrix_position(
    profile: LayoutProfile, value: object, *, source: str
) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise CompatibilityLayoutError(
            f"{source}: matrix position must be [row, column]."
        )
    row, column = value
    if (
        isinstance(row, bool)
        or isinstance(column, bool)
        or not isinstance(row, int)
        or not isinstance(column, int)
    ):
        raise CompatibilityLayoutError(
            f"{source}: matrix coordinates must be integers."
        )
    if (
        not 0 <= row < profile.keymap.matrix_rows
        or not 0 <= column < profile.keymap.matrix_columns
    ):
        raise CompatibilityLayoutError(
            f"{source}: matrix [{row},{column}] is outside the "
            f"{profile.keymap.matrix_rows}x{profile.keymap.matrix_columns} profile matrix."
        )
    return row, column


def _control_for_element(element: PhysicalElement) -> ControlId:
    if element.matrix is not None:
        return ControlId.key(*element.matrix)
    if element.encoder is not None:
        if element.encoder.direction.value == "clockwise":
            return ControlId.encoder_clockwise(element.encoder.index)
        return ControlId.encoder_counterclockwise(element.encoder.index)
    raise CompatibilityLayoutError(
        f"Element {element.element_id!r} has no matrix or encoder control address."
    )


def _resolved_control(
    control_id: ControlId,
    element_by_control: Mapping[ControlId, PhysicalElement],
    *,
    source: str,
) -> ResolvedControl:
    element = element_by_control.get(control_id)
    issue = None
    if element is None:
        issue = "profile_missing"
    elif not element.rgb_capable:
        issue = "no_rgb_address"
    return ResolvedControl(
        control_id=control_id,
        element_id=element.element_id if element is not None else None,
        led_address=element.led_address if element is not None else None,
        source=source,
        rgb_issue=issue,
    )


def _validate_anchored_rows(
    region_id: str,
    rows: tuple[tuple[ResolvedControl, ...], ...],
    element_by_id: Mapping[str, PhysicalElement],
) -> None:
    for row_index, row in enumerate(rows):
        elements: list[PhysicalElement] = []
        for control in row:
            if control.element_id is None:
                raise CompatibilityLayoutError(
                    f"Region {region_id!r} anchored row {row_index} contains "
                    f"profile-missing control {control.control_id}."
                )
            elements.append(element_by_id[control.element_id])
        if any(
            element.geometry.width != 1.0 or element.geometry.height != 1.0
            for element in elements
        ):
            raise CompatibilityLayoutError(
                f"Region {region_id!r} anchored row {row_index} contains a non-1U element."
            )
        if any(
            right.geometry.x != left.geometry.x + 1
            or right.geometry.y != left.geometry.y
            for left, right in pairwise(elements)
        ):
            raise CompatibilityLayoutError(
                f"Region {region_id!r} anchored row {row_index} contains a gap."
            )


def load_compatibility_layout(
    profile: LayoutProfile, source: str | Path
) -> ResolvedCompatibilityLayout:
    overlay_path = Path(source).expanduser().resolve()
    data = _read_json(overlay_path)
    _validate_schema(data, "compatibility-layout-v1.schema.json", str(overlay_path))
    assert isinstance(data, dict)
    if data["profile_id"] != profile.profile_id:
        raise CompatibilityLayoutError(
            f"{overlay_path}: profile_id {data['profile_id']!r} does not match "
            f"{profile.profile_id!r}."
        )

    aliases: dict[str, tuple[int, int]] = {}
    alias_sources: dict[str, str] = {}

    def add_aliases(values: object, source_label: str) -> None:
        assert isinstance(values, dict)
        for alias, raw_position in values.items():
            if alias in aliases:
                raise CompatibilityLayoutError(
                    f"Alias {alias!r} is defined more than once "
                    f"({alias_sources[alias]} and {source_label})."
                )
            aliases[alias] = _matrix_position(
                profile, raw_position, source=f"{source_label}:{alias}"
            )
            alias_sources[alias] = source_label

    base_directory = overlay_path.parent.resolve()
    for import_value in data.get("alias_imports", []):
        assert isinstance(import_value, str)
        if "://" in import_value or Path(import_value).is_absolute():
            raise CompatibilityLayoutError(
                f"Alias import {import_value!r} must be a relative local path."
            )
        imported_path = (base_directory / import_value).resolve()
        if not imported_path.is_relative_to(base_directory):
            raise CompatibilityLayoutError(
                f"Alias import {import_value!r} escapes the overlay directory."
            )
        imported = _read_json(imported_path)
        _validate_schema(imported, "matrix-aliases-v1.schema.json", str(imported_path))
        assert isinstance(imported, dict)
        if imported["profile_id"] != profile.profile_id:
            raise CompatibilityLayoutError(
                f"{imported_path}: profile_id {imported['profile_id']!r} does not "
                f"match {profile.profile_id!r}."
            )
        add_aliases(imported["matrix_aliases"], import_value)

    add_aliases(data.get("matrix_aliases", {}), overlay_path.name)

    element_by_control = {
        _control_for_element(element): element
        for element in profile.elements
        if element.matrix is not None or element.encoder is not None
    }
    element_by_id = profile.element_by_id
    regions: dict[str, ResolvedRegion] = {}
    for region_data in data["regions"]:
        assert isinstance(region_data, dict)
        region_id = region_data["id"]
        if region_id in regions:
            raise CompatibilityLayoutError(
                f"Compatibility region IDs must be unique; repeated {region_id!r}."
            )
        resolved_rows: list[tuple[ResolvedControl, ...]] = []
        seen: set[ControlId] = set()
        for row_index, row_data in enumerate(region_data["rows"]):
            row: list[ResolvedControl] = []
            for column_index, selector in enumerate(row_data):
                selector_source = (
                    f"region {region_id!r} row {row_index} column {column_index}"
                )
                if isinstance(selector, str):
                    try:
                        matrix = aliases[selector]
                    except KeyError as error:
                        raise CompatibilityLayoutError(
                            f"{selector_source}: undefined matrix alias {selector!r}."
                        ) from error
                    control = _resolved_control(
                        ControlId.key(*matrix),
                        element_by_control,
                        source=f"alias:{selector}@{alias_sources[selector]}",
                    )
                elif "matrix" in selector:
                    matrix = _matrix_position(
                        profile, selector["matrix"], source=selector_source
                    )
                    control = _resolved_control(
                        ControlId.key(*matrix),
                        element_by_control,
                        source=f"matrix[{matrix[0]},{matrix[1]}]",
                    )
                else:
                    requested = selector["element"]
                    element_id = profile.resolve_element_id(requested)
                    try:
                        element = element_by_id[element_id]
                    except KeyError as error:
                        raise CompatibilityLayoutError(
                            f"{selector_source}: unknown physical element {requested!r}."
                        ) from error
                    control_id = _control_for_element(element)
                    control = _resolved_control(
                        control_id,
                        element_by_control,
                        source=f"element:{element_id}",
                    )
                if control.control_id in seen:
                    raise CompatibilityLayoutError(
                        f"Region {region_id!r} resolves control {control.control_id} more than once."
                    )
                seen.add(control.control_id)
                row.append(control)
            resolved_rows.append(tuple(row))

        rows = tuple(resolved_rows)
        strategies = frozenset(
            MappingStrategy(value) for value in region_data["strategies"]
        )
        if MappingStrategy.ANCHORED_ROW_GRID in strategies:
            _validate_anchored_rows(region_id, rows, element_by_id)
        regions[region_id] = ResolvedRegion(
            region_id=region_id,
            rows=rows,
            strategies=strategies,
            large_key_threshold_u=float(region_data.get("large_key_threshold_u", 4.0)),
        )

    return ResolvedCompatibilityLayout(
        profile_id=profile.profile_id,
        matrix_aliases=aliases,
        alias_sources=alias_sources,
        regions=regions,
    )
