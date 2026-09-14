# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Independent keyboard simulator; imports production pure Python APIs only."""

from pathlib import Path
import sys

# Running directly from the repository requires no installation into Codex.
_source = Path(__file__).resolve().parents[3] / 'abralia/desktop/src'
if _source.is_dir() and str(_source) not in sys.path:
    sys.path.insert(0, str(_source))

from .engine import Simulator

__all__ = ['Simulator']
