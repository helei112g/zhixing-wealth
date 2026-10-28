import json,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime,timezone
from unittest.mock import patch
from backend.common import DomainError
from backend import dividends,yahoo
from backend.instruments import describe
from backend.market import ProviderError
from backend.service import Service
from backend.store import Store
from backend.app import create_app
from fastapi.testclient import TestClient

class ExtendedMarketsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
        self.p=self.s.save_portfolio({'name':'市场收益测试','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,market='CN',code='159307',date='2023-01-01',qty='100',gross='1000',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':date,'instrument':{'market':market,'code':code},'quantity':qty,'gross_amount':gross,'fx_rate':'1',**kw})
    def prices(self,e,value,date='2024-01-01'):
        self.s.cache_prices(e['instrument_id'],[{'date':date,'price':str(value)}])
    def snap(self,end='2024-01-01'):return self.s.dashboard(self.p['id'],end=end)
    def test_us_identity_currency_class_ticker(self):
        a=describe({'market':'US','code':'brk.b'});self.assertEqual(a['code'],'BRK-B');self.assertEqual(a['identity'],'US:BRK-B');self.assertEqual(a['currency'],'USD')
        e=self.buy('US','AAPL');self.assertEqual(e['currency'],'USD')
        for code in ('^GSPC','AAPL/../../','', 'ETH=F'):
            with self.assertRaises(DomainError):describe({'market':'US','code':code})
        with self.assertRaises(DomainError):describe({'market':'US','code':'AAPL','currency':'HKD'})
    def test_combined_costs_authoritative_for_buy_sell_and_roundtrip(self):
        a=self.buy(transaction_costs='7.35',fee='999',tax='999');self.assertEqual(a['fee'],'7.35');self.assertEqual(a['tax'],'0')
        r=self.snap();self.assertEqual(D(r['available_cash']),D('98992.65'));self.assertEqual(D(r['holdings'][0]['average_cost']),D('10.0735'))
        sale=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-01-01','sell_all':True,'gross_amount':'1100','transaction_costs':'3.25','fx_rate':'1'})
        self.assertEqual(sale['fee'],'3.25');self.assertEqual(sale['tax'],'0')
        self.assertEqual(self.s.csv_import(self.s.csv_export(self.p['id']),'events',self.p['id'])['imported'],0)
    def test_edit_historical_separate_costs_keeps_economic_value(self):
        a=self.buy(fee='2.1',tax='1.2');before=self.snap()['available_cash']
        self.s.save_event({**a,'transaction_costs':'3.3','change_reason':'合并费税显示'},a['id'])
        self.assertEqual(before,self.snap()['available_cash'])
        current=next(e for e in self.store.state()['events'] if e['id']==a['id'])
        cleared=self.s.save_event({**current,'transaction_costs':'0','change_reason':'确认无费税'},a['id'])
        self.assertEqual(cleared['fee'],'0');self.assertEqual(cleared['tax'],'0');self.assertEqual(D(self.snap()['available_cash'])-D(before),D('3.3'))
    def test_holdings_stable_market_code_sort_after_plan_edit(self):
        created=[self.buy('HK','700'),self.buy('US','VOO'),self.buy('CN','515080'),self.buy('CN','159307'),self.buy('US','AAPL')]
        before=[h['instrument']['code'] for h in self.snap()['holdings']]
        self.assertEqual(before,['159307','515080','00700','AAPL','VOO'])
        self.s.attach_instrument(self.p['id'],created[3]['instrument_id'],{'target_pct':'20','tags':['成长']})
        self.assertEqual(before,[h['instrument']['code'] for h in self.snap()['holdings']])
    def test_instrument_annual_return_uses_actual_flows_and_terminal_value(self):
        a=self.buy();self.prices(a,11)
        h=self.snap()['holdings'][0];self.assertEqual(D(h['performance']['profit']),100)
        self.assertAlmostEqual(float(h['performance']['xirr']),.1,places=9)
        self.assertEqual(self.s.instrument_detail(self.p['id'],a['instrument_id'])['holding']['performance']['profit'],h['performance']['profit'])
    def test_multiple_purchases_dividends_and_independent_fee_reconcile(self):
        a=self.buy(fee='10');self.buy(date='2023-06-01',gross='2000',fee='20');self.prices(a,25)
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-07-01','net_amount':'180','pay_date':'2023-07-03','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'FEE','date':'2023-08-01','amount':'10','fx_rate':'1'})
        r=self.snap();self.assertEqual(D(r['holdings'][0]['performance']['profit']),D('2140'));self.assertEqual(D(r['pnl']),D('2140'))
        self.assertIsNotNone(r['holdings'][0]['performance']['xirr'])
    def test_closed_instrument_annual_profit_freezes(self):
        a=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-01-01','sell_all':True,'gross_amount':'1100','fx_rate':'1'})
        before=self.snap()['holdings'][0]['performance'];self.prices(a,999,date='2024-02-01')
        after=self.snap('2024-02-01')['holdings'][0]['performance'];self.assertEqual(before['profit'],after['profit']);self.assertEqual(before['xirr'],after['xirr'])
    def test_todo_points_to_actual_dividend_and_clears_after_verification(self):
        a=self.buy();e=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'DIVIDEND','date':'2023-06-01','net_amount':'10','pay_date':'2023-06-03','fx_rate':'1','tax_known':False,'ex_date_known':False})
        t=self.snap()['todos'][0];self.assertEqual(t['event']['id'],e['id']);self.assertIn('分红税额',t['title']);self.assertIn('除息日',t['title'])
        review=next(r for r in self.s.trade_results(self.p['id']) if r['event']['id']==a['id']);self.assertEqual(review['todos'][0]['event']['id'],e['id'])
        self.s.save_event({**e,'tax_known':True,'ex_date_known':True,'change_reason':'已核对'},e['id']);self.assertEqual(self.snap()['todos'],[])
    def test_us_ex_day_buyer_has_no_right_but_seller_does(self):
        a=self.buy('US','AAPL',date='2024-08-01');self.s.save_fx({'currency':'USD','date':'2024-08-01','rate':'7','mode':'daily'})
        self.buy('US','AAPL',date='2024-08-10',qty='20',gross='200')
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-08-10','sell_lots':[{'buy_event_id':a['id'],'quantity':'100'}],'gross_amount':'1000','fx_rate':'7'})
        self.s.cache_dividends(a['instrument_id'],[{'code':'AAPL','source_ref':'test','record_date':'2024-08-11','ex_date':'2024-08-10','pay_date':'2024-08-13','per_share':'.27','currency':'USD','source':'test'}])
        row=self.s.dividend_candidates(self.p['id'])[0];self.assertEqual(row['eligible_quantity'],'100')
        e=self.s.confirm_dividend(row['notice']['id'],row['suggested_event']);self.assertEqual(e['record_date'],'2024-08-11')
    def test_source_upgrade_same_ex_date_does_not_duplicate_hk_notice(self):
        a=self.buy('HK','3110');source={'code':'03110','source_ref':'old-provider','record_date':None,'ex_date':'2024-03-01','pay_date':None,'per_share':'1.7','currency':'HKD','source':'test'}
        self.s.cache_dividends(a['instrument_id'],[source]);first=self.s.dividend_candidates(self.p['id'])[0]
        self.s.cache_dividends(a['instrument_id'],[{**source,'source_ref':'official','pay_date':'2024-03-07','per_share':'1.7001'}]);after=self.s.dividend_candidates(self.p['id'])
        self.assertEqual(len(after),1);self.assertEqual(first['notice']['id'],after[0]['notice']['id'])
    def test_split_notice_blocks_false_valuation_and_has_confirmation_todo(self):
        a=self.buy('US','SCHD',date='2024-10-01');self.s.save_fx({'currency':'USD','date':'2024-10-01','rate':'7','mode':'daily'});self.prices(a,20,date='2024-10-12')
        self.s.cache_actions(a['instrument_id'],[{'kind':'split_notice','date':'2024-10-11','factor':'3','source':'test'}])
        r=self.snap('2024-10-12');self.assertIsNone(r['total_assets']);self.assertIsNone(r['holdings'][0]['performance']['xirr']);self.assertEqual(r['todos'][0]['type'],'confirm_split')
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SPLIT','date':'2024-10-11','factor':'3','fx_rate':'1'})
        r=self.snap('2024-10-12');self.assertEqual(r['todos'],[]);self.assertEqual(r['holdings'][0]['quantity'],'300');self.assertEqual(D(r['holdings'][0]['value_base']),42000)
    def test_special_payment_before_ex_date_preserved_but_not_auto_confirmed(self):
        a=self.buy('US','AAPL',date='2024-01-01');self.s.cache_dividends(a['instrument_id'],[{'code':'AAPL','source_ref':'special','record_date':None,'ex_date':'2024-03-10','pay_date':'2024-03-01','per_share':'5','currency':'USD','source':'test'}])
        row=self.s.dividend_candidates(self.p['id'])[0]
        with self.assertRaisesRegex(DomainError,'特殊安排'):self.s.confirm_dividend(row['notice']['id'],row['suggested_event'])
    def assert_early_drip(self,used):
        from backend.trade_analytics import lot_projection
        a=self.buy('US','AAPL',date='2024-01-01',fx_rate='7')
        self.prices(a,10,date='2024-01-15');self.s.save_fx({'currency':'USD','date':'2024-01-15','rate':'7.2','mode':'daily'})
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'REINVEST','date':'2024-01-05',
            'gross_amount':'100','net_amount':'100','pay_date':'2024-01-20','fx_rate':'7.3','pay_fx_rate':'7.5',
            'reinvest_date':'2024-01-10','reinvest_quantity':str(D(used)/10),'reinvest_gross_amount':str(used),'reinvest_fx_rate':'7'})
        before=self.snap('2024-01-15');self.assertEqual(D(before['pnl']),920);self.assertEqual(D(before['holdings'][0]['performance']['profit']),920)
        actual=lot_projection(self.store.state(),self.p,'2024-01-15');self.assertEqual(sum(v['profit'] for v in actual.values()),D('920'))
        self.s.save_fx({'currency':'USD','date':'2024-01-25','rate':'7.4','mode':'daily'})
        after=self.snap('2024-01-25');expected=D('1140')+(100-D(used))*D('.1')
        self.assertEqual(D(after['pnl']),expected);self.assertEqual(D(after['holdings'][0]['performance']['profit']),expected)
        actual=lot_projection(self.store.state(),self.p,'2024-01-25');self.assertEqual(sum(v['profit'] for v in actual.values()),expected)
    def test_early_foreign_full_drip_does_not_double_count_receivable_fx(self):self.assert_early_drip(100)
    def test_early_foreign_partial_drip_only_values_remaining_receivable(self):self.assert_early_drip(80)
    def test_isolated_frontend_directory(self):
        folder=Path(self.tmp.name)/'frontend-preview';folder.mkdir();(folder/'index.html').write_text('isolated-front')
        app=create_app(Path(self.tmp.name)/'server',testing=True,auto_sync=False,frontend_directory=folder)
        with TestClient(app) as client:self.assertEqual(client.get('/').text,'isolated-front')

