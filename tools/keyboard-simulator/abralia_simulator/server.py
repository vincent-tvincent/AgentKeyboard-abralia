# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Loopback-only simulator server. HTTP accepts data, never filesystem paths/code."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit, parse_qs

from .engine import Simulator, PROFILES

MAX_BODY = 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parents[1] / 'web'


class Controller:
    def __init__(self, simulator):
        self.simulator = simulator
        self.custom_profile = simulator.profile_source if not simulator.profile_source.startswith('builtin:') else None
        self.lock = threading.RLock()
        self.last_wall = time.monotonic()

    def state(self, *, advance=False):
        with self.lock:
            now = time.monotonic()
            elapsed, self.last_wall = now-self.last_wall, now
            if advance and self.simulator.playing:
                self.simulator.advance(min(max(elapsed, 0), .25)*self.simulator.speed)
            return self.simulator.snapshot()

    def bootstrap(self):
        from .scenarios import SCENARIOS
        with self.lock:
            profiles = list(PROFILES)
            if self.custom_profile: profiles.append({'id':'custom','name':'CLI custom profile'})
            return {'profiles':profiles, 'scenarios':[{'id':key,'name':value['label'],
                    'description':value.get('description','')} for key,value in SCENARIOS.items()],
                    'profile':self.simulator.profile_info(), 'state':self.state()}

    def action(self, payload):
        with self.lock:
            self.state(advance=True)
            return self.simulator.apply_action(payload)

    def reset(self, payload):
        with self.lock:
            if not isinstance(payload,dict) or set(payload)-{'profile','scenario'}:
                raise ValueError('reset accepts only profile/scenario')
            profile = payload.get('profile')
            if profile is not None:
                if profile=='custom' and self.custom_profile:
                    profile=self.custom_profile
                elif profile not in {row['id'] for row in PROFILES}:
                    raise ValueError('HTTP profiles must be bundled IDs; use CLI for a custom profile file')
                current = self.simulator
                replacement = Simulator(profile,seed=current.seed,config=current.config,frame_callback=current.frame_callback)
                scenario = payload.get('scenario',current.scenario)
                if scenario is not None: replacement.load_scenario(scenario)
                else: replacement.load_design(current.design())
                self.simulator = replacement
            else:
                self.simulator.reset(scenario=payload.get('scenario'))
            self.last_wall = time.monotonic()
            return self.state()


def create_server(simulator=None, *, port=0, web_root=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('port must be between 0 and 65535')
    controller = Controller(simulator or Simulator())
    assets = Path(web_root or WEB_ROOT).resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'AbraliaSimulator/1'

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_):
            pass

        def _trusted_request(self):
            host = self.headers.get('Host','')
            valid = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if host not in valid:
                return False
            origin = self.headers.get('Origin')
            return origin is None or origin in {'http://'+value for value in valid}

        def _send(self, status, data, *, content_type='application/json', attachment=False):
            body = (json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2 if attachment else None)
                    + ('\n' if attachment else '')).encode() if content_type=='application/json' else data
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            if attachment:
                self.send_header('Content-Disposition','attachment; filename="keyboard-design.json"')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._trusted_request(): return self._send(403,{'error':'cross-origin request rejected'})
            try:
                route = unquote(urlsplit(self.path).path)
                if route=='/api/bootstrap': return self._send(200,controller.bootstrap())
                if route=='/api/state': return self._send(200,controller.state(advance=True))
                if route=='/api/design': return self._send(200,controller.simulator.design())
                if route.startswith('/api/'): return self._send(404,{'error':'unknown endpoint'})
                if route=='/': route='/index.html'
                target = (assets/route.lstrip('/')).resolve()
                if not target.is_relative_to(assets) or target.suffix not in ('.html','.css','.js','.svg','.png','.ico','.json') or not target.is_file():
                    return self._send(404,{'error':'asset not found'})
                return self._send(200,target.read_bytes(),content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            except (ValueError,TypeError,OSError,KeyError) as error:
                return self._send(400,{'error':str(error)})

        def do_POST(self):
            if not self._trusted_request(): return self._send(403,{'error':'cross-origin request rejected'})
            route = urlsplit(self.path).path
            expected_type = 'application/x-www-form-urlencoded' if route=='/api/download' else 'application/json'
            if self.headers.get_content_type()!=expected_type:
                return self._send(415,{'error':expected_type+' required'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                limit = 3*MAX_BODY+7 if route=='/api/download' else MAX_BODY
                if not 0 < length <= limit: return self._send(413,{'error':'request body too large or empty'})
                raw = self.rfile.read(length)
                if route=='/api/download':
                    fields = parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True,max_num_fields=1,encoding='utf-8',errors='strict')
                    if set(fields)!={'design'} or len(fields['design'])!=1:
                        raise ValueError('download accepts only one design field')
                    raw = fields['design'][0]
                    if len(raw.encode('utf-8'))>MAX_BODY:
                        return self._send(413,{'error':'decoded design exceeds one megabyte'})
                payload = json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))
                if route=='/api/download':
                    return self._send(200,payload,attachment=True)
                if route=='/api/action':
                    if isinstance(payload,dict) and payload.get('type')=='advance' and (type(payload.get('seconds')) not in (int,float) or payload['seconds']>60):
                        raise ValueError('HTTP steps are limited to 60 seconds; longer jumps are available to local exporters')
                    result=controller.action(payload)
                elif route=='/api/reset': result=controller.reset(payload)
                elif route=='/api/design':
                    if isinstance(payload,dict) and set(payload)=={'design'}: payload=payload['design']
                    with controller.lock:
                        result=controller.simulator.load_design(payload); controller.last_wall=time.monotonic()
                else: return self._send(404,{'error':'unknown endpoint'})
                return self._send(200,result)
            except (ValueError,TypeError,OSError,KeyError,RecursionError) as error:
                return self._send(400,{'error':str(error)})

    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    server.controller = controller
    return server
