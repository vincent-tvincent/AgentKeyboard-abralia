# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Explicit hardware declarations for offline tests, never runtime defaults."""

from dataclasses import replace

from abralia.device_profile import (
    InteractionProfile,
    KeymapGeometry,
    load_device_profile,
)

REFERENCE_SOURCE = "builtin:keychron-v3-8k-ansi-encoder-effect25"
REFERENCE = load_device_profile(REFERENCE_SOURCE)
TINY = replace(
    REFERENCE,
    keymap=KeymapGeometry(2, 2, 1),
    interaction=InteractionProfile((1, 1)),
)
