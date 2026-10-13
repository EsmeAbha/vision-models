import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pexl_integration as p


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.xlsx'
        wb = Workbook(); wb.active.append(['Name']); wb.active.append(['Alice']); wb.save(self.source); wb.close()
        self.app = FastAPI()
        self.store = p.mount(self.app, lambda rid: FileResponse(self.source), lambda rid: FileResponse(self.source), self.root / 'results')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        env = patch.dict(os.environ, {'PEXL_API_URL':'http://pexl.test', 'PEXL_API_KEY':'test-key'})
        env.start(); self.addCleanup(env.stop)

    def test_multiple_outputs_and_persistence(self):
        tid = 'a'*32; oid = 'b'*32
        self.store.put('template', {'id':tid})
        def request(client, method, path, **kwargs):
            if path == '/api/v1/files/source': return httpx.Response(201,json={'id':'c'*32})
            if path == '/api/v1/jobs':
                self.assertEqual(kwargs['json']['external_reference'], 'run1')
                return httpx.Response(201,json={'id':'d'*32,'outputs':[{'id':oid},{'id':oid}]})
            return httpx.Response(200,content=self.source.read_bytes())
        with patch.object(p.Client, 'request', request):
            response = self.client.post('/api/pexl/runs/run1/jobs',json={'outputs':[
                {'template_id':tid,'source_sheet':'Sheet','template_sheet':'Sheet','mapping':{'Name':'Name'}}]})
            self.assertEqual(response.status_code,202,response.text)
            jid=response.json()['id']
            for _ in range(100):
                job=self.client.get('/api/pexl/jobs/'+jid).json()
                if job['status'] in ('completed','failed'): break
                time.sleep(.01)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(len(job['outputs']),2)
        for out in job['outputs']:
            self.assertEqual(self.client.get(out['download']).content,self.source.read_bytes())
        self.assertEqual(p.Store(self.root/'results').get('job',jid)['pexl_job_id'],'d'*32)

    def test_error_codes_and_timeout_no_post_retry(self):
        for code in (401,404,413,422,503):
            with patch.object(httpx.Client,'request',return_value=httpx.Response(code)) as call:
                with self.assertRaises(p.PexlError): p.Client().request('POST','/api/v1/jobs')
                self.assertEqual(call.call_count,1)
        with patch.object(httpx.Client,'request',side_effect=httpx.ReadTimeout('secret diagnostic')) as call:
            with self.assertRaisesRegex(p.PexlError,'do not automatically resubmit'):
                p.Client().request('POST','/api/v1/jobs')
            self.assertEqual(call.call_count,1)

    def test_get_retry(self):
        with patch.object(httpx.Client,'request',side_effect=[httpx.Response(503),httpx.Response(200,json={})]) as call, patch.object(p.time,'sleep'):
            p.Client().request('GET','/api/v1/jobs/abc')
            self.assertEqual(call.call_count,2)

    def test_review_gate_blocks_outbound_calls(self):
        app=FastAPI()
        def denied(rid): raise HTTPException(409,'Review first')
        p.mount(app,denied,denied,self.root/'gated')
        with TestClient(app) as client, patch.object(p.Client,'request') as call:
            self.assertEqual(client.get('/api/pexl/runs/run1/source').status_code,409)
            call.assert_not_called()

    def test_automatic_template_matching_and_invalid_ai_fallback(self):
        tid = 'a'*32
        self.store.put('template', {'id':tid, 'sheets':[{'name':'Report'}]})
        with patch.object(p.Client, 'request', return_value=httpx.Response(200,json={'headers':['Name','Missing']})):
            result = self.client.post('/api/pexl/runs/run1/prepare',json={'template_id':tid})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['outputs'][0]['mapping'],{'Name':'Name'})
        self.assertEqual(result.json()['unmatched'],['Missing'])
        app=FastAPI()
        store=p.mount(app,lambda rid:FileResponse(self.source),lambda rid:FileResponse(self.source), self.root/'ai',
            suggest=lambda prompt:'{"source_sheet":"Sheet","template_sheet":"Report","mapping":{"Name":"Invented"}}')
        store.put('template', {'id':tid,'sheets':[{'name':'Report'}]})
        with TestClient(app) as client, patch.object(p.Client,'request',return_value=httpx.Response(200,json={'headers':['Name']})):
            result=client.post('/api/pexl/runs/run1/prepare',json={'template_id':tid})
        self.assertEqual(result.json()['outputs'][0]['mapping'],{'Name':'Name'})
        self.assertEqual(result.json()['method'],'Matching column names')

    def test_template_is_remembered_per_document_type(self):
        """Upload once for a type; after that the type alone picks it."""
        tid = 'e'*32
        app = FastAPI()
        p.mount(app, lambda rid: FileResponse(self.source), lambda rid: FileResponse(self.source),
                self.root/'typed',
                doc_types=lambda: [{'id':'utility-bill','name':'Utility Bill'},
                                   {'id':'bank-statement','name':'Bank Statement'}],
                doc_type_of=lambda rid: 'utility-bill' if rid == 'run1' else '')

        def request(client, method, path, **kwargs):
            if path == '/api/v1/files/template':
                return httpx.Response(201, json={'id':tid,'filename':'utility.xlsx','sheets':[{'name':'Report'}]})
            if path == f'/api/v1/files/template/{tid}':
                return httpx.Response(200, json={'headers':['Name']})
            raise AssertionError(path)

        with TestClient(app) as client, patch.object(p.Client, 'request', request):
            setup = client.get('/api/pexl/runs/run1/setup').json()
            self.assertEqual(setup['doc_type'], 'utility-bill')
            self.assertIsNone(setup['doc_types'][0]['template_id'])
            # Nothing saved yet: prepare says so instead of guessing.
            self.assertEqual(client.post('/api/pexl/runs/run1/prepare', json={}).status_code, 422)

            up = client.post('/api/pexl/templates', data={'doc_type':'utility-bill'},
                             files={'file':('utility.xlsx', b'x')})
            self.assertEqual(up.status_code, 200, up.text)
            setup = client.get('/api/pexl/runs/run1/setup').json()
            by_type = {d['id']: d['template_id'] for d in setup['doc_types']}
            self.assertEqual(by_type, {'utility-bill': tid, 'bank-statement': None})

            # No template named: the run's type picks it.
            plan = client.post('/api/pexl/runs/run1/prepare', json={})
            self.assertEqual(plan.status_code, 200, plan.text)
            self.assertEqual(plan.json()['outputs'][0]['template_id'], tid)
            self.assertEqual(plan.json()['outputs'][0]['mapping'], {'Name':'Name'})
            # A run of unknown type can still name the type it is.
            plan = client.post('/api/pexl/runs/other/prepare', json={'doc_type':'utility-bill'})
            self.assertEqual(plan.status_code, 200, plan.text)

            self.assertEqual(client.put('/api/pexl/doc-types/no-such-type/template',
                                        json={'template_id':tid}).status_code, 422)
            self.assertEqual(client.put('/api/pexl/doc-types/bank-statement/template',
                                        json={'template_id':'f'*32}).status_code, 404)

    def test_missing_configuration(self):
        with patch.dict(os.environ,{'PEXL_API_KEY':''}):
            response=self.client.post('/api/pexl/runs/run1/jobs',json={'outputs':[{
                'template_id':'a','source_sheet':'Sheet','template_sheet':'Sheet','mapping':{'a':'b'}}]})
            self.assertEqual(response.status_code,503)


if __name__ == '__main__': unittest.main()
