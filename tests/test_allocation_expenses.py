"""Synthetic financial invariants for non-trading plans and monthly expense totals."""
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from backend.allocation_planning import allocation_report, purchase_plan
from backend.common import ConflictError, DomainError
from backend.deployment_guard import capture, verify
from backend.environment import claim_environment
from backend.family_expenses import expense_summary
from backend.family_service import FamilyService
from backend.migration import migrate
from backend.research import providers
from backend.research.validation import validate
from backend.service import Service
from backend.store import Store


class AllocationExpenseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=claim_environment(Path(self.temp.name)/'test','test')
        self.store=Store(self.directory);self.s=Service(self.store);self.f=FamilyService(self.store)
        self.p=self.s.save_portfolio(dict(name='合成配置组合',planned_amount='100000',start_date='2025-01-01'))

    def tearDown(self): self.temp.cleanup()

    def purchase(self,code='000501',pct='10',labels=None,**extra):
        body=dict(instrument=dict(market='FUND',code=code,name='合成基金'+code),target_pct=pct,
                  holding_labels=labels or dict(scope='A股',direction='宽基',detail='中证500'),expected_revision=self.store.state()['revision'],**extra)
        return purchase_plan(self.s,self.p['id'],body)

    def command(self,op,payload,record_id=None,preview=False):
        return self.f.command(op,payload,self.store.state()['revision'],record_id,preview)

    def income(self,month='2025-01',amount='10000',kind='salary'):
        return self.command('income',dict(month=month,amount=amount,kind=kind,currency='CNY'))

    def test_plan_preview_atomic_save_and_conflict_never_change_cash_or_events(self):
        before=self.store.state();finance=self.s.dashboard(self.p['id'],view='summary')
        body=dict(instrument=dict(market='FUND',code='000501',name='合成基金'),target_pct='10',expected_revision=before['revision'],holding_labels=dict(scope='A股',direction='宽基',detail='中证500'))
        preview=purchase_plan(self.s,self.p['id'],body,preview=True)
        self.assertEqual(preview['target_amount'],'10000');self.assertEqual(self.store.state(),before)
        result=purchase_plan(self.s,self.p['id'],body);after=self.s.dashboard(self.p['id'],view='summary')
        for key in ('total_assets','cash_total','securities_total','pnl','capital_summary'): self.assertEqual(after[key],finance[key])
        self.assertEqual(after['holdings'][0]['status'],'计划买入');self.assertIsNone(after['holdings'][0]['performance']['return_on_cost'])
        self.assertEqual(self.store.state()['events'],before['events'])
        saved=self.store.state()
        with self.assertRaises(ConflictError):purchase_plan(self.s,self.p['id'],body)
        self.assertEqual(self.store.state(),saved)
        body['expected_revision']=saved['revision'];again=purchase_plan(self.s,self.p['id'],body)
        self.assertEqual(again['instrument']['id'],result['instrument']['id']);self.assertEqual(len(self.store.state()['allocations']),1)

    def test_http_contract_rejects_invalid_and_stale_plan_or_expense_without_writes(self):
        from fastapi.testclient import TestClient
        from backend.app import create_app
        with TestClient(create_app(self.directory,testing=True,auto_sync=False)) as client:
            boot=client.get('/api/bootstrap').json();headers={'X-CSRF-Token':boot['csrf_token']}
            path='/api/portfolios/'+self.p['id']+'/purchase-plans'
            body=dict(instrument=dict(market='FUND',code='009992',name='合成API计划'),target_pct='10',expected_revision=boot['revision'])
            before=capture(self.directory)
            self.assertEqual(client.post(path+'/preview',json=body,headers=headers).status_code,200)
            for revision in (True,None,'1',-1):
                self.assertEqual(client.post(path,json=dict(body,expected_revision=revision),headers=headers).status_code,422)
            self.assertEqual(client.post(path,json=dict(body,instrument_id='missing'),headers=headers).status_code,422)
            self.assertEqual(capture(self.directory),before)
            saved=client.post(path,json=body,headers=headers);self.assertEqual(saved.status_code,200,saved.text)
            after=capture(self.directory)
            self.assertEqual(client.post(path,json=body,headers=headers).status_code,409)
            expense=dict(operation='expense_month',payload=dict(month='2025-01',actual_amount='100',confirmed=True),expected_revision=boot['revision'])
            self.assertEqual(client.post('/api/family/commands',json=expense,headers=headers).status_code,409)
            self.assertEqual(capture(self.directory),after)
            single=client.get('/api/portfolios/'+self.p['id']+'/allocation-analysis',params=dict(level='instrument',instrument_id=saved.json()['instrument']['id'])).json()
            self.assertEqual(single['rows'][0]['required_amount'],'10000')
            self.assertEqual(client.get('/api/family/cashflow?year=2025').json()['summary']['confirmed_months'],0)

    def test_first_purchase_then_sale_requires_explicit_new_intention(self):
        plan=self.purchase();iid=plan['instrument']['id']
        buy=self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2025-01-02',instrument_id=iid,quantity='100',gross_amount='1000',fx_rate='1'))
        self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['holdings'][0]['status'],'持有中')
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='SELL',date='2025-01-03',instrument_id=iid,quantity='100',gross_amount='1100',fx_rate='1',sell_lots=[dict(buy_event_id=buy['id'],quantity='100')]))
        self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['holdings'][0]['status'],'已清仓')
        self.purchase();self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['holdings'][0]['status'],'计划买入')
        self.s.attach_instrument(self.p['id'],iid,dict(target_pct='0',expected_revision=self.store.state()['revision']))
        self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['holdings'][0]['status'],'已清仓')

    def test_updating_held_target_does_not_rearm_after_sale(self):
        plan=self.purchase();iid=plan['instrument']['id']
        buy=self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2025-01-02',instrument_id=iid,quantity='100',gross_amount='1000',fx_rate='1'))
        self.purchase(pct='20')
        with self.assertRaises(DomainError):self.s.attach_instrument(self.p['id'],iid,dict(target_pct='20',planned_buy=True,expected_revision=self.store.state()['revision']))
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='SELL',date='2025-01-03',instrument_id=iid,quantity='100',gross_amount='1100',fx_rate='1',sell_lots=[dict(buy_event_id=buy['id'],quantity='100')]))
        self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['holdings'][0]['status'],'已清仓')

    def test_hierarchy_fixed_denominator_unclassified_and_opposing_gaps(self):
        a=self.purchase();b=self.purchase('000502')
        for plan,amount in ((a,'15000'),(b,'5000')):
            self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2025-01-02',instrument_id=plan['instrument']['id'],quantity='100',gross_amount=amount,fx_rate='1'))
        self.purchase('000503','5',dict(scope='港股',direction='宽基',detail='中证500'))
        self.purchase('000504','2',dict(scope='美股'))
        scope=allocation_report(self.s,self.p['id'])
        china=next(r for r in scope['rows'] if r['name']=='A股')
        self.assertEqual((china['gap'],china['required_amount'],china['over_amount']),('0','5000','5000'))
        self.assertEqual(china['input_weight'],'0.2');self.assertEqual(china['incomplete_count'],1)
        for level in ('scope','direction','detail','instrument'):
            rows=allocation_report(self.s,self.p['id'],level=level)['rows']
            self.assertEqual(sum(D(r['target_pct']) for r in rows),D(27))
        detail=allocation_report(self.s,self.p['id'],level='detail')['rows']
        self.assertEqual(len([r for r in detail if r['name']=='中证500']),2)
        self.assertTrue(any(r['path']==['美股','未细分','未细分'] for r in detail))
        filtered=allocation_report(self.s,self.p['id'],scope='A股',status='incomplete')['rows']
        self.assertEqual(filtered[0]['input_weight'],'0.2')
        single=allocation_report(self.s,self.p['id'],level='instrument',instrument_id=b['instrument']['id'])
        self.assertEqual(len(single['rows']),1);self.assertEqual(single['rows'][0]['input_weight'],'0.05')
        other=self.s.save_portfolio(dict(name='合成另一组合',planned_amount='10000',start_date='2025-01-01'))
        self.assertEqual(allocation_report(self.s,other['id'])['rows'],[])

    def test_holding_return_uses_remaining_cost_not_cumulative_cost(self):
        plan=self.purchase();iid=plan['instrument']['id']
        buy=self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2025-01-02',instrument_id=iid,quantity='100',gross_amount='1000',fee='10',fx_rate='1'))
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='SELL',date='2025-01-03',instrument_id=iid,quantity='50',gross_amount='600',fx_rate='1',sell_lots=[dict(buy_event_id=buy['id'],quantity='50')]))
        self.s.cache_prices(iid,[dict(date='2025-01-03',price='12',source='synthetic')])
        dash=self.s.dashboard(self.p['id'],view='summary');summary=dash['capital_summary']
        self.assertEqual(summary['remaining_cost'],'505');self.assertEqual(summary['holding_profit'],'95')
        self.assertAlmostEqual(D(summary['holding_return']),D(95)/D(505))
        self.assertEqual(summary['holding_return'],dash['holdings'][0]['performance']['holding_return'])

    def test_month_total_replaces_budget_and_preview_does_not_write(self):
        self.income();self.income(amount='2000',kind='fund')
        self.command('expense_plan',dict(name='合成生活',amount='3000',effective_month='2025-01'))
        before=capture(self.directory)
        payload=dict(month='2025-01',extra_plan='500',actual_amount='4000',confirmed=True)
        self.command('expense_month',payload,preview=True);self.assertEqual(capture(self.directory),before)
        self.command('expense_month',payload)
        report=expense_summary(self.store,2025);row=report['rows'][0]
        self.assertEqual((row['planned'],row['actual'],row['surplus'],row['savings_rate']),('3500','4000','6000','0.6'))
        self.assertEqual(report['summary']['savings_rate'],'0.6')
        self.assertEqual(expense_summary(self.store,2025,True)['rows'][0]['income'],'12000')
        self.assertEqual(self.store.state()['events'],[])
        self.assertEqual(self.s.dashboard(self.p['id'],view='summary')['cash_total'],'100000')

    def test_annual_weighted_rate_missing_zero_and_income_recheck(self):
        first=self.income(amount='10000');self.income('2025-02','20000')
        for at,value in (('2025-01','5000'),('2025-02','2000')):
            self.command('expense_month',dict(month=at,actual_amount=value,confirmed=True))
        summary=expense_summary(self.store,2025)['summary']
        self.assertAlmostEqual(D(summary['savings_rate']),D(23)/D(30));self.assertEqual(summary['confirmed_months'],2)
        self.assertIsNone(expense_summary(self.store,2025)['rows'][2]['income'])
        self.command('expense_month',dict(month='2025-03',actual_amount='100',confirmed=True))
        row=expense_summary(self.store,2025)['rows'][2]
        self.assertEqual(row['income'],'0');self.assertEqual(row['surplus'],'-100');self.assertIsNone(row['savings_rate'])
        self.command('income',dict(month='2025-01',amount='12000',kind='salary',currency='CNY'),first['result']['id'])
        row=expense_summary(self.store,2025)['rows'][0]
        self.assertTrue(row['needs_review']);self.assertFalse(row['confirmed']);self.assertIsNone(row['savings_rate'])

    def test_confirmed_budget_is_frozen_and_versions_restore(self):
        plan=self.command('expense_plan',dict(name='合成固定开支',amount='1000',effective_month='2025-01'))
        self.income();self.command('expense_month',dict(month='2025-01',actual_amount='1200',confirmed=True))
        self.command('expense_plan',dict(name='合成固定开支',amount='2000',effective_month='2025-01'),plan['result']['id'])
        report=expense_summary(self.store,2025)
        self.assertEqual(report['rows'][0]['planned'],'1000');self.assertEqual(report['rows'][1]['planned'],'2000')
        restored=Service(Store(claim_environment(Path(self.temp.name)/'restored','test')))
        restored.restore(self.s.backup_download())
        restored_report=expense_summary(restored.store,2025)
        self.assertGreater(restored_report['revision'],report['revision'])
        self.assertEqual({k:v for k,v in restored_report.items() if k!='revision'},{k:v for k,v in report.items() if k!='revision'})

    def test_invalid_future_actual_conflict_and_duplicate_source_do_not_write(self):
        year=__import__('backend.common',fromlist=['today']).today().year+1
        with self.assertRaises(DomainError):self.command('expense_month',dict(month=f'{year}-01',actual_amount='1'))
        with self.assertRaises(DomainError):self.command('expense_month',dict(month='2025-01',confirmed=True))
        revision=self.store.state()['revision'];self.income()
        with self.assertRaises(ConflictError):self.f.command('expense_month',dict(month='2025-01',actual_amount='1'),revision)
        debt=self.command('item',dict(category='debt',name='合成贷款',currency='CNY',effective_on='2025-01-01',household_amount='12000',payment_plan=dict(mode='fixed',amount='1000',periods=12,first_due='2025-01-15')))
        payload=dict(name='合成还款',source_item_id=debt['result']['id'],effective_month='2025-01')
        self.command('expense_plan',payload)
        with self.assertRaises(DomainError):self.command('expense_plan',payload)
        self.assertEqual(expense_summary(self.store,2025)['rows'][0]['planned'],'1000')

    def test_schema10_upgrade_preserves_every_existing_table_and_revision(self):
        self.income();self.purchase()
        with self.store.connection() as db:
            db.execute('DROP TABLE family_expense_plans');db.execute('DROP TABLE family_expense_months')
            db.execute("UPDATE meta SET value='10' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0009_index_research'");db.commit()
        before=capture(self.directory)
        self.assertTrue(migrate(self.store.path));self.assertFalse(migrate(self.store.path))
        after=capture(self.directory);self.assertTrue(verify(before,after)['preserved'])
        self.assertEqual(after['counts']['family_expense_plans'],0);self.assertEqual(after['counts']['family_expense_months'],0)


