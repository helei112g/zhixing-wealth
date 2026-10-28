import tempfile,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from backend.store import Store
from backend.service import Service

class CapitalSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)));self.p=self.s.save_portfolio({'name':'共享摘要','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,market='CN',code='159307',qty='100',gross='1000',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-01','instrument':{'market':market,'code':code},'quantity':qty,'gross_amount':gross,'fx_rate':'7' if market=='US' else '1',**kw})
    def price(self,e,value,at='2024-01-01'):
        self.s.cache_prices(e['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':at,'price':str(value)}])
    def snap(self,end='2024-01-01'):return self.s.dashboard(self.p['id'],end=end)
    def test_running_days_include_start_and_do_not_follow_chart_end(self):
        with patch('backend.service.today',return_value=date(2023,1,1)):
            self.assertEqual(self.s.bootstrap()['portfolios'][0]['running_days'],1)
        with patch('backend.service.today',return_value=date(2024,1,1)):
            self.assertEqual(self.s.bootstrap()['portfolios'][0]['running_days'],366)
            self.assertEqual(self.s.dashboard(self.p['id'],end='2023-06-01')['running_days'],366)
        self.assertNotIn('running_days',self.s.store.state()['portfolios'][0])
    def test_xirr_is_computed_from_combined_flows_not_average_of_instrument_rates(self):
        cn=self.buy();us=self.buy('US','AAPL',qty='10',gross='100');self.s.save_fx({'currency':'USD','date':'2023-01-01','rate':'7','mode':'daily'});self.price(cn,11);self.price(us,12)
        r=self.snap();a=r['capital_summary'];self.assertEqual(D(a['cost']),1700);self.assertEqual(D(a['profit']),240);self.assertEqual(D(a['market_value']),1940)
        self.assertEqual(D(a['invested_amount']),1700);self.assertEqual(D(a['invested_weight']),D('.017'))
        self.assertAlmostEqual(float(a['xirr']),240/1700,places=10)
        self.assertNotAlmostEqual(float(a['xirr']),sum(float(h['performance']['xirr']) for h in r['holdings'])/2,places=6)
    def test_dividends_linked_tax_and_unassigned_expenses_are_not_lost_or_doubled(self):
        e=self.buy(fee='10');self.price(e,11)
        div=self.s.save_event({'portfolio_id':self.p['id'],'kind':'DIVIDEND','instrument_id':e['instrument_id'],'date':'2023-06-01','net_amount':'50','pay_date':'2023-06-03','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'TAX','related_event_id':div['id'],'date':'2023-08-01','amount':'2','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2023-08-02','amount':'5','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'INTEREST','date':'2023-08-03','amount':'3','fx_rate':'1'})
        r=self.snap();a=r['capital_summary'];self.assertEqual(D(a['profit']),136);self.assertEqual(a['profit'],r['pnl']);self.assertEqual(D(a['invested_amount']),964)
        self.assertEqual(D(a['planned_amount'])-D(a['available_amount']),D(a['invested_amount']))
    def test_reinvestment_internal_flows_cancel_in_aggregate_xirr(self):
        e=self.buy();self.price(e,10)
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'REINVEST','instrument_id':e['instrument_id'],'date':'2023-06-01','net_amount':'100','pay_date':'2023-06-03','fx_rate':'1','reinvest_date':'2023-06-03','reinvest_quantity':'10','reinvest_gross_amount':'100','reinvest_fx_rate':'1'})
        a=self.snap()['capital_summary'];self.assertEqual(D(a['market_value']),1100);self.assertEqual(D(a['profit']),100);self.assertAlmostEqual(float(a['xirr']),.1,places=10)
    def test_closed_investment_profit_and_yearly_rate_remain_frozen(self):
        e=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':'SELL','date':'2024-01-01','sell_all':True,'gross_amount':'1100','fx_rate':'1'})
        a=self.snap()['capital_summary'];self.price(e,999,at='2024-06-01');b=self.snap('2024-06-01')['capital_summary']
        self.assertEqual(a['profit'],b['profit']);self.assertEqual(a['xirr'],b['xirr']);self.assertEqual(D(b['market_value']),0);self.assertEqual(D(b['invested_amount']),-100)
    def test_missing_foreign_valuation_is_unknown_instead_of_zero(self):
        self.buy('US','AAPL',qty='10',gross='100');a=self.snap()['capital_summary'];self.assertIsNone(a['market_value']);self.assertIsNone(a['profit']);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost'])
    def test_historical_snapshot_uses_budget_active_on_that_date(self):
        e=self.buy();self.price(e,10)
        with patch('backend.service.today',return_value=date(2024,2,1)):
            self.s.save_portfolio({**self.p,'planned_amount':'200000'},self.p['id'])
        a=self.snap()['capital_summary'];b=self.snap('2024-03-01')['capital_summary'];self.assertEqual(D(a['planned_amount']),100000);self.assertEqual(D(b['planned_amount']),200000);self.assertEqual(a['invested_amount'],b['invested_amount'])
    def test_empty_portfolio_shows_no_fabricated_annual_return(self):
        a=self.snap()['capital_summary'];self.assertEqual(D(a['profit']),0);self.assertEqual(D(a['invested_amount']),0);self.assertEqual(D(a['market_value']),0);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost'])

    def test_expenses_and_interest_alone_do_not_invent_investment_principal(self):
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2023-01-01','amount':'5','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'INTEREST','date':'2024-01-01','amount':'10','fx_rate':'1'})
        a=self.snap()['capital_summary'];self.assertEqual(D(a['profit']),5);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost']);self.assertIn('买入本金',a['xirr_status'])
