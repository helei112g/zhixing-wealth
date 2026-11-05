import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.service import Service
from backend.store import Store


class SyncMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
    def tearDown(self):self.tmp.cleanup()
    def wait_for_jobs(self,count):
        until=time.monotonic()+5
        while time.monotonic()<until:
            jobs=self.store.jobs()
            if len(jobs)>=count and all(j['status']!='running' for j in jobs) and not self.s.syncing:return jobs
            time.sleep(.01)
        self.fail('Sync did not finish')
    def test_quotes_backfill_six_calendar_months_and_then_resume_incrementally(self):
        from datetime import date
        p=self.s.save_portfolio({'name':'合成趋势','planned_amount':'1000','start_date':'2025-01-01'})
        event=self.s.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2026-08-31','instrument':{'market':'CN','code':'000001','name':'合成基金'},'quantity':'10','gross_amount':'10','fee':'0','fx_rate':'1'})
        iid=event['instrument_id'];calls=[]
        def fetch(inst,start,end,**kw):
            calls.append((start,kw['adjusted_start']))
            return {'rows':[{'date':d,'price':'1','source':'synthetic','kind':'raw'} for d in ('2026-03-02','2026-11-04')],'source':'synthetic','notes':[]}
        with patch('backend.service.today',return_value=date(2026,11,5)),patch('backend.market.quotes',side_effect=fetch),patch('backend.dividends.fetch_notices',return_value=None):
            self.s.start_sync(iid,scopes={'quotes'});self.wait_for_jobs(1)
            self.assertEqual(calls[0],('2026-02-28','2026-02-28'))
            self.assertEqual(self.s.instrument_detail(p['id'],iid)['price_history_start'],'2026-02-28')
            self.s.start_sync(iid,scopes={'quotes'});self.wait_for_jobs(2)
            self.assertEqual(calls[1],('2026-10-28','2026-02-28'))
            self.s.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2026-01-31','instrument_id':iid,'quantity':'1','gross_amount':'1','fee':'0','fx_rate':'1'})
            self.s.start_sync(iid,scopes={'quotes'});self.wait_for_jobs(3)
            self.assertEqual(calls[2],('2025-07-31','2025-07-31'))

    def test_failed_quote_backfill_does_not_mark_history_as_requested(self):
        p=self.s.save_portfolio({'name':'合成失败回填','planned_amount':'1000','start_date':'2026-01-01'})
        event=self.s.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2026-08-31','instrument':{'market':'CN','code':'000001','name':'合成基金'},'quantity':'10','gross_amount':'10','fee':'0','fx_rate':'1'})
        iid=event['instrument_id']
        with patch('backend.market.quotes',return_value={'rows':[],'source':'synthetic','notes':[]}),patch('backend.dividends.fetch_notices',return_value=None):
            self.s.start_sync(iid,scopes={'quotes'});jobs=self.wait_for_jobs(1)
        self.assertEqual(jobs[0]['status'],'partial')
        inst=next(i for i in self.store.state()['instruments'] if i['id']==iid)
        self.assertNotIn('quote_history_requested_start',inst)

    def test_latest_twenty_by_start_and_stable_creation_order(self):
        for n in range(25):self.store.save_job({'id':str(n),'started_at':'2026-01-01T10:00:00','status':'running','items':[]})
        self.store.save_job({'id':'0','started_at':'2026-01-01T10:00:00','status':'complete','items':[]})
        self.assertEqual([j['id'] for j in self.store.jobs()],[str(n) for n in range(24,4,-1)])
    def test_queued_causes_and_full_scope_survive_coalescing(self):
        self.s.save_portfolio({'name':'合成同步组合','planned_amount':'100','start_date':'2024-01-01'})
        entered=threading.Event();release=threading.Event()
        def rates(currency,*args):
            if not entered.is_set():entered.set();self.assertTrue(release.wait(4))
            return [{'date':'2026-01-01','rate':'1','currency':currency,'source':'synthetic'}]
        with patch('backend.market.exchange_rates',side_effect=rates):
            self.s.start_sync(trigger='startup');self.assertTrue(entered.wait(2))
            self.assertTrue(self.s.start_sync(full=True,trigger='event_create')['queued'])
            self.s.start_sync(trigger='retry');release.set();jobs=self.wait_for_jobs(2)
        first=next(j for j in jobs if not j['queued']);queued=next(j for j in jobs if j['queued'])
        self.assertEqual(first['triggers'],['startup']);self.assertEqual(first['trigger_label'],'启动补更')
        self.assertEqual(queued['triggers'],['event_create','retry']);self.assertTrue(queued['full'])
        self.assertIn('新增流水后同步',queued['trigger_label']);self.assertIn('失败后重试',queued['trigger_label'])
    def test_manual_api_cause_and_invalid_cause(self):
        with TestClient(create_app(Path(self.tmp.name)/'api',testing=True,auto_sync=False)) as c:
            headers={'x-csrf-token':c.get('/api/bootstrap').json()['csrf_token']}
            with patch.object(c.app.state.service,'start_sync',return_value={'job_id':'synthetic','status':'running'}) as sync:
                r=c.post('/api/sync',headers=headers,json={'instrument_id':'synthetic-instrument','full':True,'trigger':'retry'})
                self.assertEqual(r.status_code,200);sync.assert_called_once_with('synthetic-instrument',full=True,trigger='retry')
                self.assertEqual(c.post('/api/sync',headers=headers,json={'trigger':'startup'}).status_code,422)
                self.assertEqual(c.post('/api/sync',headers=headers,json={}).status_code,200)
                sync.assert_called_with(None,full=False,trigger='api_request')
            schema=c.get('/api/openapi.json').json();self.assertIn('SyncRequest',schema['components']['schemas'])
    def test_startup_is_recorded_by_server(self):
        app=create_app(Path(self.tmp.name)/'startup',testing=True,auto_sync=True)
        app.state.service.save_portfolio({'name':'合成启动计划','planned_amount':'100','start_date':'2024-01-01'})
        with patch.object(app.state.research.updates,'start'),patch.object(app.state.service,'start_sync') as sync:
            with TestClient(app):
                jobs=app.state.research.jobs()
                self.assertEqual({j['trigger'] for j in jobs},{'startup'})
                self.assertEqual({scope for j in jobs for scope in j['scopes']},{'quotes','fx'})
                sync.assert_not_called()  # The durable planner, rather than startup, owns execution.


    def test_post_write_planner_respects_disabled_and_manual_scopes(self):
        app=create_app(Path(self.tmp.name)/'post-write',testing=True,auto_sync=True)
        research=app.state.research
        def policy(quotes,fx):
            settings=research.settings()
            for p in settings['policies']:
                p['enabled']=p['scope']=='quotes' and quotes=='daily' or p['scope']=='fx' and fx=='daily'
                p['cadence']=quotes if p['scope']=='quotes' else fx if p['scope']=='fx' else 'manual'
            research.save_settings(dict(policies=settings['policies'],expected_settings_revision=settings['settings_revision']))
        policy('manual','manual')
        with patch.object(research.updates,'start'),patch.object(app.state.service,'start_sync') as legacy:
            with TestClient(app) as c:
                headers={'x-csrf-token':c.get('/api/bootstrap').json()['csrf_token']}
                p=c.post('/api/portfolios',headers=headers,json={'name':'合成写后补齐','planned_amount':'10000','start_date':'2026-01-01'}).json()
                def buy(code):
                    r=c.post('/api/events',headers=headers,json={'portfolio_id':p['id'],'kind':'BUY','date':'2026-01-02','instrument':{'market':'CN','code':code,'name':'合成标的'},'quantity':'10','gross_amount':'100','fx_rate':'1'})
                    self.assertEqual(r.status_code,200);return r.json()
                buy('600001');self.assertEqual(research.jobs(),[])
                policy('daily','manual');event=buy('600002');job=research.jobs()[0]
                self.assertEqual(job['trigger'],'event_create');self.assertEqual(job['resources'],[dict(scope='quotes',instrument_id=event['instrument_id'],full=True)])
                policy('manual','daily');buy('600003');job=research.jobs()[0]
                self.assertEqual(job['scopes'],['fx']);self.assertEqual(job['resources'],[dict(scope='fx',currency=ccy,full=True) for ccy in ('HKD','USD')])
                legacy.assert_not_called()
                # The durable resource keeps history scope and original cause when handed to legacy execution.
                resource=job['resources'][0]
                # Use this app's store, rather than the separate setUp store.
                with patch.object(app.state.service,'start_sync',return_value={'job_id':'forwarded'}) as execute,patch.object(app.state.service.store,'jobs',return_value=[{'id':'forwarded','items':[]} ]):
                    research.updates.current_trigger='event_create';research.updates._legacy(resource)
                    execute.assert_called_once_with(None,trigger='event_create',scopes=['fx'],fx_currencies=['HKD'],full=True)


if __name__=='__main__':unittest.main()
