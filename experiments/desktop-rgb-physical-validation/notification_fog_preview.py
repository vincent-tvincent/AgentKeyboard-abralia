#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Generate algorithm-derived fog previews; no keyboard, socket or app access.

Requires Pillow only for writing preview images. The production fog module has
no imaging dependency. Run from an environment with abralia-desktop and Pillow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from abralia.backend.core import Broker, Caller
from abralia.backend.fog import FogField
from abralia.backend.render import Renderer
from abralia.rgb import Srgb8, load_profile


def rgb(color):
    return color.red, color.green, color.blue


def mix_sheet(output):
    from PIL import Image, ImageDraw

    panels = [
        ('Red + blue / white background', ('FF3030', '3050FF'), 128, 1),
        ('Red + green / white background', ('FF3030', '30FF50'), 128, 1),
        ('Three colors / white background', ('FF3030', '30FF50', '3050FF'), 128, 1),
        ('Red + blue / black background', ('FF3030', '3050FF'), 0, 1),
        ('Same color / stronger overlapping fog', ('3050FF', '3050FF'), 128, 1),
        ('Red + blue / 25% remaining opacity', ('FF3030', '3050FF'), 128, .25),
    ]
    width, height, label = 360, 170, 32
    sheet = Image.new('RGB', (width * 2, (height + label) * 3), '#172031')
    draw = ImageDraw.Draw(sheet)
    evidence = []
    for index, (title, colors, level, opacity) in enumerate(panels):
        field = FogField(bounds=(0, 0, 10, 4), radius=2.2)
        records = [dict(orb_key=f'fixture-{i}', identity_color=color, opacity=opacity, quiet=False)
                   for i, color in enumerate(colors)]
        field.update(records, 0)
        # Fixed fixture positions deliberately force intersecting fog volumes.
        for i, key in enumerate(sorted(field.bodies)):
            field.bodies[key].x = (4, 6, 5)[i]
            field.bodies[key].y = (2, 2, 1.1)[i]
        background = Srgb8(level, level, level)
        raster = Image.new('RGB', (width, height))
        pixels = raster.load()
        for py in range(height):
            for px in range(width):
                pixels[px, py] = rgb(field.sample(px * 10 / (width - 1), py * 4 / (height - 1),
                                                background, ceiling=128))
        col, row = index % 2, index // 2
        sheet.paste(raster, (col * width, row * (height + label) + label))
        draw.text((col * width + 10, row * (height + label) + 9), title, fill='white')
        center = rgb(field.sample(5, 2, background, ceiling=128))
        evidence.append({'title':title, 'center_srgb':center, 'frame_ceiling':128})
    sheet.save(output / 'fog-mixing.png')
    return evidence


def keyboard_image(profile, scene, title):
    from PIL import Image, ImageDraw

    scale, margin = 43, 18
    elements = [e for e in profile.rgb_elements]
    right = max(e.geometry.x + e.geometry.width for e in elements)
    bottom = max(e.geometry.y + e.geometry.height for e in elements)
    image = Image.new('RGB', (round(right * scale) + 2 * margin,
                              round(bottom * scale) + 2 * margin + 27), '#172031')
    draw = ImageDraw.Draw(image)
    draw.text((margin, 10), title, fill='white')
    for element in elements:
        g = element.geometry
        x, y = margin + g.x * scale, margin + 27 + g.y * scale
        color = rgb(scene.colors.get(element.element_id, scene.background))
        draw.rounded_rectangle((x + 2, y + 2, x + g.width * scale - 2, y + g.height * scale - 2),
                               radius=4, fill=color, outline='#4B5563')
        name = element.element_id
        aliases = {'SCREENSHOT':'Pick', 'SCROLL_LOCK':'Mute', 'PAUSE':'Mode', 'PAGE_UP':'PgUp',
                   'PAGE_DOWN':'PgDn', 'LEFT':'<', 'RIGHT':'>', 'UP':'^', 'DOWN':'v'}
        text = aliases.get(name, name if len(name) <= 5 else name[:4])
        text_color = '#152033' if max(color) > 100 else '#F8FAFC'
        draw.text((x + 5, y + 6), text, fill=text_color)
    return image


def lifecycle_preview(output, profile_name):
    now = [0.]
    broker = Broker(clock=lambda: now[0])
    broker.set_active(True)
    profile = load_profile(profile_name)
    renderer = Renderer(profile)
    for index in range(3):
        caller = Caller(f'preview:{index}')
        result = broker.call(caller, 'acquire_slot', {'label':f'Simulated task {index + 1}', 'idempotency_key':'start'})
        broker.call(caller, 'set_notification', {'slot_token':result['allocation']['slot_token'],
                                                'enabled':True, 'idempotency_key':'notify'})
    frames, stills = [], []
    samples = {0, 4, 9, 11, 20, 22, 32, 40, 145, 175, 212}
    for tick in range(1061):
        now[0] = tick / 5
        broker.step()
        scene = renderer.frame(broker).payload
        if now[0] <= 42 or now[0] in samples:
            state = broker.notification_visuals()
            presentation = state['presentation']
            phase = presentation['phase'] if presentation else 'floating / aging'
            frame = keyboard_image(profile, scene, f'{now[0]:5.1f}s | {phase} | simulated tasks only')
            if now[0] <= 42:
                frames.append(frame)
            if now[0] in samples:
                stills.append(frame)
    frames[0].save(output / 'fog-keyboard.gif', save_all=True, append_images=frames[1:], duration=200, loop=0)
    from PIL import Image
    sheet = Image.new('RGB', (stills[0].width * 2, stills[0].height * ((len(stills) + 1) // 2)), '#172031')
    for i, frame in enumerate(stills):
        sheet.paste(frame, ((i % 2) * frame.width, (i // 2) * frame.height))
    sheet.save(output / 'fog-lifecycle.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='builtin:keychron-v3-8k-ansi-encoder-effect25')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = mix_sheet(args.output)
    lifecycle_preview(args.output, args.profile)
    (args.output / 'mixing-samples.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
