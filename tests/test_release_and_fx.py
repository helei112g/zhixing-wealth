import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError
from backend.environment import LedgerProcessLock, claim_environment
from backend.ledger import Market
from backend.migration import SCHEMA, migrate, schema_version
from backend.schema import BASE_SCHEMA
from backend.service import Service
from backend.store import Store


class FxMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def legacy(self):
        path = self.root / 'legacy' / 'portfolio.sqlite'
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.executescript(BASE_SCHEMA)
        db.executemany('INSERT INTO meta VALUES (?,?)', [('schema','2'),('revision','7'),('app_id','synthetic'),('experience_version','2')])
        rows = [
            {'currency':'USD','date':'2024-01-02','rate':'7','manual':False,'source':'synthetic','providers':[{'large':'x'*500}]},
            {'currency':'USD','date':'2024-01-02','rate':'7.1','manual':False,'source':'synthetic'},
            {'currency':'USD','date':'2024-01-02','rate':'9','manual':False,'source':'synthetic','cancelled':True},
            {'currency':'USD','date':'2024-01-03','rate':'7.2','manual':True,'mode':'daily','source':'manual'},
            {'currency':'USD','date':'2024-01-03','rate':'7.3','manual':True,'mode':'daily','source':'manual'},
            {'currency':'USD','date':'2024-01-04','rate':'8','manual':True,'mode':'manual','end_date':'2024-01-06','source':'manual'},
            {'currency':'HKD','date':'2024-01-02','rate':'.9','manual':False,'source':'synthetic'},
        ]
        for row in rows:
            db.execute('INSERT INTO fx(currency,date,manual,end_date,data) VALUES (?,?,?,?,?)',
                       (row['currency'],row['date'],int(row['manual']),row.get('end_date'),json.dumps(row)))
        db.execute('INSERT INTO reviews VALUES (?,?)', ('historic', json.dumps({'note':'synthetic legacy review'})))
        db.commit(); db.close()
        return path, rows

    def test_dedup_preserves_effective_rates_and_legacy_data(self):
        path, rows = self.legacy()
        before = Market({'prices':[], 'fx':[dict(r,version_id=i+1) for i,r in enumerate(rows)]})
        store = Store(path.parent)
        after = Market(store.state())
        for currency in ('USD','HKD'):
            for date in ('2024-01-01','2024-01-02','2024-01-03','2024-01-04','2024-01-07'):
                a,b=before.fx(currency,date),after.fx(currency,date)
                self.assertEqual(a and a['rate'],b and b['rate'])
        with store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fx').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fx_overrides').fetchone()[0],2)
            self.assertNotIn('providers', ''.join(r[0] for r in db.execute('SELECT data FROM fx')))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],1)
        self.assertEqual(len(list(store.backups.glob('before-schema-upgrade-*.sqlite'))),1)
        self.assertFalse(migrate(path))

    def test_migration_failure_rolls_back_ddl_and_rows(self):
        path,_=self.legacy()
        from backend import migration
        upgrade=migration.command.upgrade
        def fail(*args,**kwargs):
            upgrade(*args,**kwargs)
            raise RuntimeError('synthetic migration interruption')
        with patch.object(migration.command,'upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):migrate(path)
        self.assertEqual(schema_version(path),2)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fx').fetchone()[0],7)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='fx_overrides'").fetchone())

    def test_sync_upsert_revisions_manual_priority_and_trade_lock(self):
        service=Service(Store(self.root/'new'))
        portfolio=service.save_portfolio({'name':'合成汇率','planned_amount':'10000','start_date':'2024-01-01'})
        trade=service.save_event({'portfolio_id':portfolio['id'],'kind':'BUY','date':'2024-01-02',
            'instrument':{'market':'US','code':'VTI'},'quantity':'1','gross_amount':'100','fx_rate':'6.9'})
        row={'currency':'USD','date':'2024-01-02','rate':'7','source':'synthetic','providers':['unused']}
        self.assertEqual(service.cache_fx([row])['inserted'],1)
        self.assertEqual(service.cache_fx([row])['unchanged'],1)
        self.assertEqual(service.cache_fx([{**row,'rate':'7.1'}])['updated'],1)
        for value in ('7.2','7.3','7.3'):
            service.save_fx({'currency':'USD','date':'2024-01-02','rate':value,'mode':'daily'})
        service.cache_fx([{**row,'rate':'7.4'}])
        state=service.store.state()
        self.assertEqual(next(e for e in state['events'] if e['id']==trade['id'])['fx_rate'],'6.9')
        self.assertEqual(Market(state).fx('USD','2024-01-02')['rate'],'7.3')
        with service.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fx').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fx_overrides').fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO fx(currency,date,manual,data) VALUES (?,?,?,?)',('USD','2024-01-02',0,json.dumps(row)))

    def test_old_and_new_backup_roundtrip(self):
        path,_=self.legacy();old=path.read_bytes()
        target=Store(self.root/'restored');target.restore(old,{})
        self.assertEqual(schema_version(target.path),SCHEMA)
        exported=Service(target).backup_download()
        other=Service(Store(self.root/'again'));other.restore(exported)
        self.assertEqual(target.state()['fx'],other.store.state()['fx'])

    def test_current_schema_without_unique_constraint_is_rejected(self):
        store=Store(self.root/'broken')
        with store.connection() as db:
            db.execute('DROP INDEX fx_daily_unique');db.commit()
        with self.assertRaises(DomainError):Store(store.directory)


class EnvironmentTests(unittest.TestCase):
    def test_hard_linked_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
            (a/'portfolio.sqlite').write_bytes(b'synthetic')
            os.link(a/'portfolio.sqlite',b/'portfolio.sqlite')
            with self.assertRaises(DomainError):claim_environment(b,'dev')

    def test_marker_and_shared_data_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'data'
            claim_environment(folder,'production')
            with self.assertRaises(DomainError):claim_environment(folder,'dev')
            with LedgerProcessLock(folder):
                with self.assertRaises(DomainError):
                    with LedgerProcessLock(folder):pass

    def test_cookie_and_write_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            a=create_app(Path(temp)/'a',testing=True,auto_sync=False,environment='dev')
            b=create_app(Path(temp)/'b',testing=True,auto_sync=False,environment='production')
            with TestClient(a) as dev,TestClient(b) as prod:
                da=dev.get('/api/bootstrap').json();pb=prod.get('/api/bootstrap').json()
                self.assertEqual(da['environment'],'dev');self.assertEqual(pb['environment'],'production')
                self.assertNotEqual(set(dev.cookies.keys()),set(prod.cookies.keys()))
                prod.cookies.update(dev.cookies)
                payload={'name':'合成隔离','planned_amount':'100','start_date':'2024-01-01'}
                self.assertEqual(prod.post('/api/portfolios',json=payload,headers={'x-csrf-token':da['csrf_token']}).status_code,403)
                self.assertEqual(prod.post('/api/portfolios',json=payload,headers={'x-csrf-token':pb['csrf_token']}).status_code,200)
                self.assertEqual(dev.get('/api/bootstrap').json()['portfolios'],[])


if __name__ == '__main__': unittest.main()
