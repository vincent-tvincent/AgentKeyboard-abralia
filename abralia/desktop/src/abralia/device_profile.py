# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Explicit device metadata from shared JSON profiles; no model registry."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import cache
from importlib import resources
from pathlib import Path

import jsonschema


class DeviceProfileError(ValueError):
    """Device metadata is missing, inconsistent, or unsupported."""


@dataclass(frozen=True, slots=True)
class DeviceMatch:
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int

    def matches(self, device: object) -> bool:
        return all(
            getattr(device, name, None) == value for name, value in asdict(self).items()
        )


@dataclass(frozen=True, slots=True)
class KeymapGeometry:
    matrix_rows: int
    matrix_columns: int
    encoder_count: int


@dataclass(frozen=True, slots=True)
class ProfileCapabilities:
    per_key_rgb: bool
    independent_brightness: bool
    guarded_frames: bool
    local_animation: bool


@dataclass(frozen=True, slots=True)
class InteractionProfile:
    toggle_matrix: tuple[int, int]


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    schema_version: int
    profile_id: str
    display_name: str
    adapter_id: str
    adapter_min_version: int
    device_match: DeviceMatch
    keymap: KeymapGeometry
    expected_led_count: int
    capabilities: ProfileCapabilities
    interaction: InteractionProfile | None = None

    def __post_init__(self) -> None:
        data = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "adapter": {"id": self.adapter_id, "min_version": self.adapter_min_version},
            "device_match": asdict(self.device_match),
            "keymap": asdict(self.keymap),
            "expected_led_count": self.expected_led_count,
            "capabilities": asdict(self.capabilities),
        }
        if self.interaction is not None:
            if not isinstance(self.interaction.toggle_matrix, tuple):
                raise DeviceProfileError(
                    "interaction.toggle_matrix must be an immutable tuple."
                )
            data["interaction"] = {
                "toggle_matrix": list(self.interaction.toggle_matrix)
            }
        validate_device_data(data)

    def require_adapter(self, adapter_id: str, version: int) -> None:
        if self.adapter_id != adapter_id or self.adapter_min_version > version:
            raise DeviceProfileError(
                f"Profile requires {self.adapter_id!r} version {self.adapter_min_version}; "
                f"available adapter is {adapter_id!r} version {version}."
            )

    def require_interaction(self) -> InteractionProfile:
        if self.interaction is None:
            raise DeviceProfileError(
                "Host Interaction requires interaction.toggle_matrix in the supplied profile."
            )
        return self.interaction


@cache
def load_schema(name: str) -> dict:
    return json.loads(
        resources.files("abralia")
        .joinpath("resources/schemas", name)
        .read_text(encoding="utf-8")
    )


def load_profile_data(source: str | Path) -> dict:
    try:
        if isinstance(source, str) and source.startswith("builtin:"):
            profile_id = source.removeprefix("builtin:")
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", profile_id):
                raise DeviceProfileError("Invalid bundled profile ID.")
            text = (
                resources.files("abralia")
                .joinpath("resources/profiles", f"{profile_id}.json")
                .read_text(encoding="utf-8")
            )
        else:
            text = Path(source).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as error:
        raise DeviceProfileError(
            f"Could not load profile {str(source)!r}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise DeviceProfileError("Profile must be a JSON object.")
    return data


def validate_device_data(data: Mapping[str, object]) -> None:
    """Validate the shared header without inspecting RGB geometry or regions."""
    full = load_schema("profile-v1.schema.json")
    fields = (
        "schema_version",
        "profile_id",
        "display_name",
        "adapter",
        "device_match",
        "keymap",
        "expected_led_count",
        "capabilities",
        "interaction",
    )
    schema = {
        "type": "object",
        "required": list(fields[:-1]),
        "properties": {name: full["properties"][name] for name in fields},
        "$defs": full["$defs"],
    }
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "profile"
        raise DeviceProfileError(f"{location}: {error.message}") from error
    if data.get("interaction") is not None:
        row, column = data["interaction"]["toggle_matrix"]
        if (
            row >= data["keymap"]["matrix_rows"]
            or column >= data["keymap"]["matrix_columns"]
        ):
            raise DeviceProfileError(
                "interaction.toggle_matrix is outside the declared matrix."
            )


def device_profile_from_data(data: Mapping[str, object]) -> DeviceProfile:
    validate_device_data(data)
    interaction = data.get("interaction")
    return DeviceProfile(
        schema_version=data["schema_version"],
        profile_id=data["profile_id"],
        display_name=data["display_name"],
        adapter_id=data["adapter"]["id"],
        adapter_min_version=data["adapter"]["min_version"],
        device_match=DeviceMatch(**data["device_match"]),
        keymap=KeymapGeometry(**data["keymap"]),
        expected_led_count=data["expected_led_count"],
        capabilities=ProfileCapabilities(**data["capabilities"]),
        interaction=(
            InteractionProfile(tuple(interaction["toggle_matrix"]))
            if interaction is not None
            else None
        ),
    )


def load_device_profile(source: str | Path) -> DeviceProfile:
    return device_profile_from_data(load_profile_data(source))
