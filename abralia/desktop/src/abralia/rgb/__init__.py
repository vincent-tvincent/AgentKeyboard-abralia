# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Generalized, diagram-aligned RGB API for Abralia desktop clients."""

from .colors import BLACK, Color, Hsv8, Srgb8
from .controller import DisplayLease, RgbController
from .key_lookup import (
    KeycodeMatch,
    KeycodeResolution,
    LiveKeycodeResolver,
    LiveKeymapAddressSpace,
    RawControlAddress,
    UnrenderableReason,
)
from .profiles import (
    DEFAULT_PROFILE,
    EncoderDirection,
    EncoderPosition,
    LayoutProfile,
    PhysicalElement,
    load_profile,
    validate_profile,
)
from .scene import (
    AbstractScene,
    Canvas,
    MappingStrategy,
    PhysicalFrame,
    PhysicalOverlaySceneBuilder,
    PhysicalSceneBuilder,
    RectangularSceneBuilder,
    SemanticRegionSceneBuilder,
)

__all__ = [
    "BLACK",
    "DEFAULT_PROFILE",
    "AbstractScene",
    "Canvas",
    "Color",
    "DisplayLease",
    "EncoderDirection",
    "EncoderPosition",
    "Hsv8",
    "KeycodeMatch",
    "KeycodeResolution",
    "LayoutProfile",
    "LiveKeycodeResolver",
    "LiveKeymapAddressSpace",
    "MappingStrategy",
    "PhysicalElement",
    "PhysicalFrame",
    "PhysicalOverlaySceneBuilder",
    "PhysicalSceneBuilder",
    "RawControlAddress",
    "RectangularSceneBuilder",
    "RgbController",
    "SemanticRegionSceneBuilder",
    "Srgb8",
    "UnrenderableReason",
    "load_profile",
    "validate_profile",
]