class OverseasParserTests(unittest.TestCase):
    def test_globalx_official_only_target_calendar_and_hkd_units(self):
        html='''<p>Dividend Per Share (HKD)</p><ul id="unlisted"><li><span>wrong</span></li></ul><ul id="dcal"><li>2026<ul><li class="clearfix"><span class="df">24 September 2026</span><span class="df">30 September 2026</span><span>1.70</span><span>100%</span><span>0%</span></li></ul></li></ul>'''
        rows=dividends.parse_globalx(html,'03110');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['per_share'],'1.7');self.assertEqual(rows[0]['pay_date'],'2026-09-30');self.assertEqual(rows[0]['capital_distribution_ratio'],'0%')
        with self.assertRaises(ProviderError):dividends.parse_globalx(html.replace('(HKD)','(USD)'),'03110')
    def test_nasdaq_original_amount_dates_and_empty_business_result(self):
        obj={'data':{'dividends':{'rows':[{'exOrEffDate':'08/07/2020','type':'Cash','amount':'$0.82','recordDate':'08/10/2020','paymentDate':'08/13/2020','currency':'USD'},{'exOrEffDate':'02/08/2019','type':'Cash','amount':'$0.73','recordDate':'N/A','paymentDate':'N/A','currency':'USD'}]}}}
        rows=dividends.parse_nasdaq(obj,'AAPL');self.assertEqual(rows[0]['per_share'],'0.82');self.assertEqual(rows[0]['record_date'],'2020-08-10');self.assertIsNone(rows[1]['pay_date']);self.assertIsNone(dividends.parse_nasdaq({'data':None},'VOO'))
    def test_provider_preference_never_sums_same_ex_day(self):
        primary=[{'code':'03110','ex_date':'2026-09-24','pay_date':'2026-09-30','per_share':'1.7','currency':'HKD','source':'official'}]
        fallback=[{'code':'03110','ex_date':'2026-09-24','per_share':'1.70001','currency':'HKD','source':'fallback'}]
        with patch('backend.dividends.market.fetch',return_value='html'),patch('backend.dividends.parse_globalx',return_value=primary),patch('backend.yahoo.dividends',return_value=fallback):
            rows=dividends.overseas_notices({'market':'HK','code':'03110','currency':'HKD'});self.assertEqual(len(rows),1);self.assertEqual(rows[0]['per_share'],'1.7')
    def test_yahoo_raw_nominal_restoration_uses_later_splits_outside_end(self):
        def ts(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())
        data={'meta':{'symbol':'SCHD','exchangeTimezoneName':'America/New_York','currency':'USD','instrumentType':'ETF'},'timestamp':[ts('2024-10-09T13:30:00')],'indicators':{'quote':[{'close':[D('28.316668')]}],'adjclose':[{'adjclose':[D('25')]}]},'events':{'splits':{'s':{'date':ts('2024-10-11T13:30:00'),'numerator':3,'denominator':1}}}}
        rows=yahoo.parse_quotes(data,{'market':'US','code':'SCHD'},'2024-10-07','2024-10-10')['rows'];raw=next(r for r in rows if r['kind']=='raw');self.assertEqual(raw['split_restore_factor'],'3');self.assertEqual(D(raw['price']),D('84.950004'))
    def test_yahoo_us_midnight_uses_exchange_date(self):
        ts=int(datetime(2024,10,9,0,30,tzinfo=timezone.utc).timestamp());data={'meta':{'symbol':'AAPL','exchangeTimezoneName':'America/New_York'},'timestamp':[ts],'indicators':{'quote':[{'close':[100]}]}}
        r=yahoo.parse_quotes(data,{'market':'US','code':'AAPL'},'2024-10-08','2024-10-09');self.assertEqual(r['rows'][0]['date'],'2024-10-08')
