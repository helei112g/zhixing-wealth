"""Synthetic dated archiving: preserve records, closed intervals and portfolio isolation."""
import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import ConflictError, DomainError
from backend.environment import claim_environment
from backend.family_domain import schedule_projection
from backend.family_service import FamilyService
from backend.service import Service
from backend.store import Store


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=claim_environment(Path(self.temp.name)/'synthetic','dev')
        self.store=Store(self.data);self.family=FamilyService(self.store);self.portfolio=Service(self.store)
        self.clocks=[patch(m+'.today',return_value=date(2026,11,1)) for m in ('backend.family_domain','backend.family_service')]
        for clock in self.clocks:clock.start()
    def tearDown(self):
        for clock in self.clocks:clock.stop()
        self.temp.cleanup()
    def write(self,operation,payload,record=None,preview=False,revision=None):
        return self.family.command(operation,payload,self.store.state()['revision'] if revision is None else revision,record,preview)
    def item(self,category='fixed',**extra):
        return self.write('item',dict(name='合成'+category,category=category,currency='CNY',household_amount='200',effective_on='2026-09-01',**extra))['result']
    def change(self,operation,row,target='item',when='2026-10-10',**extra):
        return self.write(operation,dict(target=target,effective_on=when,reason='合成生命周期测试'),row['id'],**extra)
    def investment_state(self):
        state=self.store.state()
        return {k:copy.deepcopy(v) for k,v in state.items() if k!='revision'}
    def link(self):
        p=self.portfolio.save_portfolio(dict(name='合成关联组合',planned_amount='1000',start_date='2026-09-01'))
        e=self.portfolio.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2026-09-01',instrument=dict(market='CN',code='159307'),quantity='10',gross_amount='100',fx_rate='1'))
        self.portfolio.cache_prices(e['instrument_id'],[dict(date='2026-09-01',price='10')])
        link=self.write('link',dict(portfolio_id=p['id'],effective_on='2026-09-01',share='1'))['result']
        return p,link
    def test_all_asset_categories_archive_without_zeroing_or_deleting(self):
        for category in ('cash','fixed','investment','receivable','debt','insurance'):
            with self.subTest(category=category):
                item=self.item(category);before=self.family.catalog();investments=self.investment_state()
                self.change('archive',item)
                after=self.family.catalog()
                self.assertEqual(after['values'],before['values'])
                actual=next(i for i in after['items'] if i['id']==item['id'])
                self.assertEqual(actual['versions'][:-1],item['versions'])
                self.assertIn(item['id'],[r['id'] for r in self.family.read('2026-10-09')['items']])
                self.assertNotIn(item['id'],[r['id'] for r in self.family.read('2026-10-10')['items']])
                self.assertIn(item['id'],[r['id'] for r in after['archive_entries']])
                self.assertEqual(self.investment_state(),investments)
    def test_preview_rolls_back_every_record_and_revision(self):
        item=self.item();before=self.family.catalog();state=self.investment_state()
        result=self.change('archive',item,preview=True)
        self.assertTrue(result['preview']);self.assertEqual(result['summary']['provisional_assets'],'0')
        self.assertEqual(self.family.catalog(),before);self.assertEqual(self.investment_state(),state)
    def test_restoring_preserves_archive_gap_and_existing_balances(self):
        item=self.item();self.change('archive',item);self.change('restore',item,when='2026-10-20')
        self.assertEqual(self.family.read('2026-10-09')['total_assets'],'200')
        self.assertEqual(self.family.read('2026-10-15')['total_assets'],'0')
        self.assertEqual(self.family.read('2026-10-20')['total_assets'],'200')
        self.assertEqual(len(self.family.catalog()['values']),1)
        self.assertEqual(self.family.catalog()['archive_entries'],[])
    def test_archive_and_restore_link_only_changes_association_intervals(self):
        p,link=self.link();before=self.investment_state();self.change('archive',link,'link')
        self.assertEqual(self.family.read('2026-10-09')['total_assets'],'100')
        self.assertEqual(self.family.read('2026-10-10')['total_assets'],'0')
        self.assertEqual(self.investment_state(),before)
        restored=self.change('restore',link,'link','2026-10-20')['result']
        self.assertNotEqual(restored['id'],link['id']);self.assertEqual(restored['effective_on'],'2026-10-20')
        self.assertEqual(self.family.read('2026-10-15')['total_assets'],'0')
        self.assertEqual(self.family.read('2026-10-20')['total_assets'],'100')
        old=next(r for r in self.family.catalog()['links'] if r['id']==link['id'])
        self.assertEqual(old['closed_on'],'2026-10-10');self.assertEqual(self.investment_state(),before)
        self.assertFalse(self.family.catalog()['archive_entries'][0]['can_restore'])
    def test_restore_cannot_duplicate_an_active_portfolio_association(self):
        _,link=self.link();self.change('archive',link,'link');self.change('restore',link,'link','2026-10-20')
        before=self.family.catalog()
        with self.assertRaises(DomainError):self.change('restore',link,'link','2026-10-21')
        self.assertEqual(self.family.catalog(),before)
    def test_stale_revision_never_writes(self):
        item=self.item();revision=self.store.state()['revision'];self.item('cash');before=self.family.catalog()
        with self.assertRaises(ConflictError):self.change('archive',item,revision=revision)
        self.assertEqual(self.family.catalog(),before)
    def test_invalid_dates_and_targets_do_not_mutate(self):
        item=self.item();before=self.family.catalog()
        for payload in (dict(target='item',effective_on='2026-08-01'),dict(target='item',effective_on='2026-11-02'),dict(target='portfolio',effective_on='2026-10-10')):
            with self.subTest(payload=payload),self.assertRaises(DomainError):self.write('archive',payload,item['id'])
        with self.assertRaises(DomainError):self.change('restore',item)
        self.assertEqual(self.family.catalog(),before)
    def test_archive_cannot_silently_override_later_metadata(self):
        item=self.item();self.write('item',dict(category='fixed',effective_on='2026-10-15',name='合成更名'),item['id']);before=self.family.catalog()
        with self.assertRaises(DomainError):self.change('archive',item,when='2026-10-10')
        self.assertEqual(self.family.catalog(),before)
    def test_valuations_respect_archived_interval_and_allow_old_corrections(self):
        item=self.item();self.change('archive',item)
        with self.assertRaises(DomainError):self.write('valuation',dict(item_id=item['id'],effective_on='2026-10-10',household_amount='900'))
        self.write('valuation',dict(item_id=item['id'],effective_on='2026-10-09',household_amount='210'))
        self.assertEqual(self.family.read('2026-10-09')['total_assets'],'210')
        self.assertEqual(self.family.read('2026-10-10')['total_assets'],'0')
    def test_payment_reminders_exclude_archived_due_dates_but_restore_later_ones(self):
        item=self.item('debt',payment_plan=dict(mode='fixed',first_due='2026-10-15',periods=3,amount='10'))
        self.change('archive',item);self.change('restore',item,when='2026-10-20')
        rows=schedule_projection(self.family.catalog(),'2026-10-01',90)
        self.assertEqual([r['due_date'] for r in rows],['2026-11-15','2026-12-15'])
        self.assertEqual(self.family.read('2026-10-25')['debt_summary']['month_due'],'0')
    def test_balance_history_preserves_household_share_interest_and_fees(self):
        item=self.write('item',dict(name='合成部分归属负债',category='debt',currency='CNY',share='.5',amount='100',interest='5',fees='2',effective_on='2026-09-01'))['result']
        before=self.family.catalog();self.change('archive',item)
        after=self.family.catalog();history=next(v for v in after['value_history'] if v['item_id']==item['id'])
        self.assertEqual(history['household_total'],'53.5');self.assertEqual(after['values'],before['values'])
        self.assertEqual(next(r for r in self.family.read('2026-10-09')['items'] if r['id']==item['id'])['household_amount'],'53.5')
    def test_http_schema_preview_write_and_readback(self):
        item=self.item()
        with TestClient(create_app(self.data,testing=True,auto_sync=False)) as client:
            boot=client.get('/api/bootstrap').json();headers={'X-CSRF-Token':boot['csrf_token']}
            body=dict(operation='archive',record_id=item['id'],payload=dict(target='item',effective_on='2026-10-10'),expected_revision=boot['revision'])
            before=client.get('/api/family/catalog').json()
            self.assertEqual(client.post('/api/family/preview',json=body,headers=headers).status_code,200)
            self.assertEqual(client.get('/api/family/catalog').json(),before)
            saved=client.post('/api/family/commands',json=body,headers=headers)
            self.assertEqual(saved.status_code,200,saved.text)
            self.assertIn(item['id'],[r['id'] for r in client.get('/api/family/catalog').json()['archive_entries']])
            self.assertEqual(client.post('/api/family/commands',json=body,headers=headers).status_code,409)
            self.assertEqual(client.get('/api/family/overview').json()['total_assets'],'0')
