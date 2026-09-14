# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Virtual-time Broker/Renderer host. Never constructs a device or opens an app."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from abralia.backend.core import Broker, BrokerConfig, Caller
from abralia.backend.input_routes import routes_for, dispatch_event
from abralia.backend.navigation import ModeKeyHold
from abralia.backend.render import Renderer
from abralia.backend.notification_animation import COLORS
from abralia.interaction.protocol import ControlId, DeviceEvent, Edge, EventFlags, EventType
from abralia.rgb import load_profile, PhysicalSceneBuilder, Srgb8
from abralia.rgb.colors import to_srgb8

PROFILES = (
    {'id': 'builtin:keychron-v3-8k-ansi-encoder-effect25', 'name': 'Keychron V3 8K ANSI Encoder'},
    {'id': 'builtin:keychron-v3-ansi-encoder-effect25', 'name': 'Keychron V3 ANSI Encoder'},
    {'id': 'builtin:keychron-v3-ansi-effect25', 'name': 'Keychron V3 ANSI'},
)
DEFAULT_PROFILE = PROFILES[0]['id']
MAX_DESIGN_EVENTS = 2000
MAX_DESIGN_FRAMES = 1000


def _number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return float(value)


def _text(value, name, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{name} must be nonempty text of at most {maximum} characters')
    return value


def _hex(value):
    if not isinstance(value, str):
        raise ValueError('colors must be six-digit RGB strings')
    value = value.removeprefix('#').upper()
    if len(value) != 6 or any(c not in '0123456789ABCDEF' for c in value):
        raise ValueError('colors must be six-digit RGB strings')
    return value


def _rgb_hex(value):
    rgb = to_srgb8(value)
    return f'{rgb.red:02X}{rgb.green:02X}{rgb.blue:02X}'


class _SimulationBroker(Broker):
    def __init__(self, *args, seed=1, **kwargs):
        self.seed = seed
        super().__init__(*args, **kwargs)

    def notification_visuals(self):
        # Only the fog placement seed changes: lifecycle and renderer math are
        # production behavior. Opaque real allocation tokens are not exported.
        result = super().notification_visuals()
        mapping = {}
        for orb in result['orbs']:
            slot = self.slots.get(orb['slot_id'])
            mapping[orb['orb_key']] = f'simulator:{self.seed}:{slot.slot_id}:{slot.arrival_order}' if slot else orb['orb_key']
            orb['orb_key'] = mapping[orb['orb_key']]
        if result['presentation']:
            key = result['presentation']['orb_key']
            result['presentation']['orb_key'] = mapping.get(key, key)
        return result


class Simulator:
    def __init__(self, profile=DEFAULT_PROFILE, *, seed=1, config=None, frame_callback=None):
        self.lock = threading.RLock()
        self.seed = int(seed)
        self.profile_source = str(profile)
        self.profile = load_profile(profile)
        self._elements = self.profile.element_by_id
        self._rgb_keys = frozenset(e.element_id for e in self.profile.rgb_elements)
        self.device_profile = self.profile.device_profile
        self._routing_profile = SimpleNamespace(element_by_id=self._elements,device_profile=self.device_profile)
        self.config = config if isinstance(config, BrokerConfig) else BrokerConfig(**(config or {}))
        self.frame_callback = frame_callback
        # Validate compatibility before publishing a model, without a driver.
        required = ('ESC', 'ENTER', 'INSERT', 'DELETE', 'HOME', 'END', 'PAGE_UP', 'PAGE_DOWN',
                    'LEFT', 'RIGHT', 'UP', 'DOWN', 'SCREENSHOT', 'SCROLL_LOCK',
                    *(f'F{i}' for i in range(1,13)), *'1234567890')
        self.interaction_available = True
        self.interaction_error = None
        try:
            if any(key not in self._elements or self._elements[key].matrix is None for key in required):
                raise ValueError('profile lacks physical controls required by the production interaction')
            Renderer(self.profile, fps=self.config.fps)
        except (ValueError, KeyError) as error:
            self.interaction_available = False
            self.interaction_error = str(error)
        self._design = {'version': 1, 'name': 'Blank keyboard', 'seed_agents': [], 'timeline': [], 'frames': []}
        self.scenario = None
        self.reset()

    def profile_info(self):
        return {'id': self.profile_source if self.profile_source.startswith('builtin:') else 'custom',
                'native_id':self.profile.profile_id, 'name': self.profile.display_name,
                'toggle_key': self.profile.interaction_toggle_element_id() if self.interaction_available else None,
                'interaction_available':self.interaction_available, 'interaction_error':self.interaction_error,
                'has_encoder': bool(self.device_profile.keymap.encoder_count)}

    def _new_broker(self):
        self.broker = _SimulationBroker(self.config, clock=lambda: self.time, seed=self.seed)
        self.broker.delivery = 'simulated'
        self.broker.keyboard_frame_keys = frozenset(e.element_id for e in self.profile.rgb_elements) if self.interaction_available else frozenset()
        self.broker.keyboard_frame_mode_key = self.profile_info()['toggle_key']
        self.renderer = Renderer(self.profile, fps=self.config.fps) if self.interaction_available else None
        self.mode_hold = None
        if self.interaction_available:
            self.mode_hold = ModeKeyHold(self.device_profile.require_interaction().toggle_matrix,
                                        hold_seconds=self.config.navigation_hold_seconds)
            self.mode_hold.sample([0] * self.device_profile.keymap.matrix_rows, self.time)

    def reset(self, scenario=None):
        with self.lock:
            if scenario is not None:
                return self.load_scenario(scenario)
            self.time, self.playing, self.speed = 0., False, 1.
            self.agents, self.pressed = {}, {}
            self._serial, self._operation, self._timeline_index = 0, 0, 0
            self._routes, self._generation, self._frame_overrides = {}, 0, {}
            self.error = None
            self._new_broker()
            for agent in self._design['seed_agents']:
                agent_id = self.add_agent(agent['project'], agent['label'], agent_id=agent.get('id'))
                if agent.get('state'):
                    self.set_state(agent_id, agent['state'])
            self._due_actions()
            self._tick()
            return self.snapshot()

    def _call(self, agent_id, operation, **arguments):
        record = self.agents.get(agent_id)
        if record is None:
            raise ValueError('unknown simulated agent')
        self._operation += 1
        if operation != 'get_status':
            arguments['idempotency_key'] = f'sim:{self._operation}'
        if operation != 'acquire_slot':
            arguments['slot_token'] = record['token']
        result = self.broker.call(record['caller'], operation, arguments)
        if result['status'] != 'accepted':
            raise ValueError(result.get('reason', 'simulation operation rejected'))
        return result

    def add_agent(self, project, label, agent_id=None):
        with self.lock:
            project, label = _text(project, 'project'), _text(label, 'label')
            if agent_id is None:
                agent_id = f'agent-{self._serial+1}'
            agent_id = _text(agent_id, 'agent id')
            if agent_id in self.agents:
                raise ValueError('agent id already exists')
            self._serial += 1
            thread = str(uuid5(NAMESPACE_URL, f'abralia-simulator:{self.seed}:{agent_id}:{self._serial}'))
            caller = Caller('codex:'+thread, thread, surface='codex_desktop',
                            project_id=hashlib.sha256(project.encode()).hexdigest()[:32])
            self.agents[agent_id] = {'caller': caller, 'project': project, 'label': label, 'token': None}
            try:
                result = self._call(agent_id, 'acquire_slot', label=label, harness='codex_desktop')
                self.agents[agent_id]['token'] = result['allocation']['slot_token']
            except Exception:
                del self.agents[agent_id]
                raise
            self._tick()
            return agent_id

    def notify(self, agent_id, enabled=True):
        with self.lock:
            if type(enabled) is not bool: raise ValueError('notification enabled must be boolean')
            result = self._call(agent_id, 'set_notification', enabled=enabled)
            self._tick()
            return result['status']

    def release(self, agent_id):
        with self.lock:
            self._call(agent_id, 'release_slot')
            del self.agents[agent_id]
            self._tick()

    def set_state(self, agent_id, state, progress=None):
        with self.lock:
            self._call(agent_id, 'set_slot_state', state=state, progress=progress)
            self._tick()

    def guide(self, agent_id, colors):
        with self.lock:
            self._call(agent_id, 'show_keyboard_frame', colors=colors)
            self._tick()

    def notification_animation(self, agent_id, animation):
        with self.lock:
            self._call(agent_id, 'set_notification_animation', animation=animation)
            self._tick()

    reportguide = guide

    def _key(self, key):
        key = _text(key, 'key')
        if key == 'MODE':
            if not self.interaction_available: raise ValueError('interaction unavailable for this profile')
            return self.profile_info()['toggle_key']
        if key in ('KNOB_CW', 'KNOB_CCW'):
            if not self.device_profile.keymap.encoder_count:
                raise ValueError('this model has no encoder')
            return key
        key = self.profile.resolve_element_id(key)
        if key not in self._elements:
            raise ValueError('unknown physical key')
        return key

    def _control(self, key):
        if key in ('KNOB_CW', 'KNOB_CCW'):
            return (ControlId.encoder_clockwise if key == 'KNOB_CW' else ControlId.encoder_counterclockwise)(0)
        matrix = self._elements[key].matrix
        encoder = self._elements[key].encoder
        if encoder is not None:
            return (ControlId.encoder_clockwise if encoder.direction.value == 'clockwise' else ControlId.encoder_counterclockwise)(encoder.index)
        return ControlId.key(*matrix) if matrix else None

    def _update_routes(self):
        routes = routes_for(self.broker, self._routing_profile, hold_enabled=True) if self.interaction_available else {}
        if routes != self._routes:
            self._generation += 1
            self._routes = routes

    def press(self, key):
        with self.lock:
            key = self._key(key)
            if key in self.pressed:
                return
            self._update_routes()
            match = next(((identity, route) for identity, route in self._routes.items() if route.control == self._control(key)), None)
            row = {'key': key, 'at': self.time, 'route': match, 'generation': self._generation,
                   'consumed': False, 'gap': None, 'active': self.broker.active}
            self.pressed[key] = row
            if match and self.broker.active and match[1].action == 'close_gap':
                row['gap'] = self.broker.begin_gap_hold(match[1].display_position, match[1].layout_revision)
                row['consumed'] = row['gap'] is None
            self._sample_mode()

    def _sample_mode(self):
        if self.mode_hold is None:
            return
        if self.broker.visible_keyboard_frame():
            self.mode_hold.reset()
            return
        matrix = [0] * self.device_profile.keymap.matrix_rows
        for key in self.pressed:
            element = self._elements.get(key)
            if element and element.matrix:
                row, column = element.matrix
                matrix[row] |= 1 << column
        if self.mode_hold.sample(matrix, self.time) and self.broker.active:
            self.broker.toggle_navigation()

    def release_key(self, key, duration=None):
        with self.lock:
            key = self._key(key)
            row = self.pressed.get(key)
            if row is None:
                return
            if duration is not None:
                target = _number(duration, 'hold duration', 0, 30)
                self.advance(max(0., target-(self.time-row['at'])))
            row = self.pressed.pop(key)
            self._sample_mode()
            if key == self.profile_info()['toggle_key']:
                self.mode_hold.release_event(self.time)
                return
            if row['gap'] is not None:
                self.broker.cancel_gap_hold('released')
                return
            if row['route'] and row['active'] and self.broker.active and not row['consumed']:
                identity, route = row['route']
                event = DeviceEvent(EventType.CONTROL_EDGE, 1, self._operation+1, row['generation'], identity,
                                    route.control, Edge.UP.value, EventFlags(0), round(self.time*1000))
                # Use the captured DOWN target. Semantic generation checks in
                # production routes reject remapped F keys, guides and cursors.
                dispatch_event(self.broker, event, {identity: route}, row['generation'])
            self._tick()

    def input(self, key, gesture='tap', duration=None):
        with self.lock:
            key = self._key(key)
            if gesture not in ('tap', 'double', 'hold'):
                raise ValueError('unknown input gesture')
            if gesture == 'double' and key == self.profile_info()['toggle_key']:
                self.pressed.pop(key, None)
                self.mode_hold.reset()
                self.broker.set_active(not self.broker.active)
                self._sample_mode(); self._tick()
                return self.snapshot()
            self.press(key)
            if gesture == 'hold':
                self.advance(_number(1.3 if duration is None else duration, 'hold duration', 0, 30))
            self.release_key(key)
            if gesture == 'double':
                self.press(key); self.release_key(key)
            return self.snapshot()

    def _tick(self):
        self._sample_mode()
        for row in self.pressed.values():
            if row['consumed'] or not row['route'] or not row['active']:
                continue
            route = row['route'][1]
            if route.action == 'close_gap' and row['gap'] is not None:
                if not self.broker.gap_valid(row['gap']):
                    row['consumed'] = True; self.broker.cancel_gap_hold('input_or_layout_changed')
                elif self.time-row['at'] >= self.config.gap_close_hold_seconds:
                    row['consumed'] = True; self.broker.close_gap(row['gap'])
                else:
                    self.broker._navigation_activity()
            elif route.attention_key == 'DELETE':
                current = self.broker.attention_controls().get('DELETE')
                if (route.navigation_revision != self.broker.navigation_revision or
                        route.attention_revision != self.broker.attention_policy_revision or
                        current != (route.action.removeprefix('attention:'), route.token)):
                    row['consumed'] = True
                elif self.time-row['at'] >= self.config.restore_all_hold_seconds:
                    row['consumed'] = True
                    self.broker.restore_individual_mutes(route.attention_revision)
        self.broker.step()
        if self.broker.focus_requests:
            self.broker.focus_requests.clear()
            self.broker.event('simulated_task_open')
        self._update_routes()
        visuals = self.broker.notification_visuals()
        if self.renderer is not None:
            self.renderer.fog.update(visuals['orbs'], self.time)

    def _due_actions(self):
        timeline = self._design['timeline']
        while self._timeline_index < len(timeline) and timeline[self._timeline_index]['at'] <= self.time+1e-9:
            row = timeline[self._timeline_index]; self._timeline_index += 1
            try:
                self.apply_action(row['action'], _timeline=True)
            except (ValueError, TypeError) as error:
                self.error = str(error)
                self.playing = False
                self.broker.event('design_action_rejected',reason=self.error)

    def advance(self, seconds):
        with self.lock:
            seconds = _number(seconds, 'advance seconds', 0, 3600)
            end = self.time + seconds
            while self.time < end-1e-9:
                visual = self.broker.notification_visuals()
                quantum = .05 if self.pressed or visual['orbs'] or visual['presentation'] else 1.
                next_event = (self._design['timeline'][self._timeline_index]['at']
                              if self._timeline_index < len(self._design['timeline']) else end)
                self.time = min(end, self.time + quantum, next_event if next_event>self.time+1e-9 else end)
                self._due_actions(); self._tick()
            self.time = end
            self._due_actions(); self._tick()
            return self.snapshot()

    def _colors(self, values):
        if not isinstance(values, dict) or len(values) > len(self.profile.elements):
            raise ValueError('frame colors must map physical keys to RGB')
        colors = {}
        for key, value in values.items():
            key = self._key(key)
            if key not in self._rgb_keys:
                raise ValueError('frame key has no RGB LED')
            colors[key] = _hex(value)
        return colors

    def load_design(self, design):
        with self.lock:
            if not isinstance(design, dict) or set(design)-{'version','name','seed_agents','timeline','frames','config'} or type(design.get('version')) is not int or design['version'] != 1:
                raise ValueError('expected a version 1 simulator design')
            normalized = {'version':1, 'name':_text(design.get('name','Imported design'),'design name'),
                          'seed_agents':deepcopy(design.get('seed_agents',[])), 'timeline':deepcopy(design.get('timeline',[])), 'frames':deepcopy(design.get('frames',[]))}
            agents, timeline, frames = normalized['seed_agents'], normalized['timeline'], normalized['frames']
            if not isinstance(agents,list) or len(agents)>1024 or not isinstance(timeline,list) or len(timeline)>MAX_DESIGN_EVENTS or not isinstance(frames,list) or len(frames)>MAX_DESIGN_FRAMES:
                raise ValueError('design payload exceeds simulator request limits')
            ids = set()
            for index, agent in enumerate(agents):
                if not isinstance(agent,dict) or set(agent)-{'id','label','project','state'}:
                    raise ValueError('invalid seed agent')
                _text(agent.get('project'),'project'); _text(agent.get('label'),'label')
                agent['id'] = agent.get('id',f'agent-{index+1}')
                _text(agent['id'],'agent id')
                if agent['id'] in ids: raise ValueError('duplicate seed agent id')
                ids.add(agent['id'])
                if agent.get('state') not in (None,'idle','progressing','error','action_requested','completed'):
                    raise ValueError('invalid seed state')
            for row in timeline:
                if not isinstance(row,dict) or set(row)-{'at','action'}: raise ValueError('invalid timeline entry')
                row['at'] = _number(row.get('at'),'timeline time',0,86400)
                self._validate_action(row.get('action'), timeline=True)
            timeline.sort(key=lambda row:row['at'])
            available, serial = set(ids), len(agents)
            for row in timeline:
                action = row['action']
                if action['type']=='add_agent':
                    serial += 1
                    identity = action.setdefault('agent_id',f'agent-{serial}')
                    if identity in available: raise ValueError('timeline agent id already exists')
                    available.add(identity)
                elif action['type'] in ('notify','release','set_state','guide','notification_animation'):
                    if action['agent_id'] not in available: raise ValueError('timeline refers to an unknown agent')
                    if action['type']=='release': available.remove(action['agent_id'])
            for frame in frames:
                if not isinstance(frame,dict) or set(frame)-{'at','colors','background'}: raise ValueError('invalid frame')
                frame['at'] = _number(frame.get('at'),'frame time',0,86400)
                frame['colors'] = self._colors(frame.get('colors',{}))
                if 'background' in frame: frame['background'] = _hex(frame['background'])
            frames.sort(key=lambda row:row['at'])
            if 'config' in design:
                if not isinstance(design['config'],dict): raise ValueError('invalid broker config')
                config = BrokerConfig(**design['config'])
                normalized['config'] = deepcopy(design['config'])
            else: config = self.config
            # Check initial policy execution in a disposable virtual broker.
            # A rejected initial action must not destroy the current design.
            trial = Simulator(self.profile_source,seed=self.seed,config=config)
            trial._design = deepcopy(normalized)
            trial.reset()
            if trial.error: raise ValueError(trial.error)
            self._design, self.config, self.scenario = normalized, config, None
            return self.reset()

    def design(self):
        with self.lock: return deepcopy(self._design)

    def load_scenario(self, name):
        from . import scenarios
        SCENARIOS = scenarios.SCENARIOS
        if name not in SCENARIOS: raise ValueError('unknown scenario')
        row = scenarios.get_scenario(name, self.profile) if hasattr(scenarios,'get_scenario') else SCENARIOS[name]
        self.load_design(row['design'])
        self.scenario = name
        return self.snapshot()

    def _validate_action(self, action, *, timeline=False):
        fields = {'input': {'key','gesture','duration'}, 'press': {'key'}, 'release_key': {'key','duration'},
                  'add_agent': {'project','label','agent_id'}, 'notify': {'agent_id','enabled'}, 'release': {'agent_id'},
                  'set_state': {'agent_id','state','progress'}, 'guide': {'agent_id','colors'},
                  'notification_animation': {'agent_id','animation'},
                  'background': {'percent'}, 'play': {'enabled'}, 'speed': {'value'}, 'advance': {'seconds'},
                  'scenario': {'name'}}
        if not isinstance(action,dict) or action.get('type') not in fields or set(action)-({'type'}|fields[action['type']]):
            raise ValueError('unknown action or unexpected argument')
        if timeline and action['type'] in ('advance','play','speed','scenario'):
            raise ValueError('timeline cannot control its own clock or load code/designs')
        if timeline and (action['type']=='input' and action.get('gesture')=='hold' or action['type']=='release_key' and 'duration' in action):
            raise ValueError('timeline holds require separate press and release_key events')
        kind = action['type']
        if kind in ('input','press','release_key'):
            self._key(action.get('key'))
            if kind=='input' and action.get('gesture','tap') not in ('tap','double','hold'): raise ValueError('invalid gesture')
            if 'duration' in action: _number(action['duration'],'duration',0,30)
        if kind in ('notify','release','set_state','guide','notification_animation'):
            _text(action.get('agent_id'),'agent id')
        if kind=='add_agent':
            _text(action.get('project'),'project'); _text(action.get('label'),'label')
            if 'agent_id' in action: _text(action['agent_id'],'agent id')
        if kind=='set_state':
            if action.get('state') not in ('idle','progressing','error','action_requested','completed'): raise ValueError('invalid state')
            if action.get('progress') is not None: _number(action['progress'],'progress',0,1)
        if kind=='guide':
            colors = action.get('colors')
            if not isinstance(colors,dict) or not 1 <= len(colors) <= 128: raise ValueError('guide requires 1 to 128 colors')
            for key,color in colors.items():
                if key not in self.broker.keyboard_frame_keys: raise ValueError('guide key has no supported RGB control')
                if color not in (*COLORS,'white','off'): _hex(color)
        if kind=='notify' and 'enabled' in action and type(action['enabled']) is not bool: raise ValueError('notification enabled must be boolean')
        if kind=='background': _number(action.get('percent'),'background',0,100)
        if kind=='speed': _number(action.get('value'),'speed',.1,16)
        if kind=='advance': _number(action.get('seconds'),'seconds',0,3600)

    def apply_action(self, action, *, _timeline=False):
        with self.lock:
            self._validate_action(action,timeline=_timeline)
            kind = action['type']; a = {k:v for k,v in action.items() if k!='type'}
            if kind in ('input','press','release_key','add_agent','notify','release','set_state','guide','notification_animation'):
                getattr(self,kind)(**a)
            elif kind == 'background':
                self.broker.set_background_brightness(_number(a.get('percent'),'background',0,100))
            elif kind == 'play':
                if type(a.get('enabled')) is not bool: raise ValueError('playing must be boolean')
                self.playing = a['enabled']
            elif kind == 'speed': self.speed = _number(a.get('value'),'speed',.1,16)
            elif kind == 'advance': return self.advance(a.get('seconds'))
            elif kind == 'scenario': return self.load_scenario(a.get('name'))
            self._tick()
            return self.snapshot()

    action = apply_action

    def snapshot(self):
        with self.lock:
            if self.renderer is None:
                value = round(self.broker.config.background_brightness_percent*255/100)
                scene = PhysicalSceneBuilder().build('render-only',{},background=Srgb8(value,value,value)).payload
            else:
                scene = self.renderer.frame(self.broker).payload
            background = _rgb_hex(scene.background)
            colors = {element.element_id:_rgb_hex(scene.colors.get(element.element_id,scene.background)) for element in self.profile.rgb_elements}
            # Imported frame clips are deliberately simulator-only overlays.
            frame = next((row for row in reversed(self._design['frames']) if row['at'] <= self.time), None)
            if frame:
                background = frame.get('background',background)
                if 'background' in frame: colors = dict.fromkeys(colors,background)
                colors.update(frame['colors'])
            agents = []
            for identity, record in self.agents.items():
                slot = self.broker.slots.get(self.broker.owners.get(record['caller'].caller_id))
                if slot:
                    agents.append({'id':identity,'label':record['label'],'project':record['project'],'slot_id':slot.slot_id,
                        'page':(slot.position-1)//12+1,'f_key':slot.f_key,'color':slot.identity_color,'state':slot.state,
                        'notice':slot.notification.status if slot.notification else None,'muted':self.broker.attention_muted(slot),
                        'selected':self.broker.selected==slot.slot_id,'previewed':self.broker.cursor_token==slot.slot_token,
                        'display_position':slot.position})
            bindings = []
            if self.broker.active:
                for route in self._routes.values():
                    key = next((e.element_id for e in self.profile.elements if self._control(e.element_id)==route.control),None)
                    if key is None: key = 'KNOB_CW' if route.action=='next_page' else 'KNOB_CCW'
                    bindings.append({'key':key,'action':route.action})
            visuals = self.broker.notification_visuals()
            state = {'time':self.time,'playing':self.playing,'speed':self.speed,'profile':self.profile_info(),
                'background_percent':self.broker.config.background_brightness_percent,
                'frame_source':'plugin' if self.frame_callback else 'frames' if frame else 'renderer',
                'design_duration':max([row['at'] for row in self._design['timeline']]+[row['at'] for row in self._design['frames']]+[0]),
                'design_name':self._design['name'],'error':self.error,'active':self.broker.active,'navigation_active':self.broker.navigation_active,
                'knob_mode':self.broker.knob_mode,'sort_policy':self.broker.sort_policy,'page':self.broker.page+1,
                'page_count':self.broker.page_count,'agents':agents,'bindings':bindings,
                'effect_phase':visuals['presentation']['phase'] if visuals['presentation'] else 'fog' if visuals['orbs'] else 'idle',
                'orb_count':len(visuals['orbs']),'background':background,'colors':colors,
                'events':[{k:v for k,v in row.items() if k in ('kind','at','slot_id','previous_position','display_position','policy','reason','key','gesture','duration','active','action')}
                          for row in list(self.broker.events)[-60:]]}
            def keys():
                return [{'id':e.element_id,'label':e.element_id,'type':e.element_type,'x':e.geometry.x,'y':e.geometry.y,
                         'w':e.geometry.width,'h':e.geometry.height,'rgb':e.rgb_capable,
                         'led':colors.get(e.element_id),'pressed':e.element_id in self.pressed}
                        for e in self.profile.elements]
            if self.frame_callback is not None:
                state['keys'] = keys()
                overlay = self.frame_callback(self.time, self.profile_info(), deepcopy(state))
                if not isinstance(overlay,dict) or set(overlay)-{'colors','background'}: raise ValueError('plugin must return colors/background data')
                if 'background' in overlay:
                    state['background'] = _hex(overlay['background']); colors = dict.fromkeys(colors,state['background'])
                colors.update(self._colors(overlay.get('colors',{}))); state['colors'] = colors
            state['keys'] = keys()
            return state
