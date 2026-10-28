import json,tempfile,unittest
from decimal import Decimal as D
from pathlib import Path
from backend.common import DomainError
from backend.service import Service
from backend.store import Store
from backend.trade_analytics import annual_summary, xirr

class GoalAndDividendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
        self.p=self.s.save_portfolio({'name':'收益测试','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-01','instrument':{'market':'CN','code':'159307'},'quantity':'100','gross_amount':'1000','fx_rate':'1','transaction_costs':'10'})
    def dividend(self,a,**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','net_amount':'90','tax':'10','pay_date':'2023-06-03','fx_rate':'1',**kw})
    def test_default_goal_and_legacy_read_do_not_migrate_financial_data(self):
        self.assertEqual(self.p['target_annual_pct'],'5')
        legacy=dict(self.p);legacy.pop('target_annual_pct')
        with self.store.connection() as db:
            db.execute('UPDATE portfolios SET data=? WHERE id=?',(json.dumps(legacy),self.p['id']));db.commit()
        self.assertEqual(self.s.bootstrap()['portfolios'][0]['target_annual_pct'],'5')
        self.assertEqual(self.s.dashboard(self.p['id'],end='2024-01-01')['annual_performance']['target'],'0.05')
        self.assertNotIn('target_annual_pct',self.store.state()['portfolios'][0])
    def test_goal_is_validated_preserved_and_zero_supported(self):
        for bad in ('-1','100.1','NaN',''):
            with self.assertRaises(DomainError):self.s.save_portfolio({**self.p,'target_annual_pct':bad},self.p['id'])
        saved=self.s.save_portfolio({**self.p,'target_annual_pct':'7.5'},self.p['id'])
        payload=dict(saved);payload.pop('target_annual_pct')
        self.assertEqual(self.s.save_portfolio(payload,self.p['id'])['target_annual_pct'],'7.5')
        self.assertEqual(self.s.save_portfolio({**saved,'target_annual_pct':'0'},self.p['id'])['target_annual_pct'],'0')
    def annual(self,rate='.1',end='2024-01-01',**kw):
        flow=[('2023-01-01',D('-1000')),(end,D('1000')*(1+D(rate)))]
        annual=xirr(flow)
        perf={'xirr':annual['rate'],'xirr_status':annual['status'],'cash_flows':[{'date':d,'amount':v} for d,v in flow],'provisional':False,'pending_settlement':False,**kw}
        return annual_summary(self.p,perf,end)
    def test_annual_comparison_uses_actual_cashflow_xirr_and_percentage_point_gap(self):
        a=self.annual();self.assertAlmostEqual(float(a['return']),.1,places=10);self.assertAlmostEqual(float(a['gap']),.05,places=10)
        a=self.annual(end='2023-07-02');self.assertAlmostEqual(float(a['return']),1.1**(365/182)-1,places=10)
    def test_same_day_and_missing_xirr_remain_unknown(self):
        self.assertIsNone(self.annual(end='2023-01-01')['return'])
        a=self.annual(xirr=None,xirr_status='估值不完整',provisional=True)
        self.assertIsNone(a['return']);self.assertIsNone(a['gap']);self.assertTrue(a['provisional'])
    def test_overall_goal_excludes_idle_budget_and_includes_recorded_dividend(self):
        a=self.buy();self.dividend(a);self.s.cache_prices(a['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':'2024-01-01','price':'11'}])
        r=self.s.dashboard(self.p['id'],end='2024-01-01');self.assertEqual(D(r['pnl']),180)
        expected=xirr([('2023-01-01',D('-1010')),('2023-06-03',D('90')),('2024-01-01',D('1100'))])['rate']
        self.assertEqual(D(r['annual_performance']['return']),expected)
        self.assertEqual(r['annual_performance']['return'],r['capital_summary']['xirr'])
        self.assertEqual(D(r['cumulative_return']),D(180)/D(1010))
        target=self.s.save_portfolio({**self.p,'target_annual_pct':'8'},self.p['id'])
        after=self.s.dashboard(self.p['id'],end='2024-01-01')
        self.assertEqual(r['pnl'],after['pnl']);self.assertEqual(r['twr'],after['twr']);self.assertEqual(after['annual_performance']['target'],'0.08')
        self.assertEqual(self.p['budget_history'],target['budget_history'])
    def test_dividend_records_sum_to_visible_dividend_and_profit(self):
        a=self.buy();cash=self.dividend(a)
        drip=self.dividend(a,kind='REINVEST',date='2023-08-01',pay_date='2023-08-03',net_amount='100',tax='0',reinvest_date='2023-08-03',reinvest_quantity='10',reinvest_gross_amount='100',reinvest_fx_rate='1')
        self.s.cache_prices(a['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':'2024-01-01','price':'11'}])
        detail=self.s.instrument_detail(self.p['id'],a['instrument_id']);perf=detail['holding']['performance']
        self.assertIn(cash['id'],{e['id'] for e in detail['events']});self.assertIn(drip['id'],{e['id'] for e in detail['events']})
        self.assertEqual(sum(D(v['net_base']) for v in detail['dividend_values'].values()),D(perf['dividends_net']))
        self.assertEqual(D(perf['profit']),D(detail['holding']['value_base'])+190-1110)
    def test_verification_flags_only_change_quality_not_cash_or_returns(self):
        a=self.buy();e=self.dividend(a,tax_known=False,ex_date_known=False);self.s.cache_prices(a['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':'2024-01-01','price':'11'}])
        before=self.s.dashboard(self.p['id'],end='2024-01-01');self.assertEqual(len(before['todos']),1)
        self.s.save_event({**e,'tax_known':True,'ex_date_known':True,'change_reason':'核对原始凭证'},e['id'])
        after=self.s.dashboard(self.p['id'],end='2024-01-01');self.assertEqual(after['todos'],[])
        for key in ('pnl','cash_total','twr'):self.assertEqual(before[key],after[key])
        self.assertEqual(before['holdings'][0]['performance']['profit'],after['holdings'][0]['performance']['profit'])
        self.assertEqual(before['holdings'][0]['performance']['xirr'],after['holdings'][0]['performance']['xirr'])
