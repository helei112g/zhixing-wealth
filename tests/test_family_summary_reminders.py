"""Synthetic checks for period accounting and the shared reminder registry."""
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError
from backend.environment import claim_environment
from backend.family_service import FamilyService
from backend.family_notifications import FamilyNotifications
from backend.reminders import ReminderService, dividend_reminders, sync_reminders
from backend.service import Service
from backend.store import Store


class SummaryReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(claim_environment(Path(self.temp.name)/'synthetic','test'))
        self.family=FamilyService(self.store);self.service=Service(self.store)
        self.clock=[patch(m+'.today',return_value=date(2026,10,28)) for m in ('backend.family_domain','backend.family_service','backend.family_summary','backend.reminders','backend.service')]
        for p in self.clock:p.start()
        self.notifications=FamilyNotifications(self.store,self.family,'test')
        self.center=ReminderService(self.store,self.notifications)
    def tearDown(self):
        for p in self.clock:p.stop()
        self.temp.cleanup()
    def write(self,op,payload,rid=None):
        return self.family.command(op,payload,self.store.state()['revision'],rid)['result']
    def item(self,category,amount,when='2025-01-01',rid=None,**extra):
        return self.write('item',dict(category=category,name='合成'+category,currency='CNY',effective_on=when,household_amount=amount,**extra),rid)
    def value(self,item,amount,when):
        self.write('valuation',dict(item_id=item['id'],household_amount=amount,effective_on=when))
    def income(self,month,amount,**extra):
        return self.write('income',dict(kind='salary',month=month,amount=amount,currency='CNY',**extra))
    def test_year_compares_previous_year_end_and_complete_prior_income(self):
        cash=self.item('cash','100');self.value(cash,'900','2025-12-31');self.value(cash,'1200','2026-01-01')
        self.income('2025-01','10');self.income('2025-11','999');self.income('2026-01','20');self.income('2026-10','5')
        s=self.family.summary('year','2026');m=s['metrics']
        self.assertEqual(s['period']['comparison_end'],'2025-12-31')
        self.assertEqual(D(m['total_assets']['value']),1200);self.assertEqual(D(m['total_assets']['amount']),300)
        self.assertEqual(D(m['total_assets']['rate']),D(1)/3);self.assertEqual(D(m['total_assets']['period_change']),300)
        self.assertEqual(D(m['income']['value']),25);self.assertEqual(D(m['income']['amount']),-984);self.assertEqual(D(m['income']['rate']),D(-984)/1009)
    def test_month_and_custom_ranges_use_both_endpoints(self):
        cash=self.item('cash','100');self.value(cash,'300','2026-01-01');self.value(cash,'500','2026-02-28')
        self.income('2025-02','5');self.income('2026-01','500');self.income('2026-02','20')
        s=self.family.summary('month','2026-02');self.assertEqual(s['period']['opening_date'],'2026-01-31')
        self.assertEqual(D(s['metrics']['income']['value']),20);self.assertEqual(D(s['metrics']['total_assets']['period_change']),200)
        s=self.family.summary('custom',start='2026-02-01',end='2026-02-27')
        self.assertIsNone(s['metrics']['income']['value']);self.assertEqual(D(s['metrics']['total_assets']['value']),300)
    def test_overview_previous_month_differs_from_history_yoy_including_leap_year(self):
        with patch('backend.family_summary.today',return_value=date(2025,3,1)):
            self.assertEqual(self.family.summary('month','2025-02')['period']['comparison_end'],'2025-01-31')
        with patch('backend.family_summary.today',return_value=date(2024,3,20)):
            self.assertEqual(self.family.summary('month','2024-03')['period']['comparison_end'],'2024-02-29')
        cash=self.item('cash','100','2024-01-01');self.value(cash,'200','2025-01-01')
        row=self.family.history(start='2025-02-01',end='2025-02-28',comparison='yoy')['rows'][0]
        self.assertEqual(row['comparison_date'],'2024-02-29');self.assertEqual(D(row['changes']['cash']['rate']),1)
        s=self.family.summary('custom',start='2024-02-29',end='2024-02-29');self.assertEqual(s['period']['comparison_end'],'2024-02-28')
    def test_no_prior_zero_and_scope_changes_never_make_up_a_rate(self):
        a=self.item('cash','0');self.value(a,'10','2026-01-01')
        s=self.family.summary('year','2026');self.assertIsNone(s['metrics']['total_assets']['rate']);self.assertEqual(s['metrics']['total_assets']['reason'],'上期金额不大于零')
        self.item('receivable','5','2026-02-01');s=self.family.summary('year','2026')
        self.assertIsNone(s['metrics']['total_assets']['rate']);self.assertEqual(s['metrics']['total_assets']['reason'],'资产范围发生变化')
        s=self.family.summary('year','2025');self.assertIsNone(s['metrics']['total_assets']['previous']);self.assertIsNone(s['metrics']['income']['value'])
    def test_currency_conversion_date_missing_fx_and_income_correction(self):
        i=self.write('income',dict(kind='rent',month='2026-02',currency='USD',amount='10'))
        self.assertIsNone(self.family.summary('month','2026-02')['metrics']['income']['value'])
        with self.store.transaction() as db:
            self.store.upsert_fx(db,dict(currency='USD',date='2026-02-27',rate='7',source='synthetic'))
            self.store.upsert_fx(db,dict(currency='USD',date='2026-03-01',rate='9',source='synthetic'))
        self.assertEqual(D(self.family.summary('month','2026-02')['metrics']['income']['value']),70)
        self.write('income',dict(kind='rent',month='2026-02',currency='USD',amount='20'),i['id'])
        self.assertEqual(D(self.family.summary('month','2026-02')['metrics']['income']['value']),140)
        self.assertEqual(D(self.family.read()['total_assets']),0)
    def test_backdated_entry_for_each_simple_category_preserves_later_value(self):
        for c in ('cash','fixed','investment','receivable'):
            with self.subTest(category=c):
                a=self.item(c,'200','2026-10-28');self.item(c,'100','2026-09-01',a['id'])
                old=next(r for r in self.family.read('2026-09-01')['items'] if r['id']==a['id'])
                current=next(r for r in self.family.read()['items'] if r['id']==a['id'])
                self.assertEqual(D(old['value']),100);self.assertEqual(D(current['value']),200)
                with self.assertRaises(DomainError):self.item(c,'300','2027-01-01',a['id'])
    def test_summary_range_validation_and_http_contract(self):
        for params in [('custom',None,'2026-03-01','2026-02-01'),('year','2027',None,None),('month','2026-13',None,None),('custom',None,'2026-01-01','2027-01-01')]:
            with self.assertRaises(DomainError):self.family.summary(*params)
        with TestClient(create_app(self.store.directory,testing=True,auto_sync=False)) as client:
            self.assertEqual(client.get('/api/family/summary?grain=month&period=2026-02').status_code,200)
            self.assertEqual(client.get('/api/family/summary?grain=custom&start=2026-02-01&end=2026-01-01').status_code,422)
            center=client.get('/api/reminders').json();self.assertEqual(center['count'],0)
    def test_overview_defaults_month_and_reports_six_categories_with_signed_changes(self):
        cash=self.item('cash','100','2026-08-01');fixed=self.item('fixed','200','2026-08-01')
        self.value(cash,'150','2026-10-01');self.value(fixed,'180','2026-10-01')
        self.item('investment','50','2026-08-01');self.item('receivable','20','2026-08-01')
        s=self.family.summary();self.assertEqual(s['period']['grain'],'month');self.assertEqual(s['period']['comparison_end'],'2026-09-30')
        self.assertEqual(D(s['metrics']['liquid_cash']['amount']),50);self.assertEqual(D(s['metrics']['fixed']['amount']),-20)
        self.assertEqual(D(s['metrics']['fixed']['rate']),D('-.1'));self.assertEqual(D(s['metrics']['total_assets']['amount']),30)
        self.assertEqual(D(s['metrics']['investment']['value']),50);self.assertEqual(D(s['metrics']['receivable']['value']),20)
    def test_cash_coverage_uses_available_balance_and_plan_not_total_debt(self):
        self.item('cash','1200','2026-08-01',household_restricted='200')
        self.item('debt','9000','2026-08-01',payment_plan=dict(mode='fixed',amount='100',first_due='2026-09-05',periods=12))
        s=self.family.summary();self.assertEqual(D(s['metrics']['liquid_cash']['value']),1000)
        self.assertEqual(D(s['cash_cover']['monthly_repayment']),100);self.assertEqual(D(s['cash_cover']['months']),10)
        self.item('receivable','900000','2026-08-01');self.assertEqual(D(self.family.summary()['cash_cover']['months']),10)
        self.write('item',dict(category='debt',name='合成未配置借款',currency='CNY',household_amount='200',effective_on='2026-08-01'))
        self.assertIsNone(self.family.summary()['cash_cover']['months'])
    def test_custom_previous_interval_and_unknown_or_zero_cash_coverage(self):
        self.item('cash','100','2026-01-01')
        s=self.family.summary('custom',start='2026-09-01',end='2026-09-29')
        self.assertEqual(s['period']['comparison_start'],'2026-08-03');self.assertEqual(s['period']['comparison_end'],'2026-08-31')
        self.assertEqual(s['cash_cover']['monthly_repayment'],'0');self.assertIsNone(s['cash_cover']['months'])
        self.write('item',dict(category='cash',name='合成外币现金',currency='USD',household_amount='10',effective_on='2026-08-01'))
        s=self.family.summary();self.assertIsNone(s['metrics']['liquid_cash']['value']);self.assertIsNone(s['cash_cover']['months'])
    def test_family_window_excludes_past_distant_and_receivables_and_has_no_action(self):
        self.item('debt','100','2026-10-28',payment_plan=dict(mode='fixed',amount='10',first_due='2026-10-28',periods=3))
        self.item('insurance','0','2026-10-28',policy_end='2026-12-30')
        self.item('receivable','100','2026-10-28',due_on='2026-11-01')
        rows=self.center.read()['items'];self.assertEqual(len(rows),2)
        self.assertTrue(all(0<=r['days']<=35 and r['action'] is None for r in rows));self.assertEqual(self.center.read()['today'],1)
        with patch('backend.reminders.today',return_value=date(2026,10,29)):
            self.assertEqual(len(self.center.read()['items']),1)
    def test_global_dividends_cover_all_portfolios_and_share_native_ids(self):
        portfolios=[self.service.save_portfolio(dict(name='合成组合'+str(i),planned_amount='10000',start_date='2026-01-01')) for i in range(2)]
        for p in portfolios:
            e=self.service.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2026-01-02',instrument=dict(market='CN',code='510300',name='合成ETF'),quantity='100',gross_amount='1000',fx_rate='1'))
        self.service.cache_dividends(e['instrument_id'],[dict(code='510300',source_ref='synthetic',ex_date='2026-02-02',record_date='2026-02-01',pay_date='2026-02-03',per_share='0.1',currency='CNY',source='合成公告')])
        rows=self.center.read()['items'];self.assertEqual(len(rows),2);self.assertEqual({r['portfolio_id'] for r in rows},{p['id'] for p in portfolios})
        native=self.notifications.dividend_rows(self.store.state());self.assertEqual({r['reminder_id'] for r in native},{r['id'] for r in rows})
        c=self.service.dividend_candidates(portfolios[0]['id'])[0]
        self.service.ignore_dividend(c['notice']['id'],dict(portfolio_id=portfolios[0]['id'],ignored=True))
        rows=self.center.read()['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['portfolio_id'],portfolios[1]['id'])
    def test_sync_failure_recovery_does_not_need_financial_revision(self):
        revision=self.store.state()['revision'];item=dict(source_kind='price',instrument_id='synthetic',name='合成行情',status='error',message='synthetic failure')
        self.store.save_job(dict(id='a',started_at='2026-10-28T01:00',status='error',items=[item]))
        self.assertEqual(self.center.read()['count'],1)
        self.store.save_job(dict(id='b',started_at='2026-10-28T02:00',status='done',items=[dict(item,status='done')]))
        self.assertEqual(self.store.state()['revision'],revision);self.assertEqual(self.center.read()['count'],0)
    def test_transaction_tax_review_uses_global_action(self):
        p=self.service.save_portfolio(dict(name='合成组合',planned_amount='10000',start_date='2026-01-01'))
        e=self.service.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2026-01-02',instrument=dict(market='CN',code='510300',name='合成ETF'),quantity='100',gross_amount='1000',fx_rate='1'))
        self.service.save_event(dict(portfolio_id=p['id'],instrument_id=e['instrument_id'],kind='DIVIDEND',date='2026-02-03',ex_date='2026-02-02',gross_amount='10',tax='0',tax_known=False,fx_rate='1'))
        rows=self.center.read()['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source'],'portfolio');self.assertEqual(rows[0]['action']['portfolio_id'],p['id'])
