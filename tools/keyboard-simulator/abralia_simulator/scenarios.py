# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Narrated examples; all actions run through the shared production simulator."""

from copy import deepcopy


def _input(key, gesture='tap'):
    return {'type': 'input', 'key': key, 'gesture': gesture}


def _step(caption, seconds, *actions, keys=(), jump=0):
    return {'caption': caption, 'seconds': seconds, 'actions': list(actions),
            'keys': list(keys), 'jump': jump}


def _agents(count, grouped=False):
    return [{'id': f'a{i}', 'project': 'Atlas' if not grouped or i % 2 else 'Beacon',
             'label': f'Task {i}', 'state': 'progressing'} for i in range(1, count + 1)]


def _scenario(label, description, agents, segments):
    elapsed, timeline = 0., []
    for segment in segments:
        elapsed += segment['jump']
        for action in segment['actions']:
            timeline.append({'at': round(elapsed, 3), 'action': dict(action)})
        elapsed += segment['seconds']
    return {'label': label, 'description': description, 'segments': segments,
            'design': {'version': 1, 'name': label, 'seed_agents': agents,
                       'timeline': timeline, 'frames': []}}


SCENARIOS = {
    'mode-navigation': _scenario('Agent Mode and navigation',
        'Double-tap activation, the temporary navigation layer, and independent inactivity timeouts.',
        _agents(3), [
            _step('Ordinary typing: occupied task slots remain visible.', .9),
            _step('Double-tap Pause: enter Agent Mode.', 1., _input('MODE', 'double'), keys=('MODE',)),
            _step('Hold Pause: arm the navigation keys.', 1., {'type': 'press', 'key': 'MODE'}, keys=('MODE',)),
            _step('Release Pause. Page, slot, and boundary keys have separate colors.', .7, {'type': 'release_key', 'key': 'MODE'}),
            _step('Tap Right: preview the next task. Enter would confirm.', .8, _input('RIGHT'), keys=('RIGHT',)),
            _step('15 seconds later, without navigation: the key layer times out.', 1.2, jump=15.1),
            _step('Press the knob: browse individual tasks.', .8, _input('KNOB_PRESS'), keys=('KNOB_PRESS',)),
            _step('Turn clockwise: move the preview, without opening a task.', .8, _input('KNOB_CW'), keys=('KNOB_PRESS',)),
            _step('60 seconds later, without selection: the knob returns to pages.', 1.2, jump=60.1),
            _step('Double-tap Pause: return to ordinary typing.', 1., _input('MODE', 'double'), keys=('MODE',)),
        ]),
    'notifications': _scenario('Calls, pickup, mute and fog',
        'Arrival and condensation use their production timing; long waits are explicit time jumps.',
        _agents(2), [
            _step('Double-tap Pause: call controls work in Agent Mode.', .6, _input('MODE', 'double'), keys=('MODE',)),
            _step('A task calls: yellow-green arrival, then its own identity color.', 5., {'type': 'notify', 'agent_id': 'a1'}),
            _step('Breathing settles, then the call condenses into a fog reminder.', 6.1),
            _step('Another task calls while the first reminder remains.', .7, {'type': 'notify', 'agent_id': 'a2'}),
            _step('Tap Scroll Lock: mute the oldest call; the next queued call becomes available.', 1., _input('SCROLL_LOCK'), keys=('SCROLL_LOCK',)),
            _step('Tap F1: pick up the first task and remove its reminder.', 1., _input('F1'), keys=('F1',)),
            _step('15 seconds later: the picked-up background fades back.', 5., jump=15.),
            _step('2 minutes later: the remaining fog is fading with age.', 1.1, jump=120.),
            _step('1 minute later: the expired reminder is gone.', 1.1, jump=60.),
        ]),
    'paging-sorting': _scenario('Pages and project grouping',
        'Page changes, direct page digits, project sorting and insertion of a new task.',
        _agents(14, grouped=True), [
            _step('Double-tap Pause. Fourteen tasks occupy two pages.', .8, _input('MODE', 'double'), keys=('MODE',)),
            _step('Turn clockwise: page 2. Orange marks the current page.', 1., _input('KNOB_CW'), keys=('KNOB_PRESS',)),
            _step('Press the knob: task selection keeps the page digits available.', .8, _input('KNOB_PRESS'), keys=('KNOB_PRESS',)),
            _step('Tap 1: jump directly to page 1.', .8, _input('1'), keys=('1',)),
            _step('Hold Pause: enable keyboard navigation.', 1., {'type': 'press', 'key': 'MODE'}, keys=('MODE',)),
            _step('Release Pause. Escape now changes the sorting policy.', .6, {'type': 'release_key', 'key': 'MODE'}),
            _step('Tap Escape: group tasks by project, with related hues.', 1.5, _input('ESC'), keys=('ESC',)),
            _step('New Atlas task: inserted into the Atlas group; existing IDs remain.', 1.8,
                  {'type': 'add_agent', 'agent_id': 'a15', 'project': 'Atlas', 'label': 'New Atlas task'}),
            _step('Tap Escape again: restore incoming order.', 1.3, _input('ESC'), keys=('ESC',)),
            _step('Page Down: the new arrival is last, on page 2.', 1., _input('PAGE_DOWN'), keys=('PAGE_DOWN',)),
        ]),
    'attention': _scenario('Ten-minute attention controls',
        'Delete mutes one task, Insert allows only one task, and restores use the positive color.',
        _agents(3), [
            _step('Double-tap Pause: enter Agent Mode.', .6, _input('MODE', 'double'), keys=('MODE',)),
            _step('Hold Pause: arm navigation and attention controls.', 1., {'type': 'press', 'key': 'MODE'}, keys=('MODE',)),
            _step('Release, then tap Right: target the previewed task.', .7,
                  {'type': 'release_key', 'key': 'MODE'}, _input('RIGHT'), keys=('RIGHT',)),
            _step('Tap Delete: mute this task for ten minutes.', 1., _input('DELETE'), keys=('DELETE',)),
            _step('Delete is now positive: tap again to restore the task.', .9, _input('DELETE'), keys=('DELETE',)),
            _step('Mute it again, then hold Delete to restore all individual mutes.', .6, _input('DELETE'), keys=('DELETE',)),
            _step('Hold Delete for 1.2 seconds: clear individual mutes.', 1.3, {'type': 'press', 'key': 'DELETE'}, keys=('DELETE',)),
            _step('Release Delete. No extra tap action occurs.', .6, {'type': 'release_key', 'key': 'DELETE'}),
            _step('Tap Insert: allow only the previewed task. Delete is unavailable.', 1.2, _input('INSERT'), keys=('INSERT',)),
            _step('Tap Insert again: restore every task.', .9, _input('INSERT'), keys=('INSERT',)),
            _step('Enable only-this-task once more to demonstrate its timer.', .7, _input('INSERT'), keys=('INSERT',)),
            _step('10 minutes later: the policy expires; every task is unmuted.', 1.2, jump=600.1),
        ]),
    'gap-close': _scenario('Close a whole empty gap',
        'A hold on any gap key compacts later tasks across pages while keeping allocation IDs.',
        _agents(16), [
            _step('Double-tap Pause: gap closing works throughout Agent Mode.', .9, _input('MODE', 'double'), keys=('MODE',)),
            _step('Four tasks leave: F2 through F5 become one empty gap.', 1.5,
                  *({'type': 'release', 'agent_id': f'a{i}'} for i in range(2, 6))),
            _step('Hold F3: all keys in the F2-F5 gap brighten together.', 1.35,
                  {'type': 'press', 'key': 'F3'}, keys=('F3',)),
            _step('Release after the flash. Later tasks fill the gap across pages.', 1.3,
                  {'type': 'release_key', 'key': 'F3'}),
            _step('The layout is compact; each task keeps its ID, color and state.', 1.4),
        ]),
    'keyboard-guide': _scenario('A keymap guide after pickup',
        'Guide colors stay pending until pickup, and Escape restores the normal display.',
        _agents(1), [
            _step('Double-tap Pause: enter Agent Mode.', .6, _input('MODE', 'double'), keys=('MODE',)),
            _step('The agent requests a guide; it waits for your pickup.', .9,
                  {'type': 'guide', 'agent_id': 'a1', 'colors': {'W': '2080FF', 'A': '2080FF', 'S': '2080FF', 'D': '2080FF', 'ENTER': 'A0FF00'}}),
            _step('Tap Print Screen: pick up and reveal the guide.', 1.5, _input('SCREENSHOT'), keys=('SCREENSHOT',)),
            _step('Tap W: highlighted guide keys still pass through normally.', 1.1, _input('W'), keys=('W',)),
            _step('Tap Escape: close the guide and restore the normal display.', 1.5, _input('ESC'), keys=('ESC',)),
        ]),
}


