# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""PNG/GIF export from actual simulator snapshots, with monochrome narration."""

from copy import deepcopy
from functools import lru_cache
import math
from pathlib import Path
import re

from .scenarios import get_scenario


LABELS = {
    'SCREENSHOT': 'PrtSc', 'SCROLL_LOCK': 'ScrLk', 'PAUSE': 'Pause', 'ESC': 'Esc',
    'BACKSPACE': 'Backspace', 'CAPS_LOCK': 'Caps', 'ENTER': 'Enter', 'SPACE': 'Space',
    'LEFT_SHIFT': 'Shift', 'RIGHT_SHIFT': 'Shift', 'LEFT_CONTROL': 'Ctrl', 'RIGHT_CONTROL': 'Ctrl',
    'LEFT_CTRL': 'Ctrl', 'RIGHT_CTRL': 'Ctrl', 'LEFT_ALT': 'Alt', 'RIGHT_ALT': 'Alt',
    'LEFT_GUI': 'Win', 'RIGHT_GUI': 'Win', 'APPLICATION': 'Menu', 'MENU': 'Menu',
    'PAGE_UP': 'PgUp', 'PAGE_DOWN': 'PgDn', 'INSERT': 'Ins', 'DELETE': 'Del',
    'HOME': 'Home', 'END': 'End', 'UP': '^', 'DOWN': 'v', 'LEFT': '<', 'RIGHT': '>',
    'TAB': 'Tab', 'BACKSLASH': '\\', 'GRAVE': '`', 'MINUS': '-', 'EQUAL': '=',
    'LEFT_BRACKET': '[', 'RIGHT_BRACKET': ']', 'SEMICOLON': ';', 'QUOTE': "'",
    'COMMA': ',', 'DOT': '.', 'PERIOD': '.', 'SLASH': '/', 'KNOB_PRESS': 'Knob',
    'LEFT_MODIFIER_2': 'LMod2', 'LEFT_MODIFIER_3': 'LMod3',
    'RIGHT_MODIFIER_1': 'RMod1', 'RIGHT_MODIFIER_2': 'RMod2',
    'LIGHTING_KEY': 'Light',
}
_GRAY = (0, 12, 18, 22, 26, 30, 34, 40, 46, 52, 60, 68, 76, 84, 92, 100,
         110, 120, 128, 136, 146, 156, 166, 176, 186, 196, 206, 216, 228, 238, 248, 255)
MAX_FRAMES = 1200


def _pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError('Image export requires Pillow: install the simulator export dependencies.') from error
    return Image, ImageChops, ImageDraw, ImageFont


@lru_cache(maxsize=12)
def _font(size):
    ImageFont = _pillow()[3]
    for name in ('DejaVuSans.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-fA-F]{6}', value) is None:
        raise ValueError('LED values must be six-digit RGB colors')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _keys(snapshot):
    keys = snapshot.get('keys')
    if not isinstance(keys, list) or not 1 <= len(keys) <= 256:
        raise ValueError('Snapshot needs a bounded physical-key list')
    seen = set()
    for key in keys:
        if not isinstance(key, dict) or not isinstance(key.get('id'), str) or key['id'] in seen:
            raise ValueError('Snapshot key IDs must be unique strings')
        seen.add(key['id'])
        for name in ('x', 'y', 'w', 'h'):
            value = key.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Physical geometry must be finite')
        if not (0 <= key['x'] <= 50 and 0 <= key['y'] <= 20 and 0 < key['w'] <= 15 and 0 < key['h'] <= 10):
            raise ValueError('Physical geometry is outside export bounds')
        if type(key.get('rgb')) is not bool:
            raise ValueError('Each key must state RGB capability')
        if key['rgb']:
            _rgb(key.get('led'))
        elif key.get('led') is not None:
            raise ValueError('Non-RGB controls cannot contain LED colors')
    return keys


def _indicator_keys(snapshot, keys):
    mode = snapshot.get('profile', {}).get('toggle_key', 'PAUSE')
    available = {key['id'] for key in snapshot['keys']}
    result = set()
    for key in keys:
        key = mode if key == 'MODE' else 'KNOB_PRESS' if key in ('KNOB_CW', 'KNOB_CCW') else key
        if key not in available:
            raise ValueError('Unknown physical action key: ' + str(key))
        result.add(key)
    return result


