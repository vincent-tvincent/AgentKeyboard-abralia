# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

TOOL=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOL))
from abralia_simulator.engine import Simulator, PROFILES
from abralia_simulator.server import create_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.web=Path(self.temp.name);(self.web/'index.html').write_text('simulator')
        self.server=create_server(Simulator(),port=0,web_root=self.web)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        self.thread.start();self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def request(self,method,path,payload=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        body=json.dumps(payload) if payload is not None else None
        request_headers={'Content-Type':'application/json'} if payload is not None else {}
        request_headers.update(headers or {})
        connection.request(method,path,body,request_headers)
        response=connection.getresponse();raw=response.read();status=response.status
        result=json.loads(raw) if response.getheader('Content-Type')=='application/json' else raw.decode()
        connection.close();return status,result

    def test_bootstrap_state_and_static_assets_are_local_and_readable(self):
        self.assertEqual(self.server.server_address[0],'127.0.0.1')
        code,data=self.request('GET','/api/bootstrap')
        self.assertEqual(code,200);self.assertEqual(len(data['profiles']),3)
        self.assertIn('mode-navigation',{row['id'] for row in data['scenarios']})
        self.assertEqual(self.request('GET','/'),(200,'simulator'))
        self.assertEqual(self.request('GET','/../../etc/passwd')[0],404)
        self.assertEqual(self.request('GET','/%2e%2e/secret.json')[0],404)

    def test_play_pause_and_step_control_only_virtual_time(self):
        controller=self.server.controller
        controller.last_wall-=10
        self.assertEqual(self.request('GET','/api/state')[1]['time'],0)
        self.request('POST','/api/action',{'type':'play','enabled':True})
        controller.last_wall-=.1
        advanced=self.request('GET','/api/state')[1]['time']
        self.assertGreater(advanced,0)
        self.request('POST','/api/action',{'type':'play','enabled':False})
        stopped=self.request('GET','/api/state')[1]['time'];controller.last_wall-=5
        self.assertEqual(self.request('GET','/api/state')[1]['time'],stopped)
        stepped=self.request('POST','/api/action',{'type':'advance','seconds':1})[1]
        self.assertAlmostEqual(stepped['time'],stopped+1)

    def test_import_reset_replays_design_and_bad_data_preserves_it(self):
        design={'version':1,'name':'Frame','frames':[{'at':0,'colors':{'W':'FF9900'}}]}
        code,state=self.request('POST','/api/design',design)
        self.assertEqual(code,200);self.assertEqual(state['colors']['W'],'FF9900')
        exported=self.request('GET','/api/design')[1]
        self.assertEqual(self.request('POST','/api/design',{'version':1,'plugin':'/tmp/code.py'})[0],400)
        self.assertEqual(self.request('GET','/api/design')[1],exported)
        code,state=self.request('POST','/api/reset',{})
        self.assertEqual(code,200);self.assertEqual(state['design_name'],'Frame')
        self.assertEqual(state['colors']['W'],'FF9900')

    def test_actions_and_profile_paths_are_guarded(self):
        for action in ({'type':'exec','command':'echo nope'},
                       {'type':'input','key':'F1','path':'/etc/passwd'},
                       {'type':'advance','seconds':10000},
                       {'type':'notify','agent_id':'missing'}):
            self.assertEqual(self.request('POST','/api/action',action)[0],400)
        self.assertEqual(self.request('POST','/api/reset',{'profile':'/etc/passwd'})[0],400)
        self.assertEqual(self.request('POST','/api/reset',{'profile':PROFILES[2]['id']})[0],200)
        self.assertEqual(self.request('POST','/api/action',{'type':'input','key':'KNOB_CW'})[0],400)

    def test_cross_origin_and_oversized_requests_are_rejected(self):
        self.assertEqual(self.request('POST','/api/action',{'type':'play','enabled':True},
            {'Origin':'https://external.example'})[0],403)
        self.assertEqual(self.request('GET','/api/state',headers={'Host':'other.example'})[0],403)
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request('POST','/api/action',b'{}',{'Content-Type':'text/plain'})
        response=connection.getresponse();self.assertEqual(response.status,415);response.read();connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        connection.request('POST','/api/design',b'{}',{'Content-Type':'application/json','Content-Length':str(2*1024*1024)})
        response=connection.getresponse();self.assertEqual(response.status,413);response.read();connection.close()

    def download(self,fields,origin=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Content-Type':'application/x-www-form-urlencoded'}
        if origin:headers['Origin']=origin
        connection.request('POST','/api/download',urlencode(fields).encode(),headers)
        response=connection.getresponse()
        result=(response.status,dict(response.getheaders()),response.read())
        connection.close();return result

    def test_download_exports_current_editor_draft_without_applying_or_reading_files(self):
        before=self.server.controller.simulator.snapshot()
        design_before=self.server.controller.simulator.design()
        draft={'version':1,'name':'Unapplied editor draft','frames':[{'at':0,'colors':{'W':'FF3300'}}],
               'note':'A draft may contain unfinished metadata; downloading only validates JSON.'}
        status,headers,body=self.download({'design':json.dumps(draft)})
        self.assertEqual(status,200)
        self.assertEqual(headers['Content-Disposition'],'attachment; filename="keyboard-design.json"')
        self.assertEqual(headers['Content-Type'],'application/json')
        self.assertEqual(json.loads(body),draft)
        self.assertTrue(body.endswith(b'\n'));self.assertIn(b'\n  "version"',body)
        self.assertEqual(self.server.controller.simulator.snapshot(),before)
        self.assertEqual(self.server.controller.simulator.design(),design_before)

    def test_download_rejects_external_origin_bad_fields_and_nonfinite_json(self):
        self.assertEqual(self.download({'design':'{}'},'https://external.example')[0],403)
        self.assertEqual(self.download({'path':'/etc/passwd'})[0],400)
        self.assertEqual(self.download({'design':'{}','extra':'x'})[0],400)
        self.assertEqual(self.download({'design':'{"value":NaN}'})[0],400)
        self.assertEqual(self.download({'design':'not-json'})[0],400)

    def test_download_limits_decoded_json_not_percent_encoding_expansion(self):
        draft=json.dumps({'text':'£'*200000},ensure_ascii=False)
        status,_,body=self.download({'design':draft})
        self.assertEqual(status,200);self.assertEqual(json.loads(body),json.loads(draft))
        self.assertEqual(self.download({'design':json.dumps({'text':'x'*(1024*1024)})})[0],413)


if __name__=='__main__':unittest.main()
