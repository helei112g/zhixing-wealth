import tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from backend.common import day
from backend.ledger import periods
from backend.service import Service
from backend.store import Store
from backend.trade_analytics import target_path
from backend.trading_calendar import no_expected_session

class OverviewAndQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)));self.p=self.s.save_portfolio({'name':'总览测试','planned_amount':'100000','start_date':'2026-03-30'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,market='CN',code='159307',date='2026-04-02'):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':date,'instrument':{'market':market,'code':code},'quantity':'100','gross_amount':'1000','fx_rate':'1'})
    def prices(self,e,date,price='11'):self.s.cache_prices(e['instrument_id'],[{'date':date,'price':price}])
    def test_verified_holidays_suppress_only_full_closed_gaps(self):
        for market,quote,end in [('SH','2026-04-30','2026-05-05'),('SZ','2026-09-30','2026-10-07'),('HK','2026-04-02','2026-04-07')]:
            self.assertTrue(no_expected_session(market,quote,end))
        self.assertFalse(no_expected_session('HK','2026-04-02','2026-04-08'))
        self.assertFalse(no_expected_session('SH','2026-04-30','2026-05-06'))
        self.assertFalse(no_expected_session('HK','2026-02-13','2026-02-19')) # Feb16 is a half-day session.
    def test_unverified_year_market_and_fund_calendar_stay_conservative(self):
        for args in [('SH','2025-12-31','2026-01-05'),('HK','2027-04-01','2027-04-07'),('US','2026-04-02','2026-04-07'),('FUND','2026-04-30','2026-05-05')]:
            self.assertFalse(no_expected_session(*args))
    def test_cn_holiday_carry_forward_is_not_estimated_but_missing_reopen_quote_is(self):
        e=self.buy(date='2026-04-30');self.prices(e,'2026-04-30')
        holiday=self.s.dashboard(self.p['id'],start='2026-04-30',end='2026-05-05');self.assertFalse(holiday['estimated']);self.assertFalse(holiday['periods'][0]['estimated'])
        missing=self.s.dashboard(self.p['id'],start='2026-04-30',end='2026-05-06');self.assertTrue(missing['estimated'])
        details=missing['periods'][0]['quality_reasons'];self.assertTrue(any('沿用2026-04-30' in d['message'] and d['from']=='2026-05-06' for d in details))
        self.assertEqual(holiday['pnl'],missing['pnl'])
    def test_hk_easter_quote_and_trade_fallback_are_not_confused(self):
        e=self.buy('HK','3110');self.s.save_fx({'currency':'HKD','date':'2026-04-02','rate':'1','mode':'daily'})
        before=self.s.dashboard(self.p['id'],end='2026-04-07');self.assertTrue(before['estimated'])
        self.assertTrue(any('成交/期初价格暂估' in w for w in before['warnings']))
        self.prices(e,'2026-04-02');after=self.s.dashboard(self.p['id'],end='2026-04-07');self.assertFalse(after['estimated'])
        self.assertTrue(self.s.dashboard(self.p['id'],end='2026-04-08')['estimated'])
    def test_target_curve_uses_compound_actual_days_not_a_flat_five_percent(self):
        portfolio={'start_date':'2026-01-01','target_annual_pct':'5'}
        meta,values=target_path([{'date':'2026-01-01'},{'date':'2026-07-01'},{'date':'2027-01-01'}],portfolio)
        self.assertEqual(values[0],0);self.assertAlmostEqual(float(values[-1]),.05,places=12)
        self.assertAlmostEqual(float(values[1]),1.05**(181/365)-1,places=12);self.assertEqual(meta['anchor_date'],'2026-01-01')
    def test_selected_interval_target_rebases_to_previous_day(self):
        p={'start_date':'2026-01-01','target_annual_pct':'5'};meta,v=target_path([{'date':'2026-07-01'},{'date':'2026-07-31'}],p)
        self.assertEqual(meta['anchor_date'],'2026-06-30');self.assertAlmostEqual(float(v[-1]),1.05**(31/365)-1,places=12)
        _,v=target_path([{'date':'2026-07-01'}],dict(p,target_annual_pct='0'));self.assertEqual(v,[D(0)])
    def test_dashboard_target_does_not_change_actual_profit_and_preserves_range_alignment(self):
        e=self.buy();self.prices(e,'2026-04-02');a=self.s.dashboard(self.p['id'],start='2026-05-01',end='2026-05-31')
        self.assertEqual(a['target']['anchor_date'],'2026-04-30');self.assertEqual(a['curve'][-1]['target_return'],a['target']['return'])
        self.s.save_portfolio({**self.p,'target_annual_pct':'8'},self.p['id']);b=self.s.dashboard(self.p['id'],start='2026-05-01',end='2026-05-31')
        self.assertEqual(a['pnl'],b['pnl']);self.assertEqual(a['selected_return'],b['selected_return']);self.assertNotEqual(a['target']['return'],b['target']['return'])
    def test_period_quality_retains_date_range_and_multiple_reasons(self):
        rows=[]
        for d in ['2026-05-06','2026-05-07']:
            rows.append({'date':d,'assets':D(100),'day_profit':D(0),'return':D(0),'segment':1,'estimated':True,'incomplete':False,'components':{k:D(0) for k in ['price','dividends','fx','fees','taxes','other']},'quality_reasons':['标的A缺少收盘价','分红税额待核对']})
        result=periods(rows)[0];reason=result['quality_reasons'][0]
        self.assertEqual(reason,{'message':'标的A缺少收盘价','from':'2026-05-06','to':'2026-05-07','days':2});self.assertEqual(len(result['quality_reasons']),2)
