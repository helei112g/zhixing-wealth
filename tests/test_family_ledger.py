"""Synthetic ledger navigation data, snapshot history, and comparison contracts."""
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from backend.common import DomainError
from backend.environment import claim_environment
from backend.family_service import FamilyService
from backend.store import Store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(claim_environment(Path(self.temp.name)/'synthetic', 'test'))
        self.service = FamilyService(self.store)
        self.clocks = [patch(m+'.today', return_value=date(2026,10,28)) for m in ('backend.family_domain','backend.family_service')]
        for c in self.clocks: c.start()
    def tearDown(self):
        for c in self.clocks: c.stop()
        self.temp.cleanup()
    def write(self, op, payload, preview=False):
        return self.service.command(op, payload, self.store.state()['revision'], preview=preview)['result']
    def item(self, category, name, amount, when='2026-08-01', **extra):
        return self.write('item', dict(category=category,name=name,household_amount=amount,currency='CNY',effective_on=when,**extra))
    def value(self, item, amount, when, **extra):
        return self.write('valuation', dict(item_id=item['id'],household_amount=amount,effective_on=when,**extra))
    def test_bounds_start_at_records_not_borrowing_date(self):
        self.assertEqual(self.service.catalog()['bounds']['years'], [])
        self.item('debt','合成借款','100',started_on='2000-01-01')
        bounds = self.service.catalog()['bounds']
        self.assertEqual(bounds['first_date'],'2026-08-01');self.assertEqual(bounds['years'],[2026])
        self.write('income',dict(kind='salary',month='2024-06',currency='CNY',amount='10'))
        bounds = self.service.catalog()['bounds']
        self.assertEqual(bounds['years'],[2026,2025,2024]);self.assertEqual(bounds['income_years'],[2024])
        self.assertEqual(bounds['asset_first_date'],'2026-08-01')
    def test_category_components_and_rate_ignore_unrelated_scope_changes(self):
        a=self.item('fixed','合成住宅甲','100');self.item('fixed','合成住宅乙','200')
        self.value(a,'130','2026-09-01');self.item('cash','合成现金','500','2026-09-01')
        row=self.service.history(end='2026-09-30')['rows'][-1]
        self.assertEqual(D(row['changes']['fixed']['rate']),D('.1'))
        self.assertEqual(D(row['changes']['fixed']['amount']),30)
        self.assertIsNone(row['changes']['net']['rate']);self.assertTrue(row['scope_changed'])
        self.assertEqual(sum(D(r['value']) for r in row['components'] if r['category']=='fixed'),300+30)
        self.assertEqual(len([r for r in row['components'] if r['category']=='fixed']),2)
    def test_zero_rate_is_real_but_zero_baseline_and_new_scope_are_not(self):
        a=self.item('cash','合成现金','0');self.value(a,'100','2026-09-01')
        rows=self.service.history()['rows']
        self.assertIsNone(rows[1]['changes']['cash']['rate'])
        self.assertEqual(rows[1]['changes']['cash']['reason'],'对比期金额不大于零')
        self.assertEqual(D(rows[2]['changes']['cash']['rate']),0)
    def test_missing_component_keeps_metric_unknown(self):
        self.write('item',dict(category='investment',name='合成外币',currency='USD',household_amount='10',effective_on='2026-08-01'))
        row=self.service.history()['rows'][-1]
        self.assertIsNone(row['values']['investment']);self.assertIsNone(row['changes']['investment']['rate'])
        self.assertIsNone(row['components'][0]['value'])
    def test_backfill_requires_explicit_extension_preserves_later_snapshots(self):
        for category in ('cash','fixed','investment','receivable'):
            with self.subTest(category=category):
                item=self.item(category,'合成'+category,'200','2026-10-28')
                with self.assertRaises(DomainError): self.value(item,'100','2026-06-30')
                self.value(item,'100','2026-06-30',extend_start=True)
                self.value(item,'110','2026-06-30')
                before=next(r for r in self.service.read('2026-06-30')['items'] if r['id']==item['id'])
                current=next(r for r in self.service.read()['items'] if r['id']==item['id'])
                self.assertEqual(D(before['value']),110);self.assertEqual(D(current['value']),200)
                self.assertEqual(len([v for v in self.service.catalog()['values'] if v['item_id']==item['id']]),3)
    def test_preview_and_failed_backfill_leave_no_metadata_changes(self):
        item=self.item('cash','合成现金','200')
        before=self.service.catalog()
        payload=dict(item_id=item['id'],household_amount='100',effective_on='2026-07-01',extend_start=True)
        self.write('valuation',payload,True);self.assertEqual(self.service.catalog(),before)
        with self.assertRaises(DomainError):self.write('valuation',dict(payload,household_amount='-5'))
        self.assertEqual(self.service.catalog(),before)
        debt=self.item('debt','合成借款','50')
        with self.assertRaises(DomainError):self.value(debt,'50','2026-07-01',extend_start=True)
    def test_monthly_due_uses_entire_calendar_month_and_excludes_next_month(self):
        self.item('cash','合成现金','500')
        self.item('debt','合成贷款','1200',payment_plan=dict(mode='fixed',first_due='2026-10-05',periods=12,amount='100'))
        self.item('debt','合成下月贷款','100',payment_plan=dict(mode='once',first_due='2026-11-01'))
        stats=self.service.read()['debt_summary']
        self.assertEqual(D(stats['total_debt']),1300);self.assertEqual(D(stats['month_due']),100)
        self.assertEqual(D(stats['cash_coverage']),5)
        self.assertEqual(stats['unconfigured'],[])
    def test_unconfigured_loan_is_not_misrepresented_as_zero_due(self):
        self.item('cash','合成现金','500');self.item('debt','合成借款','100')
        stats=self.service.read()['debt_summary'];self.assertIsNone(stats['month_due']);self.assertIsNone(stats['cash_coverage'])
        self.assertEqual(stats['unconfigured'],['合成借款'])
    def test_metadata_edit_does_not_drop_earlier_installment_in_current_month(self):
        item=self.item('debt','合成贷款','1200',payment_plan=dict(mode='fixed',first_due='2026-10-05',periods=12,amount='100'))
        self.service.command('item',dict(category='debt',name='合成贷款更名',currency='CNY',effective_on='2026-10-28'),self.store.state()['revision'],item['id'])
        self.assertEqual(self.service.read()['debt_summary']['month_due'],'100')
    def test_zero_due_has_no_infinite_coverage_and_generated_payments_already_family_share(self):
        self.item('cash','合成现金','500')
        self.assertEqual(self.service.read()['debt_summary']['month_due'],'0')
        self.assertIsNone(self.service.read()['debt_summary']['cash_coverage'])
        self.item('debt','合成贷款','1200',share='0.5',payment_plan=dict(mode='fixed',first_due='2026-10-05',periods=12,amount='100'))
        self.assertEqual(self.service.read()['debt_summary']['month_due'],'100')
    def test_unknown_fx_does_not_make_coverage_look_safe(self):
        self.item('cash','合成现金','500')
        self.write('item',dict(category='debt',name='合成美元贷款',currency='USD',household_amount='100',effective_on='2026-08-01',payment_plan=dict(mode='once',first_due='2026-10-30')))
        stats=self.service.read()['debt_summary'];self.assertTrue(stats['missing_fx'])
        self.assertIsNone(stats['month_due']);self.assertIsNone(stats['cash_coverage'])
