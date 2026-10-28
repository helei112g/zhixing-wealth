"""Synthetic household ledger: accounting, history, isolation, notifications, backups."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError, ConflictError, json_dumps
from backend.environment import claim_environment
from backend.family_domain import TABLES
from backend.family_service import FamilyService, validate_family_backup
from backend.family_notifications import FamilyNotifications, notification_plan
from backend.migration import migrate, SCHEMA
from backend.service import Service
from backend.store import Store


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = claim_environment(Path(self.temp.name)/'synthetic', 'test')
        self.store = Store(directory); self.service = Service(self.store); self.family = FamilyService(self.store)
        self.patches = [patch(m+'.today', return_value=date(2026,10,27)) for m in ('backend.family_domain','backend.family_service','backend.service')]
        for p in self.patches: p.start()
    def tearDown(self):
        for p in self.patches: p.stop()
        self.temp.cleanup()
    def write(self, operation, payload, rid=None):
        return self.family.command(operation, payload, self.store.state()['revision'], rid)['result']
    def item(self, category='cash', amount='100000', **kwargs):
        return self.write('item', dict(name='合成'+category,category=category,currency='CNY',economic_id=category,
                         effective_on='2026-01-01',amount=amount,source='合成核对资料',**kwargs))
    def value(self, item, amount, when='2026-02-01', **kwargs):
        return self.write('valuation', dict(item_id=item['id'],amount=amount,effective_on=when,source='合成',**kwargs))
    def portfolio(self):
        return self.service.save_portfolio({'name':'合成只读组合','planned_amount':'100000','start_date':'2026-01-01'})
    def buy(self, p, amount='1000'):
        return self.service.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2026-02-01',
                    'instrument':{'market':'CN','code':'510300','name':'合成ETF'},'quantity':'100','gross_amount':amount,'fx_rate':'1'})
    def link(self,p,date='2026-02-02',**kwargs):
        return self.write('link',dict(portfolio_id=p['id'],effective_on=date,share='1',**kwargs))
    def test_basic_assets_debt_share_and_unpaid_costs(self):
        self.item(); self.item('fixed','1000000',share='0.5')
        self.item('debt','200000',interest='100',fees='20',share='0.5')
        r=self.family.read(); self.assertEqual(D(r['total_assets']),600000);self.assertEqual(D(r['total_debt']),100060)
        self.assertEqual(D(r['net_assets']),499940)
    def test_unknown_cash_value_is_not_zero_or_insured_amount(self):
        self.item();self.item('insurance',None,coverage_amount='3000000')
        r=self.family.read();self.assertIsNone(r['total_assets']);self.assertEqual(D(r['known_assets']),100000)
        self.assertIsNone(r['safety']['coverage_months'])
    def test_cash_coverage_requires_manual_expense_and_complete_plans(self):
        self.item(amount='120000',restricted='20000')
        self.assertIsNone(self.family.read()['safety']['coverage_months'])
        self.write('settings',dict(effective_on='2026-01-01',living_expense='10000',reserve_months='6',debt_plan_complete=True,policy_plan_complete=True))
        s=self.family.read()['safety'];self.assertEqual(D(s['available_cash']),100000);self.assertEqual(D(s['coverage_months']),10);self.assertEqual(D(s['target']),60000)
    def test_debt_and_premiums_are_distinct_plan_inputs(self):
        self.item(amount='120000');d=self.item('debt');i=self.item('insurance','0')
        self.write('schedule',dict(item_id=d['id'],kind='debt',due_date='2026-11-01',principal='900',interest='100'))
        self.write('schedule',dict(item_id=i['id'],kind='premium',due_date='2026-12-01',amount='12000'))
        self.write('settings',dict(effective_on='2026-01-01',living_expense='10000',monthly_income='20000',debt_plan_complete=True,policy_plan_complete=True))
        s=self.family.read()['safety'];self.assertEqual(D(s['monthly_total']),12000);self.assertEqual(D(s['debt_income_ratio']),D('.05'))
    def test_zero_expense_is_explicit_but_no_division_by_zero(self):
        self.item();self.write('settings',dict(effective_on='2026-01-01',living_expense='0',debt_plan_complete=True,policy_plan_complete=True))
        s=self.family.read()['safety'];self.assertEqual(D(s['monthly_total']),0);self.assertIsNone(s['coverage_months'])
    def test_history_does_not_backfill_before_inception(self):
        item=self.item(amount='100');self.value(item,'110')
        r=self.family.history('2025-01-01','2026-02-28')['rows']
        self.assertEqual(len(r),2);self.assertEqual(r[0]['date'],'2026-01-31');self.assertEqual(D(r[1]['changes']['net']['amount']),10)
        self.assertEqual(D(r[1]['changes']['net']['rate']),D('.1'))
    def test_negative_net_baseline_has_amount_but_no_rate(self):
        a=self.item(amount='10');self.item('debt','20');self.value(a,'15')
        r=self.family.history(end='2026-02-28')['rows'];self.assertEqual(D(r[-1]['changes']['net']['amount']),5);self.assertIsNone(r[-1]['changes']['net']['rate'])
    def test_scope_changes_have_no_growth_rate(self):
        a=self.item(amount='100');v=a['versions'][-1]
        self.write('item',dict(name=v['name'],category='cash',currency='CNY',economic_id='cash',share='.5',effective_on='2026-02-01'),a['id'])
        r=self.family.history(end='2026-02-28')['rows'];self.assertTrue(r[-1]['scope_changed']);self.assertIsNone(r[-1]['changes']['cash']['rate'])
    def test_latest_effective_value_and_same_day_correction(self):
        a=self.item(amount='100');self.value(a,'120','2026-03-01');self.value(a,'110','2026-02-01');self.value(a,'125','2026-03-01')
        self.assertEqual(D(self.family.read('2026-02-20')['total_assets']),110)
        self.assertEqual(D(self.family.read('2026-03-01')['total_assets']),125)
    def test_future_values_rejected_and_stale_dates_visible(self):
        a=self.item()
        with self.assertRaises(DomainError):self.value(a,'200','2027-01-01')
        self.assertTrue(self.family.read()['warnings']);self.assertEqual(self.family.read()['oldest_source_date'],'2026-01-01')
    def test_missing_fx_stays_unknown(self):
        self.write('item',dict(name='合成美元',category='cash',currency='USD',economic_id='usd',effective_on='2026-01-01',amount='100',source='合成'))
        self.assertIsNone(self.family.read()['total_assets'])
        with self.store.transaction() as db:self.store.upsert_fx(db,dict(currency='USD',date='2026-01-01',rate='7',source='synthetic'))
        self.assertEqual(D(self.family.read()['total_assets']),700)
    def test_duplicate_economic_asset_and_portfolio_link_rejected(self):
        self.item()
        with self.assertRaises(DomainError):self.item()
        p=self.portfolio();self.link(p)
        with self.assertRaises(DomainError):self.link(p)
    def test_unused_budget_excluded_even_when_large(self):
        p=self.portfolio();self.link(p)
        r=self.family.read();self.assertEqual(D(r['total_assets']),0);self.assertEqual(D(r['links'][0]['excluded_budget']),100000)
    def test_linked_market_updates_readonly_and_cash_event_requires_reconcile(self):
        p=self.portfolio();e=self.buy(p);link=self.link(p);self.item(amount='99000')
        self.assertEqual(D(self.family.read()['total_assets']),100000)
        before=self.store.state()['events'];self.buy(p,'2000')
        r=self.family.read();self.assertEqual(D(r['total_assets']),103000);self.assertTrue(r['reconciliation_todos'])
        self.write('reconcile',dict(link_id=link['id'],effective_on='2026-10-27',confirmation='已核对手工现金'))
        self.assertIsNotNone(self.family.read()['total_assets'])
        self.assertEqual(len(self.store.state()['events']),len(before)+1)
    def test_preview_is_side_effect_free_and_stale_save_rejected(self):
        rev=self.store.state()['revision'];payload=dict(name='合成',category='cash',currency='CNY',economic_id='a',effective_on='2026-01-01',amount='1',source='合成')
        a=self.family.command('item',payload,rev,preview=True)
        self.assertEqual(a['revision'],rev);self.assertEqual(self.family.catalog()['items'],[]);self.assertEqual(self.store.state()['revision'],rev)
        self.item()
        with self.assertRaises(ConflictError):self.family.command('item',payload,rev)
    def test_invalid_write_rolls_back_item_and_revision(self):
        rev=self.store.state()['revision']
        with self.assertRaises(DomainError):self.item(restricted='100001')
        self.assertEqual(self.store.state()['revision'],rev);self.assertFalse(self.family.catalog()['items'])
    def test_actual_payment_requires_evidence_and_does_not_change_balances(self):
        d=self.item('debt','1000');plan=self.write('schedule',dict(item_id=d['id'],kind='debt',due_date='2026-10-27',principal='100',interest='10'))
        before=self.family.read()['total_debt']
        payload={k:plan[k] for k in ('item_id','kind','due_date','principal','interest')};payload['status']='done'
        with self.assertRaises(DomainError):self.write('schedule',payload,plan['id'])
        payload['confirmation']='合成还款回执';self.write('schedule',payload,plan['id'])
        self.assertEqual(self.family.read()['total_debt'],before);self.assertEqual(self.family.reminders()['count'],0)
    def test_policy_end_generates_and_replaces_renewal(self):
        item=self.item('insurance','0',policy_end='2026-11-01');rows=self.family.catalog()['schedules'];self.assertEqual(len(rows),1)
        self.write('item',dict(name='合成insurance',category='insurance',currency='CNY',economic_id='insurance',effective_on='2026-10-27',policy_end='2027-11-01'),item['id'])
        changed=self.family.catalog()['schedules'];self.assertEqual(len(changed),1);self.assertEqual(changed[0]['due_date'],'2027-11-01');self.assertEqual(changed[0]['version'],2)
    def test_closure_preserves_original_after_history_correction(self):
        item=self.item(amount='100');close=self.write('closure',{'effective_on':'2026-01-31'})
        self.value(item,'200','2026-01-20');self.assertEqual(D(self.family.read('2026-01-31')['net_assets']),200)
        self.assertEqual(D(self.family.catalog()['closures'][0]['snapshot']['net_assets']),100)
    def test_full_backup_restore_preserves_family_tables(self):
        self.item();self.item('insurance','0',policy_end='2027-01-01');before=self.family.catalog()
        backup=self.service.backup_download();self.item('fixed');self.service.restore(backup)
        after=self.family.catalog()
        for k in ('items','values','links','schedules','settings','closures'):self.assertEqual(before[k],after[k])
    def test_schema3_migration_keeps_existing_business_records(self):
        p=self.portfolio();before=self.store.state()['portfolios']
        with self.store.connection() as db:
            for table in reversed(TABLES):db.execute('DROP TABLE '+table)
            db.execute("UPDATE meta SET value='3' WHERE key='schema'");db.execute("UPDATE alembic_version SET version_num='0002_fx_daily'");db.commit()
        self.assertTrue(migrate(self.store.path));self.assertFalse(migrate(self.store.path));self.assertEqual(self.store.state()['portfolios'],before)
    def test_notification_plan_is_idempotent_and_timezone_explicit(self):
        row=dict(id='abc',status='pending',kind='debt',due_date='2026-11-10',version=1)
        clock=datetime(2026,10,27,9,tzinfo=ZoneInfo('Asia/Shanghai'))
        a=notification_plan([row],clock);self.assertEqual(a,notification_plan([row],clock));self.assertEqual(len(a),3)
        self.assertTrue(all(r['at'].endswith('+08:00') for r in a));self.assertEqual(a[0]['at'],'2026-11-03T09:00:00+08:00')
        self.assertNotEqual(a[0]['id'],notification_plan([dict(row,version=2)],clock)[0]['id'])
        self.assertEqual(notification_plan([dict(row,status='done')],clock),[])
    def test_snooze_does_not_mark_paid_and_notification_preferences_not_restored(self):
        row=dict(id='abc',status='pending',kind='debt',due_date='2026-11-10',version=1,snooze_until='2026-11-11')
        a=notification_plan([row],datetime(2026,10,27,tzinfo=ZoneInfo('Asia/Shanghai')))
        self.assertEqual(len(a),1);self.assertIn('2026-11-11',a[0]['at'])
        n=FamilyNotifications(self.store,self.family,'test');self.assertFalse(n.status()['enabled'])
        with patch.object(n,'_helper') as helper:n.reconcile();helper.assert_not_called()
    def test_notification_os_acceptance_verified_not_assumed(self):
        n=FamilyNotifications(self.store,self.family,'test');n.prepared=True;n._save(n.preferences,{'enabled':True})
        self.item('insurance','0',policy_end='2026-11-10')
        with patch.object(n,'_helper',return_value={'authorization':'authorized','pending_ids':[]}):r=n.reconcile(True)
        self.assertEqual(r['missing_count'],3);self.assertTrue(r['error']);self.assertIsNone(r['coverage_until'])
    def test_family_api_requires_csrf_revision_and_preserves_nulls(self):
        with TestClient(create_app(self.store.directory,testing=True,auto_sync=False)) as c:
            boot=c.get('/api/bootstrap').json();payload=dict(operation='settings',expected_revision=boot['revision'],payload=dict(effective_on='2026-01-01',living_expense=None,debt_plan_complete=False,policy_plan_complete=False))
            self.assertEqual(c.post('/api/family/commands',json=payload).status_code,403)
            h={'X-CSRF-Token':boot['csrf_token']}
            self.assertEqual(c.post('/api/family/preview',json=payload,headers=h).status_code,200)
            self.assertEqual(c.post('/api/family/commands',json=payload,headers=h).status_code,200)
            self.assertEqual(c.post('/api/family/commands',json=payload,headers=h).status_code,409)
            self.assertIsNone(c.get('/api/family/overview').json()['safety']['coverage_months'])

    def test_real_cash_portfolio_separates_cash_and_positions(self):
        p=self.service.save_portfolio({'name':'合成真实现金','planned_amount':'999999','funding_mode':'cash','start_date':'2026-01-01'})
        self.service.save_event({'portfolio_id':p['id'],'kind':'DEPOSIT','date':'2026-01-01','amount':'10000','currency':'CNY','fx_rate':'1'})
        self.buy(p);self.link(p)
        r=self.family.read();self.assertEqual(D(r['total_assets']),10000)
        self.assertEqual(D(r['categories'][0]['value']),9000);self.assertEqual(D(r['categories'][2]['value']),1000)

    def test_price_and_budget_change_update_without_cash_reconciliation(self):
        p=self.portfolio();event=self.buy(p);self.link(p)
        before=self.store.state()['events'];self.service.cache_prices(event['instrument_id'],[{'date':'2026-10-27','price':'20','source':'合成行情'}])
        r=self.family.read();self.assertEqual(D(r['total_assets']),2000);self.assertEqual(r['reconciliation_todos'],[])
        self.service.save_portfolio({'name':p['name'],'planned_amount':'1100000','start_date':'2026-01-01'},p['id'])
        r=self.family.read();self.assertEqual(D(r['total_assets']),2000);self.assertEqual(r['reconciliation_todos'],[])
        self.assertEqual(self.store.state()['events'],before)

    def test_pending_dividend_is_counted_once_then_requires_cash_check_on_settlement(self):
        p=self.portfolio();event=self.buy(p)
        self.service.save_event({'portfolio_id':p['id'],'instrument_id':event['instrument_id'],'kind':'DIVIDEND',
                               'date':'2026-03-01','pay_date':'2026-03-10','gross_amount':'100','tax':'0','fx_rate':'1'})
        self.link(p,'2026-03-02')
        self.assertEqual(D(self.family.read('2026-03-02')['total_assets']),1100)
        r=self.family.read('2026-03-10');self.assertEqual(D(r['total_assets']),1000);self.assertTrue(r['reconciliation_todos']);self.assertEqual(D(r['provisional_assets']),1000)

    def test_bad_family_backup_rejected_before_replace(self):
        self.item();original=self.store.state()['revision']
        with self.store.connection() as db:
            db.execute('INSERT INTO family_settings VALUES (?,?)',('broken',json.dumps({'id':'broken','effective_on':'2026-01-01'})));db.commit()
        damaged=self.service.backup_download()
        with self.store.connection() as db:db.execute("DELETE FROM family_settings WHERE id='broken'");db.commit()
        with self.assertRaises(DomainError):self.service.restore(damaged)
        self.assertEqual(self.store.state()['revision'],original);self.assertEqual(len(self.family.catalog()['items']),1)

    def test_policy_termination_removes_future_reminder_without_deleting_history(self):
        i=self.item('insurance','0',policy_end='2027-01-01')
        self.write('item',dict(name='合成保险',category='insurance',currency='CNY',economic_id='insurance',effective_on='2026-10-01',closed_on='2026-10-20',policy_end='2027-01-01'),i['id'])
        self.assertEqual(self.family.read()['schedules'],[])
        self.assertEqual(len(self.family.catalog()['items'][0]['versions']),2)

    def test_filtered_first_period_still_compares_previous_month(self):
        a=self.item(amount='100');self.value(a,'110')
        r=self.family.history(start='2026-02-01',end='2026-02-28')['rows'][0]
        self.assertEqual(r['comparison_date'],'2026-01-31');self.assertEqual(D(r['changes']['net']['rate']),D('.1'))

    def test_partial_year_yoy_uses_same_day_not_full_prior_year(self):
        a=self.write('item',dict(name='合成跨年现金',category='cash',currency='CNY',economic_id='years',effective_on='2025-01-01',amount='100',source='合成'))
        self.value(a,'200','2025-12-31');self.value(a,'300','2026-01-01')
        row=self.family.history('2026-01-01','2026-10-27','year','yoy')['rows'][0]
        self.assertEqual(row['comparison_date'],'2025-10-27');self.assertEqual(D(row['changes']['net']['rate']),2)
        row=self.family.history('2026-01-01','2026-10-27','year','previous')['rows'][0]
        self.assertEqual(row['comparison_date'],'2025-12-31');self.assertEqual(D(row['changes']['net']['rate']),D('.5'))

    def test_every_notification_mutation_queues_even_during_old_worker_exit(self):
        n=FamilyNotifications(self.store,self.family,'test');n._save(n.preferences,{'enabled':True})
        with patch('backend.family_notifications.threading.Thread') as thread:
            n.invalidate();n.invalidate()
            self.assertEqual(thread.call_count,2)
            self.assertEqual(thread.return_value.start.call_count,2)
            self.assertFalse(thread.call_args.kwargs['daemon'])

    def test_multiple_links_preview_and_save_are_atomic(self):
        a=self.portfolio();b=self.service.save_portfolio({'name':'合成第二组合','planned_amount':'200','start_date':'2026-01-01'})
        rev=self.store.state()['revision'];body={'portfolio_ids':[a['id'],b['id']],'effective_on':'2026-02-02'}
        preview=self.family.command('links',body,rev,preview=True)
        self.assertEqual(len(preview['result']),2);self.assertEqual(self.family.catalog()['links'],[])
        self.assertEqual(self.store.state()['revision'],rev)
        with self.assertRaises(DomainError):self.write('links',dict(body,portfolio_ids=[a['id'],'missing']))
        self.assertEqual(self.family.catalog()['links'],[]);self.assertEqual(self.store.state()['revision'],rev)
        self.write('links',body);self.assertEqual(len(self.family.read()['links']),2)
        self.assertEqual(self.store.state()['revision'],rev+1)
        with self.assertRaises(ConflictError):self.family.command('unlink',{'link_ids':[self.family.read()['links'][0]['id']]},rev)

    def test_unlink_preserves_source_and_prior_history_and_allows_same_day_relink(self):
        p=self.portfolio();self.buy(p);l=self.link(p);before=self.store.state()
        self.write('unlink',{'link_ids':[l['id']],'effective_on':'2026-03-01'})
        self.assertEqual(D(self.family.read('2026-02-28')['total_assets']),1000)
        self.assertEqual(D(self.family.read('2026-03-01')['total_assets']),0)
        self.assertEqual(self.store.state()['events'],before['events']);self.assertEqual(self.store.state()['portfolios'],before['portfolios'])
        new=self.write('links',{'portfolio_ids':[p['id']],'effective_on':'2026-10-27'})[0]
        self.write('unlink',{'link_ids':[new['id']],'effective_on':'2026-10-27'})
        again=self.write('links',{'portfolio_ids':[p['id']],'effective_on':'2026-10-27'})
        self.assertEqual(len(again),1);self.assertEqual(len(self.family.read()['links']),1)
        backup=self.service.backup_download();self.service.restore(backup)
        self.assertEqual(len(self.family.read()['links']),1)

    def test_batch_unlink_failure_does_not_partially_disconnect(self):
        p=self.portfolio();l=self.link(p);before=self.family.catalog()
        with self.assertRaises(DomainError):self.write('unlink',{'link_ids':[l['id'],'missing']})
        self.assertEqual(self.family.catalog(),before)

    def test_simple_debt_dates_do_not_backfill_current_balance_or_double_count_payments(self):
        d=self.write('item',{'category':'debt','name':'合成简明借款','currency':'CNY','effective_on':'2026-10-27',
                             'household_amount':'9000','started_on':'2021-01-01','due_on':'2026-11-01'})
        self.assertEqual(self.family.read('2026-10-26')['items'],[])
        self.assertEqual(D(self.family.read()['total_debt']),9000)
        self.write('settings',dict(effective_on='2026-10-27',living_expense='100',debt_plan_complete=True,policy_plan_complete=True))
        self.assertEqual(D(self.family.read()['safety']['debt_month']),0)
        pay=self.write('schedule',dict(item_id=d['id'],kind='debt',due_date='2026-11-01',amount='1000'))
        self.assertFalse(pay['breakdown_known']);self.assertIsNone(pay['principal']);self.assertIsNone(pay['interest'])
        self.assertEqual(D(self.family.read()['safety']['debt_month']),1000)
        self.assertFalse(any(r['kind']=='debt_end' for r in self.family.read()['schedules']))
        self.write('schedule',dict(item_id=d['id'],kind='debt',due_date=pay['due_date'],amount='1000',status='done',confirmation='合成已还清'),pay['id'])
        self.assertEqual(D(self.family.read()['total_debt']),9000)


    def test_simplified_schedule_uses_household_amount_for_legacy_share(self):
        d=self.item('debt','2000',share='.5')
        pay=self.write('schedule',dict(item_id=d['id'],kind='debt',due_date='2026-11-01',household_amount='200'))
        self.assertEqual(pay['amount'],'400');self.assertFalse(pay['breakdown_known'])
        self.assertEqual(self.family.read()['schedules'][0]['household_amount'],'200')

    def test_receivable_is_asset_not_cash_and_supports_history_and_overdue_reminders(self):
        self.item(amount='1000')
        r=self.write('item',dict(category='receivable',name='合成借出款',currency='USD',effective_on='2026-01-01',
                                household_amount='100',started_on='2025-12-01',due_on='2026-10-26'))
        self.assertIsNone(self.family.read()['total_assets'])
        with self.store.transaction() as db:self.store.upsert_fx(db,dict(currency='USD',date='2026-01-01',rate='7',source='synthetic'))
        self.assertEqual(D(self.family.read()['total_assets']),1700)
        self.assertEqual(D(self.family.read()['safety']['available_cash']),1000)
        self.write('valuation',dict(item_id=r['id'],effective_on='2026-02-01',household_amount='80'))
        rows=self.family.history(end='2026-02-28')['rows']
        self.assertEqual(D(rows[-1]['values']['receivable']),560)
        self.assertEqual(D(rows[-1]['changes']['receivable']['amount']),-140)
        self.assertEqual(self.family.reminders()['count'],0)
        with self.store.connection() as db:validate_family_backup(db)
        backup=self.service.backup_download();self.service.restore(backup)
        self.assertEqual(D(self.family.read()['total_assets']),1560)

    def test_recording_already_overdue_receivable_does_not_require_fake_historical_balance(self):
        self.write('item',dict(category='receivable',name='合成逾期待收',currency='CNY',effective_on='2026-10-27',
                              household_amount='50',started_on='2026-01-01',due_on='2026-10-01'))
        self.assertEqual(self.family.reminders()['items'],[])
        self.assertEqual(D(self.family.read()['total_assets']),50)
        self.assertEqual(self.family.read('2026-10-01')['items'],[])

    def test_simplified_balance_preserves_legacy_share_and_history(self):
        d=self.item('debt','2000',share='.5',interest='100',fees='20')
        self.assertEqual(D(self.family.read()['items'][0]['household_amount']),1060)
        self.write('valuation',dict(item_id=d['id'],effective_on='2026-02-01',household_amount='900'))
        self.assertEqual(D(self.family.read()['total_debt']),900)
        self.assertEqual(D(self.family.read('2026-01-01')['total_debt']),1060)
        self.assertEqual(self.family.read()['items'][0]['share'],'0.5')
        c=self.item('cash','1000',share='.5',restricted='100')
        self.write('valuation',dict(item_id=c['id'],effective_on='2026-02-01',household_amount='400',household_restricted='40'))
        self.assertEqual(D(self.family.read()['safety']['available_cash']),360)

    def test_simple_metadata_edit_retains_hidden_legacy_data_and_existing_values(self):
        a=self.item('fixed','1000',share='.5',platform='合成机构')
        self.write('item',dict(category='fixed',name='合成新名称',currency='CNY',effective_on='2026-02-01'),a['id'])
        row=self.family.read()['items'][0]
        self.assertEqual(row['platform'],'合成机构');self.assertEqual(row['share'],'0.5')
        self.assertEqual(D(row['value']),500);self.assertEqual(len(self.family.catalog()['values']),1)
        self.assertEqual(row['versions'][0]['name'],'合成fixed')

    def test_due_dates_validate_and_reschedule_without_changing_balance(self):
        payload=dict(category='receivable',name='合成应收',currency='CNY',effective_on='2026-10-27',household_amount='50',started_on='2026-01-01')
        with self.assertRaises(DomainError):self.write('item',dict(payload,due_on='2025-12-31'))
        with self.assertRaises(DomainError):self.write('item',dict(payload,started_on='2027-01-01'))
        item=self.write('item',dict(payload,due_on='2027-01-01'))
        self.assertEqual(self.family.read()['schedules'],[])
        self.assertEqual(D(self.family.read()['total_assets']),50)

    def test_schema4_upgrade_adds_unknown_dates_without_touching_balance_or_original_fields(self):
        d=self.item('debt','1234');before=self.store.state();values=self.family.catalog()['values']
        path=self.store.path
        with self.store.connection() as db:
            raw=json.loads(db.execute('SELECT data FROM family_items WHERE id=?',(d['id'],)).fetchone()[0])
            for v in raw['versions']:v.pop('started_on',None);v.pop('due_on',None)
            db.execute('UPDATE family_items SET data=? WHERE id=?',(json.dumps(raw),d['id']))
            db.execute('DROP TABLE family_income')
            db.execute("UPDATE meta SET value='4' WHERE key='schema'");db.execute("UPDATE alembic_version SET version_num='0003_family'");db.commit()
        self.assertTrue(migrate(path));self.assertFalse(migrate(path))
        self.assertEqual(self.family.catalog()['values'],values);self.assertEqual(self.store.state()['events'],before['events'])
        row=self.family.read()['items'][0];self.assertIsNone(row['started_on']);self.assertIsNone(row['due_on'])
        self.assertEqual(D(row['value']),1234)

    def test_required_simple_amount_and_atomic_edit(self):
        with self.assertRaises(DomainError):self.write('item',dict(category='cash',name='合成空金额',currency='CNY',household_amount='',effective_on='2026-10-27'))
        self.assertEqual(self.family.catalog()['items'],[])
        item=self.item('cash','100')
        rev=self.store.state()['revision']
        preview=self.family.command('item',dict(category='cash',name='合成修改',currency='CNY',household_amount='200',effective_on='2026-10-27'),rev,item['id'],True)
        self.assertEqual(D(preview['summary']['provisional_assets']),200)
        self.assertEqual(D(self.family.read()['total_assets']),100)
        self.write('item',dict(category='cash',name='合成修改',currency='CNY',household_amount='200',effective_on='2026-10-27'),item['id'])
        self.assertEqual(D(self.family.read()['total_assets']),200)
        self.assertEqual(D(self.family.read('2026-10-26')['total_assets']),100)

    def test_amortization_zero_rate_rounding_and_month_end(self):
        from backend.family_plans import payment_plan, installments
        for mode in ('annuity','equal_principal'):
            p=payment_plan(dict(mode=mode,first_due='2024-01-31',periods=3,annual_rate='0'),'debt','100')
            rows=installments(p,'a','合成贷款','CNY','2024-01-01','2024-12-31')
            self.assertEqual([r['due_date'] for r in rows],['2024-01-31','2024-02-29','2024-03-31'])
            self.assertEqual([D(r['amount']) for r in rows],[D('33.33'),D('33.33'),D('33.34')])
        p=payment_plan(dict(mode='annuity',first_due='2026-01-01',periods=12,annual_rate='12'),'debt','100000')
        rows=installments(p,'a','合成贷款','CNY','2026-01-01','2026-12-31')
        self.assertEqual(D(rows[0]['amount']),D('8884.88'));self.assertLess(abs(D(rows[-1]['amount'])-D('8884.88')),D('.1'))
        p=payment_plan(dict(mode='equal_principal',first_due='2026-01-01',periods=12,annual_rate='12'),'debt','120000')
        rows=installments(p,'a','合成贷款','CNY','2026-01-01','2026-12-31')
        self.assertEqual(D(rows[0]['amount']),11200);self.assertEqual(D(rows[-1]['amount']),10100)
        self.assertEqual(sum(D(r['amount']) for r in rows),127800)

    def test_policy_annual_year_count_and_leap_day(self):
        from backend.family_plans import payment_plan, installments
        p=payment_plan(dict(mode='annual',first_due='2024-02-29',periods=5,amount='6000'),'insurance')
        rows=installments(p,'a','合成保单','CNY','2024-01-01','2030-01-01')
        self.assertEqual(len(rows),5);self.assertEqual(rows[1]['due_date'],'2025-02-28');self.assertEqual(rows[-1]['due_date'],'2028-02-29')
        self.assertTrue(all(r['kind']=='premium' for r in rows))
        p=payment_plan(dict(mode='once',first_due='2026-11-01',amount='5000'),'insurance')
        self.assertEqual(len(installments(p,'a','合成保单','CNY','2026-01-01','2027-01-01')),1)

    def test_payment_rules_validate_and_preview_without_mutation(self):
        payload=dict(category='debt',name='合成自动还款',currency='CNY',effective_on='2026-10-27',household_amount='10000')
        for rule in [dict(mode='annual'),dict(mode='fixed',first_due='2026-11-01',periods=0,amount='100'),dict(mode='annuity',first_due='2026-11-01',periods=12,annual_rate='101')]:
            with self.assertRaises(DomainError):self.write('item',dict(payload,payment_plan=rule))
        self.assertEqual(self.family.catalog()['items'],[])
        preview=self.family.command('item',dict(payload,payment_plan=dict(mode='annuity',first_due='2026-11-01',periods=12,annual_rate='0')),self.store.state()['revision'],preview=True)
        self.assertEqual(preview['result']['payment_preview']['periods'],12)
        self.assertEqual(D(preview['result']['payment_preview']['total']),10000)
        self.assertEqual(self.family.catalog()['items'],[])

    def test_payment_reminders_window_expiry_replacement_and_no_balance_changes(self):
        d=self.item('debt','12000',payment_plan=dict(mode='fixed',first_due='2026-10-27',periods=12,amount='1000'))
        rows=self.family.reminders()['items'];self.assertEqual([r['due_date'] for r in rows],['2026-10-27','2026-11-27'])
        ids={r['id'] for r in rows}
        self.write('item',dict(category='debt',name='合成debt',currency='CNY',effective_on='2026-10-27',payment_plan=dict(mode='fixed',first_due='2026-11-01',periods=12,amount='1500')),d['id'])
        rows=self.family.reminders()['items'];self.assertFalse(ids & {r['id'] for r in rows})
        self.assertTrue(all(0<=r['days']<=35 for r in rows));self.assertEqual(D(self.family.read()['total_debt']),12000)
        self.assertEqual(self.family.read('2026-10-26')['schedules'][0]['amount'],'1000')
        with patch('backend.family_service.today',return_value=date(2027,12,1)),patch('backend.family_domain.today',return_value=date(2027,12,1)):
            self.assertEqual(self.family.reminders()['items'],[])
            self.assertEqual(D(self.family.read()['total_debt']),12000)
        self.value(d,'0','2026-10-27');self.assertEqual(self.family.reminders()['items'],[])

    def test_rule_suppresses_legacy_manual_reminders_and_receivable_is_not_scheduled(self):
        d=self.item('debt','12000');self.write('schedule',dict(item_id=d['id'],kind='debt',due_date='2026-11-01',amount='5'))
        self.write('item',dict(category='debt',name='合成debt',currency='CNY',effective_on='2026-10-27',payment_plan=dict(mode='fixed',first_due='2026-11-01',periods=12,amount='1000')),d['id'])
        self.item('receivable','500',due_on='2026-11-01')
        rows=self.family.reminders()['items'];self.assertEqual(len(rows),2);self.assertTrue(all(r['kind']=='debt' for r in rows))
        self.assertEqual(len(self.family.catalog()['schedules']),1)

    def test_income_monthly_categories_and_correction_do_not_change_assets(self):
        self.item(amount='500');before=self.family.read()['net_assets']
        for kind,amount in [('salary','10000'),('rent','3000'),('fund','2000'),('custom','500')]:
            self.write('income',dict(kind=kind,name='合成自定义',month='2026-09',currency='CNY',amount=amount))
        r=self.family.income_summary(2026);self.assertEqual(D(r['rows'][8]['total']),15500);self.assertFalse(r['rows'][9]['recorded'])
        record=r['records'][0]
        self.write('income',dict(kind=record['kind'],name=record['name'],month='2026-08',currency='CNY',amount='100'),record['id'])
        self.assertEqual(len(self.family.catalog()['income']),4)
        self.assertEqual(len(next(i for i in self.family.catalog()['income'] if i['id']==record['id'])['versions']),2)
        self.assertEqual(self.family.read()['net_assets'],before)
        backup=self.service.backup_download();self.service.restore(backup);self.assertEqual(len(self.family.income_summary(2026)['records']),4)

    def test_income_missing_fx_is_unknown_not_zero_and_no_future_entries(self):
        self.write('income',dict(kind='salary',month='2026-10',currency='USD',amount='100'))
        self.assertIsNone(self.family.income_summary(2026)['rows'][9]['total'])
        with self.assertRaises(DomainError):self.write('income',dict(kind='salary',month='2027-01',currency='CNY',amount='1'))
        with self.assertRaises(DomainError):self.write('income',dict(kind='custom',month='2026-10',currency='CNY',amount='1',name=''))
        with self.assertRaises(DomainError):self.write('income',dict(kind='salary',month='2026-10',currency='CNY',amount=''))

    def test_global_notification_endpoint_uses_same_explicit_preferences(self):
        with TestClient(create_app(self.store.directory,testing=True,auto_sync=False)) as client:
            boot=client.get('/api/bootstrap').json()
            self.assertEqual(client.get('/api/notifications').json()['enabled'],False)
            self.assertEqual(client.post('/api/notifications',json={'action':'disable'}).status_code,403)
            response=client.post('/api/notifications',json={'action':'disable'},headers={'X-CSRF-Token':boot['csrf_token']})
            self.assertEqual(response.status_code,200);self.assertFalse(response.json()['enabled'])

    def test_dividend_native_queue_is_deduplicated_and_removed_when_resolved(self):
        n=FamilyNotifications(self.store,self.family,'test');n.prepared=True;n._save(n.preferences,{'enabled':True})
        current=datetime(2026,10,27,10,tzinfo=ZoneInfo('Asia/Shanghai'));requests=[]
        row=dict(id='a'*64,portfolio_id='b'*32,kind='dividend',version=1,status='pending')
        def helper(command, desired, active):
            requests.append((desired,active));return dict(authorization='authorized',pending_ids=[r['id'] for r in desired])
        with patch.object(n,'dividend_rows',return_value=[row]) as candidates,patch.object(n,'_helper',side_effect=helper),patch('backend.family_notifications.datetime') as clock:
            clock.now.return_value=current;clock.fromisoformat.side_effect=datetime.fromisoformat
            n.reconcile(True);n.reconcile(True)
            self.assertEqual(requests[0][0][0]['id'],requests[1][0][0]['id']);self.assertEqual(requests[0][0][0]['route'],'reminders')
            self.assertEqual(requests[0][0][0]['portfolio_id'],row['portfolio_id'])
            clock.now.return_value=current.replace(second=10);n.reconcile(True)
            self.assertEqual(requests[-1][0],[]);self.assertEqual(requests[-1][1],[row['id']+':1'])
            candidates.return_value=[];n.reconcile(True);self.assertEqual(requests[-1][1],[])

    def test_dividend_notifications_use_actual_eligible_unconfirmed_notices(self):
        p=self.portfolio();buy=self.buy(p)
        source=dict(code='510300',source_ref='synthetic-dividend-1',ex_date='2026-10-20',record_date='2026-10-19',pay_date='2026-10-21',per_share='0.1',currency='CNY',source='合成公告')
        self.service.cache_dividends(buy['instrument_id'],[source])
        n=FamilyNotifications(self.store,self.family,'test')
        rows=n.dividend_rows(self.store.state());self.assertEqual(len(rows),1);self.assertEqual(rows[0]['portfolio_id'],p['id'])
        with patch('backend.service.now',return_value='2026-10-27T23:59:00+08:00'):self.service.cache_dividends(buy['instrument_id'],[source])
        self.assertEqual(rows,n.dividend_rows(self.store.state()))
        notice=self.service.dividend_candidates(p['id'])[0]['notice']
        self.service.ignore_dividend(notice['id'],{'portfolio_id':p['id'],'ignored':True})
        self.assertEqual(n.dividend_rows(self.store.state()),[])
        self.service.ignore_dividend(notice['id'],{'portfolio_id':p['id'],'ignored':False})
        self.service.cache_dividends(buy['instrument_id'],[dict(source,per_share='0.2')])
        self.assertNotEqual(rows[0]['id'],n.dividend_rows(self.store.state())[0]['id'])

    def test_dividend_denied_submission_is_retried_after_permission_recovers(self):
        n=FamilyNotifications(self.store,self.family,'test');n.prepared=True;n._save(n.preferences,{'enabled':True})
        current=datetime(2026,10,27,10,tzinfo=ZoneInfo('Asia/Shanghai'))
        row=dict(id='a'*64,portfolio_id='b'*32,kind='dividend',version=1,status='pending')
        with patch.object(n,'dividend_rows',return_value=[row]),patch.object(n,'_helper') as helper,patch('backend.family_notifications.datetime') as clock:
            clock.now.return_value=current;clock.fromisoformat.side_effect=datetime.fromisoformat
            helper.return_value=dict(authorization='denied',pending_ids=[])
            n.reconcile(True);self.assertEqual(n.status()['dividend_dispatches'],{})
            clock.now.return_value=current.replace(minute=1)
            helper.return_value=dict(authorization='authorized',pending_ids=[row['id']])
            n.reconcile(True)
            desired=helper.call_args.args[1]
            self.assertEqual(len(desired),1);self.assertGreater(datetime.fromisoformat(desired[0]['at']),clock.now.return_value)
            self.assertIn(row['id'],n.status()['dividend_dispatches'])

    def test_payment_and_income_backup_validation_rejects_broken_rules(self):
        item=self.item('debt','1000')
        base=dict(category='debt',name='合成借款',currency='CNY',effective_on='2026-10-27',household_amount='1000',started_on='2026-01-01')
        for plan in (None,[],dict(mode='fixed',first_due='2025-12-01',periods=3,amount='100')):
            with self.assertRaises(DomainError):self.write('item',dict(base,payment_plan=plan),item['id'])
        self.write('item',dict(base,payment_plan=dict(mode='annuity',first_due='2026-10-31',periods=3,annual_rate='0')),item['id'])
        self.assertEqual(self.family.read()['items'][0]['payment_end'],'2026-12-31')
        with self.store.connection() as db:
            validate_family_backup(db)
            raw=json.loads(db.execute('SELECT data FROM family_items WHERE id=?',(item['id'],)).fetchone()[0])
            raw['versions'][-1]['payment_plan']['periods']=0
            db.execute('UPDATE family_items SET data=? WHERE id=?',(json.dumps(raw),item['id']))
            with self.assertRaises(DomainError):validate_family_backup(db)
            db.rollback()
            db.execute('INSERT INTO family_income (id,data) VALUES (?,?)',('invalid',json.dumps(dict(id='invalid',versions=[]))))
            with self.assertRaises(DomainError):validate_family_backup(db)
            db.rollback()

    def test_debt_plan_recalculation_does_not_backdate_current_balance(self):
        base=dict(category='debt',name='合成计划调整',currency='CNY',effective_on='2026-09-01',household_amount='1200',payment_plan=dict(mode='annuity',first_due='2026-10-01',periods=12,annual_rate='0'))
        item=self.write('item',base)
        # Updating the balance preserves the original calculation base and first date.
        updated=self.write('item',dict(base,effective_on='2026-10-27',household_amount='1100'),item['id'])
        self.assertEqual(updated['versions'][-1]['payment_plan']['principal'],'1200')
        with self.assertRaises(DomainError):self.write('item',dict(base,effective_on='2026-10-27',household_amount='1100',payment_plan=dict(base['payment_plan'],periods=11)),item['id'])
        updated=self.write('item',dict(base,effective_on='2026-10-27',household_amount='1100',payment_plan=dict(base['payment_plan'],periods=11,first_due='2026-11-01')),item['id'])
        self.assertEqual(updated['versions'][-1]['payment_plan']['principal'],'1100')
