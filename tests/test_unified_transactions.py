import json,tempfile,time,unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError
from backend.store import Store
from backend.service import Service
from backend.trade_analytics import xirr

class UnifiedTransactionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)))
        self.p=self.s.save_portfolio({'name':'统一口径','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,code='159307',date='2023-01-01',qty='100',gross='1000',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':date,'instrument':{'market':'CN','code':code},'quantity':qty,'gross_amount':gross,'fx_rate':'1',**kw})
    def price(self,e):self.s.cache_prices(e['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':'2024-01-01','price':'11'}])
    def test_all_primary_returns_and_calendar_use_investment_cost(self):
        a=self.buy(transaction_costs='10');self.price(a)
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','pay_date':'2023-06-03','net_amount':'90','tax':'10','fx_rate':'1'})
        r=self.s.dashboard(self.p['id'],end='2024-01-01');c=r['capital_summary'];perf=r['holdings'][0]['performance']
        self.assertEqual(D(c['profit']),180);self.assertEqual(D(r['cumulative_return']),D(180)/D(1010))
        self.assertEqual(r['cumulative_return'],perf['return_on_cost']);self.assertEqual(r['cumulative_return'],r['curve'][-1]['portfolio_return'])
        self.assertEqual(c['xirr'],perf['xirr']);self.assertEqual(c['xirr'],r['annual_performance']['return'])
        for grain in ('day','week','month','year'):
            rows=r['calendar'][grain];self.assertEqual(sum(D(p['profit']) for p in rows),180)
            for period in rows:self.assertEqual(D(period['return']),D(period['profit'])/D(period['investment_cost']))
    def test_transaction_facts_and_actions_match_detail_exactly(self):
        a=self.buy(transaction_costs='10');b=self.buy('515080');self.price(a);self.price(b)
        div=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','pay_date':'2023-06-03','net_amount':'90','tax':'10','fx_rate':'1'})
        rows=self.s.transaction_rows(self.p['id']);detail=self.s.instrument_detail(self.p['id'],a['instrument_id'])
        self.assertEqual(detail['transactions'],[r for r in rows if r['event'].get('instrument_id')==a['instrument_id']])
        facts=next(r['facts'] for r in rows if r['event']['id']==a['id']);self.assertEqual(facts['base_total'],'1010');self.assertEqual(facts['unit_price'],'10');self.assertEqual(facts['fees_native'],'10')
        facts=next(r['facts'] for r in rows if r['event']['id']==div['id']);self.assertEqual(facts['base_total'],'90');self.assertEqual(facts['distribution_base'],'90')
        self.assertTrue(all(r['can_edit'] and r['can_cancel'] for r in rows))
    def test_buy_xirr_has_same_cashflow_basis_as_full_lot_sale_and_is_frozen(self):
        a=self.buy(transaction_costs='10');self.price(a)
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','net_amount':'50','pay_date':'2023-06-01','fx_rate':'1'})
        sale=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-01-01','sell_all':True,'gross_amount':'1200','transaction_costs':'10','fx_rate':'1'})
        rows={r['event']['id']:r for r in self.s.transaction_rows(self.p['id'])};buy=rows[a['id']]['actual'];sold=rows[sale['id']]['actual']
        self.assertEqual(buy['xirr'],sold['xirr']);self.assertEqual(buy['profit'],'230');self.assertEqual(buy['profit'],sold['profit']);self.assertEqual(buy['dividends_net'],'50')
        self.s.cache_prices(a['instrument_id'],[{'date':'2024-06-01','price':'1000'}]);new=next(r['actual'] for r in self.s.transaction_rows(self.p['id']) if r['event']['id']==a['id']);self.assertEqual(new['xirr'],buy['xirr']);self.assertEqual(new['profit'],buy['profit'])
    def test_late_dividend_tax_reduces_each_net_dividend_view_once(self):
        a=self.buy();self.price(a)
        div=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','pay_date':'2023-06-03','net_amount':'90','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'TAX','related_event_id':div['id'],'date':'2023-08-01','amount':'5','fx_rate':'1'})
        rows={r['event']['id']:r for r in self.s.transaction_rows(self.p['id'])};detail=self.s.instrument_detail(self.p['id'],a['instrument_id'])
        self.assertEqual(rows[a['id']]['actual']['dividends_net'],'85');self.assertEqual(rows[div['id']]['facts']['distribution_base'],'85');self.assertEqual(detail['holding']['performance']['dividends_net'],'85');self.assertEqual(detail['dividend_values'][div['id']]['net_base'],'85')
    def test_no_price_writes_via_api_or_csv(self):
        with TestClient(create_app(self.tmp.name,testing=True,auto_sync=False)) as client:
            headers={'x-csrf-token':client.get('/api/bootstrap').json()['csrf_token']}
            for path in ('/api/prices','/api/prices/reset'):
                self.assertIn(client.post(path,headers=headers,json={}).status_code,(404,405))
            for path in ('/api/csv/preview','/api/csv/import'):
                self.assertEqual(client.post(path,headers=headers,json={'category':'prices','text':'date,price\n2024-01-01,10\n'}).status_code,422)
        self.assertFalse(hasattr(self.s,'save_prices'));self.assertFalse(hasattr(self.s,'reset_manual_price'))
    def test_provider_failure_is_targeted_retryable_and_preserves_cache(self):
        a=self.buy();self.price(a);before=self.s.store.state()['prices']
        def wait():
            end=time.monotonic()+5
            while self.s.syncing and time.monotonic()<end:time.sleep(.01)
            self.assertFalse(self.s.syncing)
        with patch('backend.market.exchange_rates',return_value=[{'date':'2024-01-01','rate':'1','currency':'HKD','source':'fixture'}]),patch('backend.market.quotes',side_effect=RuntimeError('HTTP 503 test')),patch('backend.dividends.fetch_notices',return_value=[]):
            self.s.start_sync(a['instrument_id'],full=True);wait()
        job=self.s.store.jobs()[0];error=next(i for i in job['items'] if i['status']=='error');self.assertEqual(error['instrument_id'],a['instrument_id']);self.assertEqual(error['source_kind'],'prices');self.assertIn('503',error['message']);self.assertEqual(self.s.store.state()['prices'],before)
        result={'rows':[{'date':'2024-01-01','price':'12','kind':'raw'}],'source':'fixture','notes':[]}
        with patch('backend.market.exchange_rates',return_value=[{'date':'2024-01-01','rate':'1','currency':'HKD','source':'fixture'}]),patch('backend.market.quotes',return_value=result),patch('backend.dividends.fetch_notices',return_value=[]):
            self.s.start_sync(error['instrument_id'],full=True);wait()
        self.assertEqual(self.s.store.jobs()[0]['status'],'complete');self.assertEqual(self.s.bootstrap()['price_status'][-1]['latest']['price'],'12')
