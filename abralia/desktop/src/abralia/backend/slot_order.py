# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Deterministic slot palettes. Color is presentation, never allocation identity."""

import colorsys
import hashlib
from pathlib import Path

PROJECT_ANCHORS = (210, 90, 330, 30, 150, 270)
SIBLING_HUES = (0, -12, 12, -18, 18, -6, 6, 0)
SIBLING_SV = ((.78, .96), (.72, 1), (.84, .88), (.82, 1),
              (.70, .92), (.88, .96), (.76, .86), (.68, 1))


def _ordinal(value):
    if type(value) is not int or value < 0:
        raise ValueError('palette ordinal must be a nonnegative integer')
    return value


def _fraction(namespace, *ordinals):
    digest = hashlib.sha256(namespace)
    for ordinal in ordinals:
        ordinal = _ordinal(ordinal)
        encoded = ordinal.to_bytes(max(1, (ordinal.bit_length()+7)//8), 'big')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
    return (int.from_bytes(digest.digest()[:8], 'big') >> 11) / (1 << 53)


def _hex(hue, saturation, value):
    return ''.join(f'{round(c*255):02X}' for c in colorsys.hsv_to_rgb(hue % 1, saturation, value))


def normal_color(ordinal):
    """Preserve the existing initial golden-angle palette; accept any ordinal."""
    ordinal = _ordinal(ordinal)
    base_hue, saturation, _ = colorsys.rgb_to_hsv(32/255, 128/255, 1)
    if ordinal < 1_000_000:
        hue = (base_hue + ordinal * .618033988749895) % 1
        saturation = saturation if ordinal < 128 else .65 + .3 * ((ordinal * .754877666) % 1)
    else:
        hue = _fraction(b'abralia-individual-hue', ordinal)
        saturation = .65 + .3 * _fraction(b'abralia-individual-saturation', ordinal)
    return _hex(hue, saturation, 1)


def project_anchor(project_ordinal):
    ordinal = _ordinal(project_ordinal)
    if ordinal < len(PROJECT_ANCHORS):
        return PROJECT_ANCHORS[ordinal]
    cell, batch = (ordinal-6) % 6, (ordinal-6) // 6
    fraction, weight, bits = 0., .5, batch+1
    if batch:
        # Binary radical inversion progressively fills the largest remaining
        # hue gaps. Skip .5: those six centers are the accepted first palette.
        while bits and weight:
            if bits & 1:
                fraction += weight
            bits >>= 1
            weight *= .5
    return 60 * (cell + fraction)


def project_color(project_ordinal, sibling_ordinal):
    """Keep the accepted six families/eight siblings, then extend procedurally."""
    project_ordinal, sibling_ordinal = _ordinal(project_ordinal), _ordinal(sibling_ordinal)
    anchor = project_anchor(project_ordinal)
    if sibling_ordinal < len(SIBLING_HUES):
        hue = SIBLING_HUES[sibling_ordinal]
        saturation, value = SIBLING_SV[sibling_ordinal]
    else:
        hue = -18 + 36 * _fraction(b'abralia-sibling-hue', project_ordinal, sibling_ordinal)
        saturation = .68 + .2 * _fraction(b'abralia-sibling-saturation', project_ordinal, sibling_ordinal)
        value = .86 + .14 * _fraction(b'abralia-sibling-value', project_ordinal, sibling_ordinal)
    return _hex((anchor+hue)/360, saturation, value)


def project_key(caller):
    if caller.project_id:
        return 'id:' + caller.project_id
    if caller.project_path:
        return 'path:' + str(Path(caller.project_path).resolve())
    return 'unassigned'


def valid_color(value):
    return isinstance(value, str) and len(value) == 6 and all(c in '0123456789ABCDEF' for c in value)
