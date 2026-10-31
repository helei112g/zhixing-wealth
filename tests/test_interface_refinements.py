"""Synthetic regressions for explicit retirement, insurance terms and actionable notices."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.common import DomainError, json_dumps
from backend.deployment_guard import capture, verify
from backend.environment import claim_environment
from backend.family_notifications import FamilyNotifications, missing_details
from backend.family_service import FamilyService
from backend.service import Service
from backend.store import Store


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = claim_environment(Path(self.temp.name)/'synthetic', 'test')
        self.store = Store(self.directory)
        self.service = Service(self.store)
        self.family = FamilyService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def policy(self, **extra):
        payload = dict(category='insurance', name='合成保障', currency='CNY', effective_on='2026-01-01',
                       household_amount='100', payment_plan=dict(mode='annual', amount='50', first_due='2026-11-10', periods=3), **extra)
        return self.family.command('item', payload, self.store.state()['revision'])['result']

    def test_lifetime_cancels_renewal_and_keeps_premium_plan_and_value(self):
        policy = self.policy(policy_end='2026-11-20')
        command = dict(category='insurance', name='合成保障', currency='CNY', effective_on='2026-10-31',
                       policy_lifetime=True, policy_end=None)
        with patch('backend.family_service.today', return_value=datetime(2026,10,31).date()):
            self.family.command('item', command, self.store.state()['revision'], policy['id'])
        current = next(r for r in self.family.read('2026-10-31')['items'] if r['id'] == policy['id'])
        self.assertIs(current['policy_lifetime'], True)
        self.assertIsNone(current['policy_end'])
        self.assertEqual(current['value'], '100')
        self.assertEqual(current['payment_plan']['mode'], 'annual')
        with self.store.connection() as db:
            schedules = [json.loads(r[0]) for r in db.execute('SELECT data FROM family_schedules')]
        self.assertTrue(all(r['status'] == 'cancelled' for r in schedules if r['kind'] == 'renewal'))
        old = next(r for r in self.family.read('2026-01-01')['items'] if r['id'] == policy['id'])
        self.assertEqual(old['policy_end'], '2026-11-20')
        self.assertFalse(old.get('policy_lifetime', False))
        # Switching back creates a dated renewal, without inventing a lifetime date.
        self.family.command('item', dict(command, policy_lifetime=False, policy_end='2027-11-20'), self.store.state()['revision'], policy['id'])
        with self.store.connection() as db:
            renewal = next(json.loads(r[0]) for r in db.execute('SELECT data FROM family_schedules') if json.loads(r[0])['kind'] == 'renewal')
        self.assertEqual((renewal['status'], renewal['due_date']), ('pending', '2027-11-20'))

    def test_lifetime_is_distinct_from_unknown_and_rejects_conflicting_dates(self):
        unknown = self.policy()
        self.assertFalse(unknown['versions'][-1]['policy_lifetime'])
        before = self.store.state()['revision']
        for values in (dict(policy_lifetime=True, policy_end='2076-12-31'), dict(policy_lifetime='true')):
            with self.assertRaises(DomainError): self.policy(**values)
        self.assertEqual(self.store.state()['revision'], before)

    def legacy(self):
        p = self.service.save_portfolio(dict(name='合成迁移', planned_amount='1000', start_date='2026-01-01'))
        e = self.service.save_event(dict(portfolio_id=p['id'], kind='BUY', date='2026-01-02', instrument=dict(market='CN',code='600111'), quantity='1',gross_amount='10',fx_rate='1'))
        with self.store.connection() as db:
            for table in ('instruments','allocations','events'):
                for rowid, raw in db.execute(f'SELECT rowid,data FROM {table}').fetchall():
                    value = dict(json.loads(raw), tags=['旧标签'], region='混合')
                    db.execute(f'UPDATE {table} SET data=? WHERE rowid=?', (json_dumps(value), rowid))
            label = dict(portfolio_id=p['id'], instrument_id=e['instrument_id'], scope='A股', direction='行业', detail='合成细分')
            db.execute('INSERT INTO holding_labels VALUES (?,?,?)', (p['id'], e['instrument_id'], json_dumps(label)))
            for table in ('research_heads','research_snapshots','research_indices','research_meta','research_price_chunks','data_update_tasks','data_update_policies','data_update_receipts'):
                db.execute('DROP TABLE IF EXISTS '+table)
            db.execute("UPDATE meta SET value='8' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0007_holding_labels'")
            db.commit()
        return p,e

    def test_schema9_cleanup_preserves_finances_audit_labels_and_restore(self):
        self.legacy()
        before_state = self.store.state()
        before = capture(self.directory)
        old_backup = self.store.backup('synthetic-schema8')
        upgraded = Store(self.directory)
        after_state = upgraded.state()
        self.assertTrue(verify(before, capture(self.directory,before))['preserved'])
        for table in ('instruments','allocations','events'):
            expected = [{k:v for k,v in r.items() if k != 'tags'} for r in before_state[table]]
            for record in expected: record['region'] = '未分类'
            self.assertEqual(after_state[table], expected)
        self.assertEqual(before_state['holding_labels'], after_state['holding_labels'])
        self.assertEqual(before_state['revision'], after_state['revision'])
        target = Store(claim_environment(Path(self.temp.name)/'restored','test'))
        target.restore(old_backup.read_bytes())
        self.assertEqual(target.state()['events'], after_state['events'])
        self.assertEqual(target.state()['holding_labels'], after_state['holding_labels'])
        text = Service(upgraded).csv_export(after_state['portfolios'][0]['id'])
        self.assertNotIn('tags', text.splitlines()[0])
        self.assertEqual(Service(upgraded).csv_preview(text,portfolio_id=after_state['portfolios'][0]['id'])['duplicates'],1)

    def test_cleanup_guard_rejects_financial_changes_and_same_schema_cleanup(self):
        self.legacy(); before = capture(self.directory); upgraded = Store(self.directory)
        with upgraded.connection() as db:
            event_id,raw = db.execute('SELECT id,data FROM events').fetchone()
            event = dict(json.loads(raw),fx_rate='9')
            db.execute('UPDATE events SET data=? WHERE id=?',(json_dumps(event),event_id));db.commit()
        with self.assertRaisesRegex(DomainError,'events'): verify(before,capture(self.directory,before))
        # An already-current ledger gets no migration exception.
        current = capture(self.directory)
        with upgraded.connection() as db:
            event = dict(event,tags=['不应新增'])
            db.execute('UPDATE events SET data=? WHERE id=?',(json_dumps(event),event_id));db.commit()
        with self.assertRaisesRegex(DomainError,'events'): verify(current,capture(self.directory,current))

    def test_cleanup_failure_rolls_back_rows_and_schema(self):
        self.legacy(); before = capture(self.directory)
        from backend.legacy_labels import clean_data
        calls = []
        def fail_after_one(raw):
            calls.append(raw)
            if len(calls) > 1: raise RuntimeError('synthetic migration failure')
            return clean_data(raw)
        with patch('backend.legacy_labels.clean_data', side_effect=fail_after_one):
            with self.assertRaisesRegex(RuntimeError,'synthetic migration failure'): Store(self.directory)
        self.assertEqual(capture(self.directory),before)
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone()[0],'0007_holding_labels')

    def test_soft_deleted_events_and_reservations_are_cleaned_without_other_changes(self):
        p,e = self.legacy()
        with self.store.connection() as db:
            deleted = dict(e, id='synthetic-deleted', tags=['旧标签'], region='混合')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,1)', ('synthetic-deleted',p['id'],e['date'],99,e['fingerprint'],json_dumps(deleted)))
            reservation = dict(id='synthetic-reservation',portfolio_id=p['id'],amount='12.34',tags=['旧标签'],region='混合',reason='保留原备注')
            db.execute('INSERT INTO reservations VALUES (?,?,?)', (reservation['id'],p['id'],json_dumps(reservation)))
            db.commit()
        before = capture(self.directory); upgraded=Store(self.directory)
        self.assertTrue(verify(before,capture(self.directory,before))['preserved'])
        with upgraded.connection() as db:
            result=json.loads(db.execute("SELECT data FROM events WHERE id='synthetic-deleted'").fetchone()[0])
            self.assertEqual(result,{k:('未分类' if k=='region' else v) for k,v in deleted.items() if k!='tags'})
            self.assertEqual(db.execute("SELECT deleted FROM events WHERE id='synthetic-deleted'").fetchone()[0],1)
        restored=upgraded.state()['reservations'][0]
        self.assertEqual(restored,{k:('未分类' if k=='region' else v) for k,v in reservation.items() if k!='tags'})

    def test_backup_rejects_conflicting_lifetime_and_expiry(self):
        self.policy(policy_lifetime=True)
        backup=self.store.backup('synthetic-conflicting-policy')
        with sqlite3.connect(backup) as db:
            identity,raw=db.execute('SELECT id,data FROM family_items').fetchone()
            value=json.loads(raw);value['versions'][0]['policy_end']='2076-12-31'
            db.execute('UPDATE family_items SET data=? WHERE id=?',(json_dumps(value),identity));db.commit()
        db.close()
        with self.assertRaisesRegex(DomainError,'保障期限'): self.store.validate_backup(backup.read_bytes())

    def test_notification_details_identify_item_time_reason_and_recovery(self):
        current = datetime(2026,10,31,tzinfo=ZoneInfo('Asia/Shanghai'))
        requests = [dict(id='request',schedule_id='schedule',at=(current+timedelta(days=1)).isoformat())]
        rows = [dict(id='schedule',item_id='item',name='合成保单',kind='premium',due_date='2026-11-08')]
        response = dict(authorization='authorized',pending_ids=[],request_errors=[dict(id='request',domain='SyntheticError',code=7,message='合成排程失败')])
        issues = missing_details(requests,rows,response,current)
        self.assertEqual(len(issues),1)
        self.assertEqual((issues[0]['item_id'],issues[0]['name'],issues[0]['reason']),('item','合成保单','合成排程失败'))
        self.assertEqual(issues[0]['error_code'],7)
        self.assertEqual(missing_details(requests,rows,dict(response,pending_ids=['request']),current),[])
        denied = missing_details(requests,rows,dict(response,authorization='denied'),current)
        self.assertIn('未授权',denied[0]['reason'])
        unknown = missing_details(requests,rows,dict(response,request_errors=[]),current)
        self.assertIn('未提供具体原因',unknown[0]['reason'])

    def test_notification_retry_clears_resolved_details(self):
        self.policy(policy_end=(datetime.now().date()+timedelta(days=15)).isoformat())
        native = FamilyNotifications(self.store,self.family,'test'); native.prepared=True
        native._save(native.preferences,{'enabled':True})
        with patch.object(native,'_helper',return_value={'authorization':'authorized','pending_ids':[]}):
            failed = native.reconcile(True)
        self.assertGreater(failed['missing_count'],0)
        self.assertTrue(all(r['item_id'] and r['at'] for r in failed['missing_requests']))
        def accepted(command,requests,active): return dict(authorization='authorized',pending_ids=[r['id'] for r in requests])
        with patch.object(native,'_helper',side_effect=accepted): resolved=native.reconcile(True)
        self.assertIsNone(resolved['error']);self.assertEqual(resolved['missing_requests'],[])