def _wrap(draw, text, font, width, max_lines=2):
    lines, current = [], ''
    for word in str(text).split():
        candidate = current + (' ' if current else '') + word
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(current); current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:max_lines]


def render_snapshot(snapshot, *, caption='', title='Abralia keyboard simulator',
                    action_keys=(), width=960, progress=0., step=None):
    """Render physical geometry and LED values; all other pixels are grayscale."""
    if type(width) is not int or not 640 <= width <= 1600:
        raise ValueError('Export width must be 640..1600 pixels')
    if not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1:
        raise ValueError('Export progress must be within 0..1')
    Image, _, ImageDraw, _ = _pillow()
    keys = _keys(snapshot)
    indicator = _indicator_keys(snapshot, action_keys)
    left, top = min(key['x'] for key in keys), min(key['y'] for key in keys)
    right, bottom = max(key['x'] + key['w'] for key in keys), max(key['y'] + key['h'] for key in keys)
    margin = 27
    scale = (width - 2 * margin) / max(1, right - left)
    keyboard_y = 116
    keyboard_h = math.ceil((bottom - top) * scale)
    height = keyboard_y + keyboard_h + 96
    image = Image.new('RGB', (width, height), (18, 18, 18))
    draw = ImageDraw.Draw(image)
    title_font, caption_font, detail_font = _font(22), _font(15), _font(12)
    draw.text((margin, 18), title, font=title_font, fill=(248, 248, 248))
    badge = 'SIMULATED HOST OUTPUT'
    badge_width = draw.textbbox((0, 0), badge, font=detail_font)[2]
    draw.text((width - margin - badge_width, 23), badge, font=detail_font, fill=(156, 156, 156))
    for i, line in enumerate(_wrap(draw, caption, caption_font, width - 2 * margin)):
        draw.text((margin, 56 + i * 21), line, font=caption_font, fill=(216, 216, 216))
    draw.rounded_rectangle((margin - 9, keyboard_y - 9, width - margin + 9, keyboard_y + keyboard_h + 8),
                           radius=12, fill=(30, 30, 30), outline=(68, 68, 68), width=1)
    rendered = set()
    for key in keys:
        rectangle = (key['x'], key['y'], key['w'], key['h'])
        if not key['rgb'] and rectangle in rendered:
            continue  # Knob CW/CCW/press are one physical control.
        rendered.add(rectangle)
        x = margin + (key['x'] - left) * scale
        y = keyboard_y + (key['y'] - top) * scale
        box = (round(x + 3), round(y + 3), round(x + key['w'] * scale - 3), round(y + key['h'] * scale - 3))
        led = _rgb(key['led']) if key['rgb'] else (46, 46, 46)
        if key['rgb']:
            draw.rounded_rectangle(box, radius=4, fill=led, outline=(92, 92, 92), width=1)
        else:
            draw.ellipse(box, fill=led, outline=(128, 128, 128), width=2)
            draw.line(((box[0]+box[2])/2, box[1]+5, (box[0]+box[2])/2, box[1]+10), fill=(216,216,216), width=2)
        label = LABELS.get(key['id'], key.get('label', key['id']).replace('_', ' ').title() if len(key['id']) > 3 else key['id'])
        font = _font(11 if len(label) >= 5 else 12)
        while draw.textbbox((0, 0), label, font=font)[2] > box[2] - box[0] - 3 and len(label) > 2:
            label = label[:-1]
        text_box = draw.textbbox((0, 0), label, font=font)
        foreground = (18, 18, 18) if .2126*led[0] + .7152*led[1] + .0722*led[2] > 142 else (248, 248, 248)
        draw.text(((box[0]+box[2]-text_box[2])/2, (box[1]+box[3]-(text_box[3]-text_box[1]))/2-text_box[1]),
                  label, font=font, fill=foreground)
        if key['id'] in indicator or key.get('pressed'):
            ring = (box[0]-2, box[1]-2, box[2]+2, box[3]+2)
            if key['rgb']:
                draw.rounded_rectangle(ring, radius=6, outline=(255,255,255), width=3)
            else:
                draw.ellipse(ring, outline=(255,255,255), width=3)
    footer_y = keyboard_y + keyboard_h + 24
    elapsed = max(0, int(snapshot.get('time', 0)))
    clock = f'{elapsed//3600:02}:{elapsed%3600//60:02}:{elapsed%60:02}'
    status = ('Agent Mode' if snapshot.get('active') else 'Ordinary typing')
    status += f"   |   Page {snapshot.get('page', 1)}/{snapshot.get('page_count', 1)}"
    status += f"   |   Tasks {len(snapshot.get('agents', []))}   |   Sort: {snapshot.get('sort_policy', 'incoming')}"
    draw.text((margin, footer_y), status, font=detail_font, fill=(196,196,196))
    note = ('Navigation armed' if snapshot.get('navigation_active') else 'Navigation off')
    note += f"   |   Knob: {snapshot.get('knob_mode', 'pages')}   |   Clock {clock}"
    if step is not None:
        note += f'   |   Step {step}'
    draw.text((margin, footer_y+21), note, font=detail_font, fill=(146,146,146))
    draw.rectangle((margin, height-16, width-margin, height-12), fill=(52,52,52))
    if progress:
        draw.rectangle((margin, height-16, margin+(width-2*margin)*progress, height-12), fill=(196,196,196))
    return image


