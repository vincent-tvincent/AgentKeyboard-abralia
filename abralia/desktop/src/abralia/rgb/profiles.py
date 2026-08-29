# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Versioned JSON profile loading and semantic validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from itertools import pairwise
from pathlib import Path

import jsonschema

from .errors import ProfileValidationError
from .scene import MappingStrategy

DEFAULT_PROFILE = "builtin:keychron-v3-8k-ansi-encoder-effect25"


@dataclass(frozen=True, slots=True)
class Geometry:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


class EncoderDirection(str, Enum):
    COUNTERCLOCKWISE = "counterclockwise"
    CLOCKWISE = "clockwise"


@dataclass(frozen=True, slots=True)
class EncoderPosition:
    index: int
    direction: EncoderDirection


@dataclass(frozen=True, slots=True)
class PhysicalElement:
    element_id: str
    element_type: str
    row: int
    order: int
    geometry: Geometry
    led_address: int | None
    led_point: Point | None
    matrix: tuple[int, int] | None
    encoder: EncoderPosition | None

    @property
    def rgb_capable(self) -> bool:
        return self.led_address is not None


@dataclass(frozen=True, slots=True)
class RegionTarget:
    region_id: str
    elements: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]
    strategies: frozenset[MappingStrategy]
    large_key_threshold_u: float
    anchored_rows: tuple[str, ...]
    compatibility_issues: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DeviceMatch:
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int


@dataclass(frozen=True, slots=True)
class ProfileCapabilities:
    per_key_rgb: bool
    independent_brightness: bool
    guarded_frames: bool
    local_animation: bool


@dataclass(frozen=True, slots=True)
class KeymapGeometry:
    matrix_rows: int
    matrix_columns: int
    encoder_count: int


@dataclass(frozen=True, slots=True)
class LayoutProfile:
    schema_version: int
    profile_id: str
    display_name: str
    adapter_id: str
    adapter_min_version: int
    device_match: DeviceMatch
    keymap: KeymapGeometry
    expected_led_count: int
    capabilities: ProfileCapabilities
    elements: tuple[PhysicalElement, ...]
    regions: Mapping[str, RegionTarget]
    semantic_bindings: Mapping[str, Mapping[str, tuple[str, ...]]]
    aliases: Mapping[str, str]

    @property
    def element_by_id(self) -> dict[str, PhysicalElement]:
        return {element.element_id: element for element in self.elements}

    @property
    def rgb_elements(self) -> tuple[PhysicalElement, ...]:
        return tuple(element for element in self.elements if element.rgb_capable)

    def resolve_element_id(self, value: str) -> str:
        normalized = value.strip().upper()
        if normalized in self.element_by_id:
            return normalized
        return self.aliases.get(normalized, normalized)


def _resource_text(package: str, relative: str) -> str:
    return resources.files(package).joinpath(relative).read_text(encoding="utf-8")


def _schema() -> dict[str, object]:
    return json.loads(
        _resource_text("abralia.rgb", "resources/schemas/profile-v1.schema.json")
    )


