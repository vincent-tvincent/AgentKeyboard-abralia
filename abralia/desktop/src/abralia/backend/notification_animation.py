# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Small, data-only notification overlays; no device, code or file execution."""

from __future__ import annotations

import json
import math

from abralia.rgb import Srgb8
from abralia.rgb.colors import LinearRgb, linear_to_srgb8, to_linear_rgb

TRANSITION_SECONDS = 4.
MAX_LAYERS = 4
MAX_OPACITY = .7
COLORS = ('slot', 'slot_highlight', 'slot_shadow', 'positive', 'negative')
SHAPES = ('ring', 'spot', 'sweep', 'pulse')


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('animation_value_out_of_range')
    return float(value)


def _pair(value, low, high):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('animation_requires_two_values')
    return [_number(v, low, high) for v in value]


def _range(value, low, high):
    return _pair(value, low, high) if isinstance(value, list) else [_number(value, low, high)] * 2


def validate_animation(value, *, max_duration_ms=4000):
    """Normalize a bounded JSON description. Raise stable errors, never echo input."""
    if not isinstance(value, dict) or set(value) != {'duration_ms', 'layers'}:
        raise ValueError('animation_requires_duration_and_layers')
    if len(json.dumps(value, allow_nan=False).encode()) > 8192:
        raise ValueError('animation_too_large')
    duration = value['duration_ms']
    if type(duration) is not int or not 1000 <= duration <= max_duration_ms:
        raise ValueError('animation_duration_out_of_range')
    layers = value['layers']
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        raise ValueError('animation_layer_limit')
    normalized = []
    for layer in layers:
        if not isinstance(layer, dict) or layer.get('shape') not in SHAPES:
            raise ValueError('unsupported_animation_shape')
        shape = layer['shape']
        allowed = {'shape', 'color', 'opacity', 'start_ms', 'end_ms'} | {
            'ring': {'center', 'radius', 'width'}, 'spot': {'from', 'to', 'radius'},
            'sweep': {'axis', 'from', 'to', 'width'}, 'pulse': {'center', 'radius', 'pulses'},
        }[shape]
        if set(layer) - allowed or layer.get('color', 'slot_highlight') not in COLORS:
            raise ValueError('unsupported_animation_field_or_color')
        start, end = layer.get('start_ms', 0), layer.get('end_ms', duration)
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= duration or end - start < 300:
            raise ValueError('animation_layer_time_out_of_range')
        item = {'shape': shape, 'color': layer.get('color', 'slot_highlight'),
                'opacity': _range(layer.get('opacity', [.6, 0]), 0, MAX_OPACITY),
                'start_ms': start, 'end_ms': end}
        if shape in ('ring', 'pulse'):
            item['center'] = _pair(layer.get('center', [.5, .5]), 0, 1)
        if shape in ('ring', 'spot', 'pulse'):
            item['radius'] = _range(layer.get('radius', [.05, .6] if shape == 'ring' else .15), .02, 1)
        if shape in ('ring', 'sweep'):
            item['width'] = _number(layer.get('width', .06), .02, .25)
        if shape == 'spot':
            item['from'] = _pair(layer.get('from', [.1, .5]), 0, 1)
            item['to'] = _pair(layer.get('to', [.9, .5]), 0, 1)
        if shape == 'sweep':
            if layer.get('axis', 'x') not in ('x', 'y'):
                raise ValueError('unsupported_animation_axis')
            item.update(axis=layer.get('axis', 'x'), **{
                'from': _number(layer.get('from', 0), 0, 1), 'to': _number(layer.get('to', 1), 0, 1)})
        if shape == 'pulse':
            pulses = layer.get('pulses', 1)
            if type(pulses) is not int or not 1 <= pulses <= 3:
                raise ValueError('animation_pulse_limit')
            item['pulses'] = pulses
        normalized.append(item)
    return {'duration_ms': duration, 'layers': normalized}


def _smooth(value):
    value = min(1., max(0., value))
    return value * value * (3 - 2 * value)


def _lerp(pair, t):
    return pair[0] + (pair[1] - pair[0]) * t


def palette_color(name, slot):
    if name == 'positive':
        return Srgb8(160, 255, 0)
    if name == 'negative':
        return Srgb8(255, 0, 0)
    values = (slot.red, slot.green, slot.blue)
    if name == 'slot_highlight':
        return Srgb8(*(round(v + (255 - v) * .3) for v in values))
    if name == 'slot_shadow':
        return Srgb8(*(round(v * .35) for v in values))
    return slot


def render_animation(animation, points, elapsed_ms, slot_color, background, ceiling):
    """Composite normalized layers over a fixed slot-colored background.

    points maps physical key names to normalized coordinates. The caller masks
    control keys. Combined alpha stays <= .7 even when every layer overlaps.
    """
    if elapsed_ms <= 0 or elapsed_ms >= animation['duration_ms']:
        return {}
    active = []
    for layer in animation['layers']:
        if not layer['start_ms'] < elapsed_ms < layer['end_ms']:
            continue
        span = layer['end_ms'] - layer['start_ms']
        t = (elapsed_ms - layer['start_ms']) / span
        edge = min(200., span / 4)
        envelope = _smooth((elapsed_ms - layer['start_ms']) / edge) * _smooth((layer['end_ms'] - elapsed_ms) / edge)
        color = palette_color(layer['color'], slot_color)
        bounded = Srgb8(*(round(v * ceiling / 255) for v in (color.red, color.green, color.blue)))
        active.append((layer, _smooth(t), t, envelope, to_linear_rgb(bounded)))
    base = to_linear_rgb(background)
    result = {}
    for key, (x, y) in points.items():
        contributions = []
        for layer, t, raw_t, envelope, color in active:
            shape = layer['shape']
            if shape == 'sweep':
                distance = abs((x if layer['axis'] == 'x' else y) - _lerp([layer['from'], layer['to']], t))
                spatial = math.exp(-(distance / layer['width']) ** 2)
            else:
                center = ([_lerp([a, b], t) for a, b in zip(layer['from'], layer['to'])]
                          if shape == 'spot' else layer['center'])
                distance = math.hypot(x - center[0], y - center[1])
                radius = _lerp(layer['radius'], t)
                spatial = (math.exp(-((distance - radius) / layer['width']) ** 2) if shape == 'ring'
                           else math.exp(-(distance / radius) ** 2))
                if shape == 'pulse':
                    spatial *= math.sin(math.pi * layer['pulses'] * raw_t) ** 2
            weight = _lerp(layer['opacity'], t) * envelope * spatial
            if weight > 1e-9:
                contributions.append((weight, color))
        total = math.fsum(w for w, _ in contributions)
        if total:
            alpha = min(MAX_OPACITY, total)
            mixed = [math.fsum(w * getattr(c, channel) for w, c in contributions) / total
                     for channel in ('red', 'green', 'blue')]
            result[key] = linear_to_srgb8(LinearRgb(*(a * (1 - alpha) + b * alpha
                for a, b in zip((base.red, base.green, base.blue), mixed))))
    return result