def capture_scenario(name, *, profile=None, seed=1, fps=12, max_frames=600):
    """Advance actual simulation time; omitted long waits are explicitly captioned."""
    from .engine import DEFAULT_PROFILE, Simulator
    if type(fps) is not int or not 1 <= fps <= 30:
        raise ValueError('Export fps must be an integer in 1..30')
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise ValueError('Export frame limit must be 1..1200')
    simulator = Simulator(profile=profile or DEFAULT_PROFILE, seed=seed)
    scenario = get_scenario(name, simulator.profile)
    for agent in scenario['design']['seed_agents']:
        identity = simulator.add_agent(agent['project'], agent['label'], agent_id=agent.get('id'))
        if agent.get('state'):
            simulator.set_state(identity, agent['state'])
    frames, jumps, truncated = [], [], False
    segments = scenario['segments']
    for index, segment in enumerate(segments):
        if len(frames) >= max_frames:
            truncated = True
            break
        if segment['jump']:
            simulator.advance(segment['jump'])
            jumps.append({'seconds': segment['jump'], 'caption': segment['caption']})
        for action in segment['actions']:
            simulator.apply_action(deepcopy(action))
        count = max(1, round(segment['seconds'] * fps))
        for sample in range(count):
            if len(frames) >= max_frames:
                truncated = True
                break
            snapshot = simulator.snapshot()
            _keys(snapshot)
            _indicator_keys(snapshot, segment['keys'])
            frames.append({'snapshot': {key: deepcopy(snapshot[key]) for key in
                          ('time','profile','active','navigation_active','knob_mode','sort_policy',
                           'page','page_count','agents','keys')},
                           'caption': segment['caption'], 'action_keys': tuple(segment['keys']),
                           'progress': (index + (sample+1)/count)/len(segments),
                           'step': f'{index+1}/{len(segments)}'})
            simulator.advance(segment['seconds']/count)
        if truncated:
            break
    if truncated and frames:
        frames[-1]['caption'] = 'Preview stopped at the requested frame limit; the scenario is incomplete.'
    return {'name': name, 'label': scenario['label'], 'frames': frames, 'fps': fps,
            'time_jumps': jumps, 'truncated': truncated, 'simulation_seconds': simulator.time}


def _palette(frames):
    Image = _pillow()[0]
    samples = [_rgb(key['led']) for frame in frames for key in frame['snapshot']['keys'] if key['rgb']]
    size = (256, max(1, math.ceil(len(samples)/256)))
    image = Image.new('RGB', size)
    image.putdata(samples + [(0,0,0)]*(size[0]*size[1]-len(samples)))
    adaptive = image.quantize(colors=224, method=Image.Quantize.MEDIANCUT).getpalette()[:224*3]
    values = [channel for gray in _GRAY for channel in (gray,gray,gray)] + adaptive
    values += [0] * (768-len(values))
    palette = Image.new('P', (1,1)); palette.putpalette(values)
    return palette


