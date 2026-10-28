import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.api_models import TransactionPage
from backend.common import ConflictError, DomainError
from backend.service import Service
from backend.store import Store
from scripts.ai_client import Client
from scripts.source_check import allowed, collect, ROOT


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)))
        self.p=self.s.save_portfolio({'name':'合成组合','planned_amount':'1000000','start_date':'2024-01-01'})
        self.ids=[]
        for n in range(45):
            e=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'510300' if n%2==0 else '159915'},'quantity':'1','gross_amount':str(100+n),'fx_rate':'1'})
            self.ids.append(e['id'])
        self.events=self.s.store.state()['events'];self.iids=sorted({e['instrument_id'] for e in self.events})
        self.s.attach_instrument(self.p['id'],self.events[0]['instrument_id'],{'tags':['收益 & 增长'],'region':'国内','asset_class':'权益'})
        self.s.attach_instrument(self.p['id'],self.events[1]['instrument_id'],{'tags':['科技'],'region':'A股','asset_class':'权益'})
    def tearDown(self):self.tmp.cleanup()
    def test_pages_do_not_duplicate_and_sort_stably(self):
        pages=[self.s.transaction_page(self.p['id'],page=n) for n in range(1,4)]
        self.assertEqual([len(r['items']) for r in pages],[20,20,5]);self.assertEqual(pages[0]['total'],45)
        ids=[r['event']['id'] for p in pages for r in p['items']]
        self.assertEqual(ids,list(reversed(self.ids)));self.assertEqual(len(set(ids)),45)
    def test_tag_filter_is_applied_before_pagination_and_combines_with_other_filters(self):
        opts=dict(tag='收益 & 增长',market='CN',region='A股',asset_class='权益',page_size=7)
        pages=[self.s.transaction_page(self.p['id'],page=n,**opts) for n in range(1,5)]
        self.assertEqual([len(r['items']) for r in pages],[7,7,7,2]);self.assertEqual(pages[0]['total'],23)
        self.assertTrue(all(r['filters']['tag']==opts['tag'] for r in pages))
        self.assertEqual(len({r['event']['id'] for p in pages for r in p['items']}),23)
        self.assertIn(self.ids[0],[r['event']['id'] for p in pages for r in p['items']])
    def test_current_plan_labels_not_historical_event_labels(self):
        iid=self.events[0]['instrument_id'];before=self.s.store.state()['events']
        self.s.attach_instrument(self.p['id'],iid,{'tags':['新标签'],'region':'港股'})
        self.assertEqual(self.s.transaction_page(self.p['id'],tag='收益 & 增长')['total'],0)
        self.assertEqual(self.s.transaction_page(self.p['id'],tag='新标签',region='港股')['total'],23)
        self.assertEqual(before,self.s.store.state()['events'])
    def test_instrument_and_portfolio_isolation(self):
        p2=self.s.save_portfolio({'name':'另一个','planned_amount':'1000','start_date':'2024-01-01'})
        self.assertEqual(self.s.transaction_page(p2['id'],tag='科技')['total'],0)
        with self.assertRaises(DomainError):self.s.transaction_page(p2['id'],self.iids[0])
        one=self.s.transaction_page(self.p['id'],self.events[0]['instrument_id'],page_size=100)
        self.assertEqual(one['total'],23);self.assertTrue(all(r['event']['instrument_id']==self.events[0]['instrument_id'] for r in one['items']))
    def test_last_page_after_mutation_and_empty_results(self):
        self.s.delete_event(self.ids[-1],'合成取消')
        end=self.s.transaction_page(self.p['id'],page=100)
        self.assertEqual((end['page'],end['total'],len(end['items'])),(3,44,4))
        none=self.s.transaction_page(self.p['id'],tag='不存在',page=30)
        self.assertEqual((none['page'],none['page_count'],none['items']),(1,1,[]))
    def test_invalid_queries_rejected(self):
        for kw in ({'page':0},{'page_size':101},{'market':'bad'},{'page_size':0}):
            with self.assertRaises(DomainError):self.s.transaction_page(self.p['id'],**kw)


class ApiCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,testing=True,auto_sync=False)
        self.client=TestClient(self.app);self.client.__enter__()
        boot=self.client.get('/api/bootstrap').json();self.headers={'x-csrf-token':boot['csrf_token']}
        self.p=self.client.post('/api/portfolios',headers=self.headers,json={'name':'API合成','planned_amount':'100000','start_date':'2024-01-01','expected_revision':boot['revision']}).json()
        self.payload={'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'510300'},'quantity':'10','gross_amount':'100','transaction_costs':'2','fx_rate':'1'}
    def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()
    def test_preview_commit_readback_and_stale_retry(self):
        preview=self.client.post('/api/events/preview',json=self.payload,headers=self.headers).json()
        self.assertEqual(self.app.state.store.state()['events'],[])
        payload={**self.payload,'expected_revision':preview['revision']}
        saved=self.client.post('/api/events',json=payload,headers=self.headers);self.assertEqual(saved.status_code,200,saved.text)
        again=self.client.post('/api/events',json=payload,headers=self.headers);self.assertEqual(again.status_code,409)
        page=self.client.get('/api/portfolios/'+self.p['id']+'/transactions?page=1').json()
        TransactionPage.model_validate(page)
        self.assertEqual(page['total'],1);self.assertEqual(page['items'][0]['facts']['base_total'],'102')
        self.assertEqual(len(self.app.state.store.state()['events']),1)
    def test_human_change_invalidates_ai_preview(self):
        preview=self.client.post('/api/events/preview',json=self.payload,headers=self.headers).json()
        self.app.state.service.save_portfolio({**self.p,'name':'人在界面修改'},self.p['id'])
        stale=self.client.post('/api/events',json={**self.payload,'expected_revision':preview['revision']},headers=self.headers)
        self.assertEqual(stale.status_code,409);self.assertFalse(self.app.state.store.state()['events'])
    def test_stale_edit_and_cancel_do_not_write(self):
        saved=self.client.post('/api/events',json=self.payload,headers=self.headers).json();rev=self.app.state.store.state()['revision']
        self.app.state.service.save_portfolio({**self.p,'name':'并发更新'},self.p['id'])
        before=self.app.state.store.state()
        for method,path,payload in [('PUT','events/'+saved['id'],{**saved,'reason':'过期','change_reason':'测试'}),('DELETE','events/'+saved['id'],{'reason':'过期取消'})]:
            response=self.client.request(method,'/api/'+path,headers=self.headers,json={**payload,'expected_revision':rev})
            self.assertEqual(response.status_code,409,response.text)
        after=self.app.state.store.state();self.assertEqual(before['events'],after['events']);self.assertEqual(before['reviews'],after['reviews'])
    def test_openapi_capabilities_and_security(self):
        doc=self.client.get('/api/openapi.json');self.assertEqual(doc.status_code,200)
        self.assertIn('expected_revision',doc.json()['components']['schemas']['EventRequest']['properties'])
        self.assertEqual(self.client.get('/api/capabilities').json()['pagination']['max_page_size'],100)
        self.assertIn('expected_revision',self.client.get('/api/guide').text)
        self.assertEqual(self.client.post('/api/events',json=self.payload).status_code,403)
        self.assertEqual(self.client.post('/api/events',json=self.payload,headers={**self.headers,'origin':'https://example.invalid'}).status_code,403)
        self.assertEqual(self.client.get('/api/bootstrap',headers={'host':'example.invalid'}).status_code,400)
        self.assertEqual(self.client.get('/api/portfolios/'+self.p['id']+'/transactions?page_size=101&page=1').status_code,422)

    def test_legacy_ui_plan_tag_text_and_readable_validation_errors(self):
        event=self.client.post('/api/events',json=self.payload,headers=self.headers).json()
        response=self.client.put('/api/portfolios/'+self.p['id']+'/instruments/'+event['instrument_id'],headers=self.headers,json={'tags':'红利,成长','region':'A股'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['tags'],['红利','成长'])
        invalid=self.client.post('/api/events',headers=self.headers,json={'kind':'BUY'})
        self.assertEqual(invalid.status_code,422);self.assertIsInstance(invalid.json()['error'],str)
    def test_source_export_and_client_restrictions(self):
        paths=collect();self.assertTrue(paths);self.assertTrue(all(allowed(p) for p in paths))
        for name in ('data/portfolio.sqlite','work/screen.png','backup.sqlite','data/backups/private.zip','.env','frontend/dist/index.html','notes/private.md','data/notes/p/i/n/body.md'):
            self.assertFalse(allowed(ROOT/name),name)
        for url in ('https://example.com','http://127.0.0.1.evil.test','http://user:pass@localhost','http://localhost/api'):
            with self.assertRaises(ValueError):Client(url)
        with self.assertRaises(ValueError):Client().write('/api/restore',{'expected_revision':1},'POST')
        with self.assertRaises(ValueError):Client().write('/api/events',{},'POST')

    def test_restore_cannot_reuse_a_previously_previewed_revision(self):
        service=self.app.state.service
        archive=service.backup_download()
        preview=service.preview_event(self.payload)
        service.save_event(self.payload)
        before=self.app.state.store.state()['revision']
        service.restore(archive)
        self.assertGreater(self.app.state.store.state()['revision'],before)
        with self.assertRaises(ConflictError):service.save_event({**self.payload,'expected_revision':preview['revision']})
        self.assertFalse(self.app.state.store.state()['events'])


if __name__=='__main__': unittest.main()