def validate_profile(data: Mapping[str, object]) -> None:
    try:
        jsonschema.Draft202012Validator(_schema()).validate(data)
    except jsonschema.ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path) or "profile"
        raise ProfileValidationError(f"{path}: {error.message}") from error

    elements = data["elements"]
    assert isinstance(elements, list)
    ids = [element["id"] for element in elements]
    if len(ids) != len(set(ids)):
        raise ProfileValidationError("Physical element IDs must be unique.")
    led_addresses = [
        element.get("led_address")
        for element in elements
        if element.get("led_address") is not None
    ]
    if len(led_addresses) != len(set(led_addresses)):
        raise ProfileValidationError("LED addresses must be unique.")
    expected = int(data["expected_led_count"])
    if sorted(led_addresses) != list(range(expected)):
        raise ProfileValidationError(
            f"RGB elements must cover every LED address 0...{expected - 1}."
        )

    keymap = data["keymap"]
    matrix_positions = [
        tuple(element["matrix"]) for element in elements if element.get("matrix")
    ]
    if len(matrix_positions) != len(set(matrix_positions)):
        raise ProfileValidationError("Matrix coordinates must be unique.")
    encoder_positions = [
        (element["encoder"]["index"], element["encoder"]["direction"])
        for element in elements
        if element.get("encoder")
    ]
    if len(encoder_positions) != len(set(encoder_positions)):
        raise ProfileValidationError("Encoder control positions must be unique.")
    for element in elements:
        matrix = element.get("matrix")
        encoder = element.get("encoder")
        if matrix is not None and encoder is not None:
            raise ProfileValidationError(
                f"Element {element['id']!r} cannot be both a matrix and encoder position."
            )
        if matrix is not None and (
            matrix[0] >= keymap["matrix_rows"] or matrix[1] >= keymap["matrix_columns"]
        ):
            raise ProfileValidationError(
                f"Element {element['id']!r} has an out-of-range matrix coordinate."
            )
        if encoder is not None and encoder["index"] >= keymap["encoder_count"]:
            raise ProfileValidationError(
                f"Element {element['id']!r} has an out-of-range encoder index."
            )
        if element["type"] == "key" and matrix is None:
            raise ProfileValidationError(
                f"Key element {element['id']!r} requires a matrix coordinate."
            )
        if element["type"] == "encoder" and encoder is None:
            raise ProfileValidationError(
                f"Encoder element {element['id']!r} requires an encoder position."
            )

    known = set(ids)
    regions = data["regions"]
    assert isinstance(regions, list)
    region_ids = [region["id"] for region in regions]
    if len(region_ids) != len(set(region_ids)):
        raise ProfileValidationError("Region IDs must be unique.")
    element_by_id = {element["id"]: element for element in elements}
    for region in regions:
        element_references = set(region["elements"])
        row_references: set[str] = set()
        for row in region.get("rows", []):
            row_references.update(row)
        references = element_references | row_references
        if not references <= known:
            raise ProfileValidationError(
                f"Region {region['id']!r} references unknown elements {sorted(references - known)!r}."
            )
        if not row_references <= element_references:
            raise ProfileValidationError(
                f"Region {region['id']!r} rows contain elements outside the region."
            )
        strategies = set(region["strategies"])
        rows = region.get("rows", [])
        if strategies & {"row_key_index", "anchored_row_grid"} and not rows:
            raise ProfileValidationError(
                f"Region {region['id']!r} requires row metadata for its mapping strategies."
            )
        if "anchored_row_grid" in strategies:
            anchors = region.get("anchored_rows", [])
            if len(anchors) != len(rows):
                raise ProfileValidationError(
                    f"Region {region['id']!r} requires one anchor for every anchored row."
                )
            for row_index, (anchor, row) in enumerate(zip(anchors, rows, strict=True)):
                if not row or anchor != row[0]:
                    raise ProfileValidationError(
                        f"Region {region['id']!r} row {row_index} must begin at its anchor."
                    )
                geometries = [
                    element_by_id[element_id]["geometry"] for element_id in row
                ]
                if any(
                    geometry["width"] != 1 or geometry["height"] != 1
                    for geometry in geometries
                ):
                    raise ProfileValidationError(
                        f"Region {region['id']!r} anchored row {row_index} contains a non-1U element."
                    )
                if any(
                    right["x"] != left["x"] + 1 or right["y"] != left["y"]
                    for left, right in pairwise(geometries)
                ):
                    raise ProfileValidationError(
                        f"Region {region['id']!r} anchored row {row_index} contains a gap."
                    )

    for region, slots in data.get("semantic_bindings", {}).items():
        if region not in set(region_ids):
            raise ProfileValidationError(
                f"Semantic bindings reference unknown region {region!r}."
            )
        for slot, references in slots.items():
            unknown = set(references) - known
            if unknown:
                raise ProfileValidationError(
                    f"Semantic slot {region!r}/{slot!r} references unknown elements {sorted(unknown)!r}."
                )
    unknown_alias_targets = set(data.get("aliases", {}).values()) - known
    if unknown_alias_targets:
        raise ProfileValidationError(
            f"Aliases reference unknown elements {sorted(unknown_alias_targets)!r}."
        )


def _load_data(source: str | Path) -> dict[str, object]:
    if isinstance(source, str) and source.startswith("builtin:"):
        profile_id = source.removeprefix("builtin:")
        try:
            text = _resource_text(
                "abralia.rgb", f"resources/profiles/{profile_id}.json"
            )
        except FileNotFoundError as error:
            raise ProfileValidationError(
                f"Unknown bundled profile {profile_id!r}."
            ) from error
        return json.loads(text)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def load_profile(source: str | Path = DEFAULT_PROFILE) -> LayoutProfile:
    data = _load_data(source)
    validate_profile(data)
    elements = tuple(
        PhysicalElement(
            element_id=item["id"],
            element_type=item["type"],
            row=item["row"],
            order=item["order"],
            geometry=Geometry(**item["geometry"]),
            led_address=item.get("led_address"),
            led_point=Point(**item["led_point"]) if item.get("led_point") else None,
            matrix=tuple(item["matrix"]) if item.get("matrix") else None,
            encoder=(
                EncoderPosition(
                    index=item["encoder"]["index"],
                    direction=EncoderDirection(item["encoder"]["direction"]),
                )
                if item.get("encoder")
                else None
            ),
        )
        for item in data["elements"]
    )
    regions = {
        item["id"]: RegionTarget(
            region_id=item["id"],
            elements=tuple(item["elements"]),
            rows=tuple(tuple(row) for row in item.get("rows", [])),
            strategies=frozenset(
                MappingStrategy(value) for value in item["strategies"]
            ),
            large_key_threshold_u=float(item.get("large_key_threshold_u", 4.0)),
            anchored_rows=tuple(item.get("anchored_rows", [])),
        )
        for item in data["regions"]
    }
    match = data["device_match"]
    capabilities = data["capabilities"]
    return LayoutProfile(
        schema_version=data["schema_version"],
        profile_id=data["profile_id"],
        display_name=data["display_name"],
        adapter_id=data["adapter"]["id"],
        adapter_min_version=data["adapter"]["min_version"],
        device_match=DeviceMatch(**match),
        keymap=KeymapGeometry(**data["keymap"]),
        expected_led_count=data["expected_led_count"],
        capabilities=ProfileCapabilities(**capabilities),
        elements=elements,
        regions=regions,
        semantic_bindings={
            region: {slot: tuple(values) for slot, values in slots.items()}
            for region, slots in data.get("semantic_bindings", {}).items()
        },
        aliases=data.get("aliases", {}),
    )