def _quantize(image, palette):
    Image, ImageChops, _, _ = _pillow()
    quantized = image.quantize(palette=palette, dither=Image.Dither.NONE)
    # Palette approximation must never turn monochrome captions/chrome into color.
    red, green, blue = image.split()
    difference = ImageChops.lighter(ImageChops.difference(red, green), ImageChops.difference(green, blue))
    gray_mask = difference.point(lambda value: 255 if value == 0 else 0)
    index = red.point([min(range(len(_GRAY)), key=lambda i: abs(_GRAY[i]-value)) for value in range(256)])
    gray = Image.frombytes('P', image.size, index.tobytes()); gray.putpalette(palette.getpalette())
    quantized.paste(gray, mask=gray_mask)
    return quantized


def _save_capture(captured, output, *, width):
    if not isinstance(output, (str, Path)) or Path(output).suffix.lower() not in ('.gif', '.png'):
        raise ValueError('Output must end in .gif or .png')
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = captured['frames']
    fps = captured['fps']
    # GIF stores centiseconds. Distribute rounding so playback retains the requested
    # total duration instead of consistently speeding up e.g. a 12 fps animation.
    durations = [10 * (round((i+1)*100/fps)-round(i*100/fps)) for i in range(len(frames))]
    if path.suffix.lower() == '.png':
        render_snapshot(**frames[-1], title=captured['label'], width=width).save(path)
    else:
        palette = _palette(frames)
        images = [_quantize(render_snapshot(**frame, title=captured['label'], width=width), palette) for frame in frames]
        images[0].save(path, save_all=True, append_images=images[1:], duration=durations,
                       loop=0, optimize=True, disposal=1, comment=b'Production Broker/Renderer simulation. Time jumps are captioned.')
    return {'scenario': captured['name'], 'output': str(path.resolve()), 'frames': len(frames), 'fps': fps,
            'playback_seconds': sum(durations)/1000, 'simulation_seconds': captured['simulation_seconds'],
            'time_jumps': captured['time_jumps'], 'truncated': captured['truncated'], 'bytes': path.stat().st_size}


def export_scenario(name=None, output=None, *, scenario=None, profile=None, seed=1, fps=12,
                    width=960, max_frames=600):
    """Export a reproducible narrated GIF, or the final scene as a PNG."""
    captured = capture_scenario(name or scenario, profile=profile, seed=seed, fps=fps, max_frames=max_frames)
    return _save_capture(captured, output, width=width)


def export_design(design, output, *, profile=None, duration=10, seed=1, fps=12,
                  width=960, max_frames=600, frame_callback=None):
    """Export user JSON/timeline or an explicitly loaded Python frame callback.

    This function never imports a plugin path itself. Its caller chooses whether
    to execute a Python callback; JSON remains data interpreted by the simulator.
    """
    from .engine import DEFAULT_PROFILE, Simulator
    if type(fps) is not int or not 1 <= fps <= 30:
        raise ValueError('Export fps must be an integer in 1..30')
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise ValueError('Export frame limit must be 1..1200')
    if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError('Export duration must be within 0..3600 seconds')
    simulator = Simulator(profile=profile or DEFAULT_PROFILE, seed=seed, frame_callback=frame_callback)
    simulator.load_design(deepcopy(design))
    total = max(1, math.ceil(duration*fps))
    count = min(total, max_frames)
    frames = []
    for index in range(count):
        snapshot = simulator.snapshot()
        _keys(snapshot)
        frames.append({'snapshot': {key: deepcopy(snapshot[key]) for key in
                      ('time','profile','active','navigation_active','knob_mode','sort_policy',
                       'page','page_count','agents','keys')},
                       'caption': 'User design: ' + str(design.get('name', 'Untitled')),
                       'action_keys': (), 'progress': (index+1)/total})
        simulator.advance(min(1/fps, duration-index/fps))
    if count < total:
        frames[-1]['caption'] = 'Preview stopped at the requested frame limit; the design is incomplete.'
    captured = {'name':str(design.get('name', 'User design')), 'label':str(design.get('name', 'User design')),
                'frames':frames, 'fps':fps, 'simulation_seconds':simulator.time,
                'time_jumps':[], 'truncated':count < total}
    return _save_capture(captured, output, width=width)
