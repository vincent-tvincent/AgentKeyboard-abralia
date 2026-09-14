# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Pure slot-order/color experiment. No broker, files, IPC, or hardware access."""

from dataclasses import asdict, dataclass
import colorsys
import json
import math

PAGE_SIZE = 12
PROJECT_ANCHORS = (210, 90, 330, 30, 150, 270)
SIBLING_HUES = (0, -12, 12, -18, 18, -6, 6, 0)
SIBLING_SV = ((.78, .96), (.72, 1), (.84, .88), (.82, 1),
              (.70, .92), (.88, .96), (.76, .86), (.68, 1))
MAX_PROJECTS = len(PROJECT_ANCHORS)
MAX_PER_PROJECT = len(SIBLING_HUES)
MAX_SLOTS = MAX_PROJECTS * MAX_PER_PROJECT
DEMO_PROJECTS = ('Project A', 'Project B', 'Project C')


def _hex(hue, saturation, value):
    return ''.join(f'{round(channel * 255):02X}' for channel in colorsys.hsv_to_rgb(hue % 1, saturation, value))


def individual_palette(count=MAX_SLOTS):
    """Match Broker._identity_color's current registration-order algorithm."""
    if type(count) is not int or not 0 <= count <= MAX_SLOTS:
        raise ValueError('demo palette count must be between 0 and 48')
    base_hue, saturation, _ = colorsys.rgb_to_hsv(32 / 255, 128 / 255, 1)
    result, used = [], set()
    for ordinal in range(count):
        for offset in range(1024):
            candidate = ordinal + offset
            hue = (base_hue + candidate * .618033988749895) % 1
            sat = saturation if candidate < 128 else .65 + .3 * ((candidate * .754877666) % 1)
            color = _hex(hue, sat, 1)
            if color not in used:
                break
        else:
            raise ValueError('identity palette exhausted')
        result.append(color)
        used.add(color)
    return result


def grouped_palette(project_ordinal):
    if type(project_ordinal) is not int or not 0 <= project_ordinal < MAX_PROJECTS:
        raise ValueError('demo supports at most six project families')
    return [_hex((PROJECT_ANCHORS[project_ordinal] + offset) / 360, saturation, value)
            for offset, (saturation, value) in zip(SIBLING_HUES, SIBLING_SV)]


@dataclass(frozen=True)
class Slot:
    slot_id: int
    label: str
    project: str
    arrival_sequence: int
    individual_color: str
    grouped_color: str


class SortModel:
    def __init__(self):
        self.mode = 'incoming'
        self._slots = []
        self._projects = []
        self._individuals = individual_palette()

    def add(self, project, label):
        for value, name in ((project, 'project'), (label, 'label')):
            if not isinstance(value, str) or not value.strip() or len(value) > 80:
                raise ValueError(f'{name} must be nonempty text of at most 80 characters')
        if len(self._slots) >= MAX_SLOTS:
            raise ValueError('demo slot limit reached')
        sibling = sum(slot.project == project for slot in self._slots)
        if sibling >= MAX_PER_PROJECT:
            raise ValueError('demo is limited to eight agents per project')
        if project not in self._projects:
            if len(self._projects) >= MAX_PROJECTS:
                raise ValueError('demo project limit reached')
            self._projects.append(project)
        ordinal = len(self._slots)
        slot = Slot(ordinal + 1, label, project, ordinal + 1, self._individuals[ordinal],
                    grouped_palette(self._projects.index(project))[sibling])
        self._slots.append(slot)
        return slot

    def toggle_sort(self):
        self.mode = 'project' if self.mode == 'incoming' else 'incoming'
        return self.mode

    def ordered_slots(self):
        if self.mode == 'incoming':
            return list(self._slots)
        project_order = {project: index for index, project in enumerate(self._projects)}
        return sorted(self._slots, key=lambda slot: (project_order[slot.project], slot.arrival_sequence))

    def color(self, slot):
        if not isinstance(slot, Slot) or not any(slot is candidate for candidate in self._slots):
            raise ValueError('slot must belong to this demo model')
        return slot.grouped_color if self.mode == 'project' else slot.individual_color

    def snapshot(self):
        slots = [{**asdict(slot), 'display_position': position,
                  'page': (position - 1) // PAGE_SIZE + 1,
                  'f_key': f'F{(position - 1) % PAGE_SIZE + 1}', 'color': self.color(slot)}
                 for position, slot in enumerate(self.ordered_slots(), 1)]
        return {'mode': self.mode, 'page_size': PAGE_SIZE,
                'page_count': max(1, math.ceil(len(slots) / PAGE_SIZE)),
                'projects': list(self._projects), 'slots': slots}


def palette_metrics(palettes):
    """Numerical separation only; these are not perceived/physical contrasts."""
    entries = [(project, color, tuple(int(color[index:index+2], 16) for index in (0, 2, 4)))
               for project, colors in palettes.items() for color in colors]
    within, between, hue_between = [], [], []
    for index, (project, _, rgb) in enumerate(entries):
        hue = colorsys.rgb_to_hsv(*(value / 255 for value in rgb))[0] * 360
        for other_project, _, other in entries[index+1:]:
            distance = math.sqrt(sum((a-b)**2 for a, b in zip(rgb, other)))
            (within if project == other_project else between).append(distance)
            if project != other_project:
                other_hue = colorsys.rgb_to_hsv(*(value / 255 for value in other))[0] * 360
                hue_between.append(abs((hue-other_hue+180) % 360 - 180))
    minimum = lambda values: round(min(values), 3) if values else None
    return {'unique_colors': len({color for _, color, _ in entries}), 'color_count': len(entries),
            'minimum_within_project_rgb_distance': minimum(within),
            'minimum_between_project_rgb_distance': minimum(between),
            'minimum_between_project_hue_degrees': minimum(hue_between),
            'interpretation': 'sRGB-byte/hue distances only; no physical or perceptual contrast claim'}


def demo_payload():
    """Initial 12-agent page and immutable palettes for a dependency-free UI."""
    model = SortModel()
    for number in range(1, 5):
        for project in DEMO_PROJECTS:
            model.add(project, f'{project[-1]}{number}')
    palettes = {project: grouped_palette(index) for index, project in enumerate(DEMO_PROJECTS)}
    return {'initial': model.snapshot(), 'individual_palette': individual_palette(),
            'grouped_palettes': palettes,
            'project_anchors': {project: PROJECT_ANCHORS[index] for index, project in enumerate(DEMO_PROJECTS)},
            'limits': {'max_projects': MAX_PROJECTS, 'max_per_project': MAX_PER_PROJECT, 'max_slots': MAX_SLOTS},
            'palette_metrics': palette_metrics(palettes)}


if __name__ == '__main__':
    print(json.dumps(demo_payload(), indent=2))
