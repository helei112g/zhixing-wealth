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
        with patch.object(app.state.service,'start_sync') as sync:
            with TestClient(app):sync.assert_called_once_with(trigger='startup')


if __name__=='__main__':unittest.main()