def get_scenario(name, profile=None):
    try:
        scenario = deepcopy(SCENARIOS[name])
    except KeyError as error:
        raise ValueError('Unknown scenario: ' + str(name)) from error
    if profile is None:
        return scenario
    if isinstance(profile, dict):
        toggle = profile.get('toggle_key', 'PAUSE')
        encoder = profile.get('has_encoder', False)
    else:
        toggle = profile.interaction_toggle_element_id()
        encoder = bool(profile.device_profile.keymap.encoder_count)
    if not encoder and name == 'mode-navigation':
        scenario['segments'] = [step for step in scenario['segments']
                                if not any(key.startswith('KNOB') for key in step['keys']) and step['jump'] != 60.1]
        scenario['segments'][-2]['caption'] = '15 seconds later: navigation times out. This model has no encoder.'
    elif not encoder and name == 'paging-sorting':
        scenario['segments'] = [
            _step('Double-tap Pause. Fourteen tasks occupy two pages.', .8, _input('MODE', 'double'), keys=('MODE',)),
            _step('Hold Pause: navigate using keys on this model without an encoder.', 1., {'type': 'press', 'key': 'MODE'}, keys=('MODE',)),
            _step('Release Pause. Navigation keys are now armed.', .6, {'type': 'release_key', 'key': 'MODE'}),
            _step('Tap Page Down: page 2. Orange marks the current page.', 1., _input('PAGE_DOWN'), keys=('PAGE_DOWN',)),
            _step('Tap Page Up: return to page 1.', 1., _input('PAGE_UP'), keys=('PAGE_UP',)),
            *scenario['segments'][6:],
        ]
    label = 'Pause' if toggle == 'PAUSE' else 'lighting key' if toggle == 'LIGHTING_KEY' else toggle.replace('_', ' ').title()
    for segment in scenario['segments']:
        segment['caption'] = segment['caption'].replace('Pause', label)
    return _scenario(scenario['label'], scenario['description'], scenario['design']['seed_agents'], scenario['segments'])