class ExpandedIndexTests(unittest.TestCase):
    def test_official_pairs_and_overseas_capabilities(self):
        for code,tr in [('000688','000688CNY01'),('H11136','H01136')]:
            self.assertEqual(providers.identity(tr.lower())['code'],code)
            self.assertEqual(providers.identity(code)['return_code'],tr)
        with self.assertRaises(providers.market.ProviderError):providers.identity('000688CNY010')
        caps=providers.module_capabilities(providers.identity('H11136'))
        self.assertTrue(caps['memberships']['supported']);self.assertFalse(caps['financials']['supported'])

    def test_overseas_identity_is_market_qualified_and_never_calls_a_share_finance(self):
        hk=providers.constituent_identity('0700.HK','香港证券交易所',True)
        us=providers.constituent_identity('PDD.O','纳斯达克证券交易所',True)
        self.assertEqual((hk['code'],hk['market']),('00700.HK','HK'))
        self.assertEqual((us['code'],us['market']),('PDD.O','US'))
        payload=dict(rows=[dict(hk,weight='60'),dict(us,weight='40')],effective_date='2025-01-31')
        validate('memberships',payload)
        with self.assertRaises(providers.market.ProviderError):providers.constituent_identity('PDD.O','上海证券交易所',True)
        with patch.object(providers,'request') as fetch:
            with self.assertRaises(providers.market.ProviderError):providers.financials(payload['rows'],'2025-01-31')
            fetch.assert_not_called()
