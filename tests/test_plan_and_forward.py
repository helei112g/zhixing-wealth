import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError, ConflictError
from backend.planning import plan_funding
from backend.service import Service
from backend.store import Store
from backend.trade_windows import forward_performance


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)))
        self.clock=patch('backend.service.today',return_value=date(2024,5,1));self.clock.start()
        self.p=self.s.save_portfolio({'name':'合成计划组合','planned_amount':'500000','start_date':'2024-01-01'})
        self.a=self.buy('510300','350000');self.b=self.buy('159915','50000')
    def tearDown(self):self.clock.stop();self.tmp.cleanup()
    def buy(self,code,total,pid=None):
        return self.s.save_event({'portfolio_id':pid or self.p['id'],'kind':'BUY','date':'2024-01-31',
            'instrument':{'market':'CN','code':code,'name':'合成标的'},'quantity':'100','gross_amount':total,'fx_rate':'1'})
    def plan(self,event,pct):return self.s.attach_instrument(self.p['id'],event['instrument_id'],{'target_pct':pct})
    def check(self,event,pct):return self.s.check_plan(self.p['id'],event['instrument_id'],pct)
    def test_readonly_preview_matches_saved_dashboard_and_nonblocking_shortfall(self):
        self.plan(self.a,'70');before=self.s.store.state();check=self.check(self.b,'40')
        self.assertEqual(self.s.store.state(),before)
        funding=check['plan_funding'];self.assertEqual(funding['status'],'shortfall')
        self.assertEqual(D(funding['available_amount']),100000);self.assertEqual(D(funding['required_amount']),150000)
        self.assertEqual(D(funding['shortfall']),50000);self.assertEqual(D(funding['total_target_pct']),110)
        saved=self.plan(self.b,'40');self.assertEqual(saved['plan_funding'],funding)
        dashboard=self.s.dashboard(self.p['id'],end='2024-05-01')
        self.assertEqual(dashboard['plan_funding'],funding)
        self.assertEqual(D(dashboard['capital_summary']['available_amount']),D(funding['available_amount']))
        self.assertEqual(self.s.store.state()['events'],before['events'])
    def test_overfunded_holding_does_not_offset_other_plans(self):
        self.plan(self.a,'50') # Already over this target by 100,000, but no cash released.
        funding=self.check(self.b,'40')['plan_funding']
        self.assertEqual(D(funding['total_target_pct']),90);self.assertEqual(D(funding['shortfall']),50000)
    def test_balanced_surplus_and_no_plan_are_distinct(self):
        self.plan(self.a,'70')
        self.assertEqual(self.check(self.b,'30')['plan_funding']['status'],'balanced')
        funding=self.check(self.b,'20')['plan_funding'];self.assertEqual(funding['status'],'surplus');self.assertEqual(D(funding['surplus']),50000)
        funding=self.check(self.b,'0')['plan_funding'];self.assertEqual(D(funding['required_amount']),0)
    def test_scope_and_invalid_single_percentage(self):
        q=self.s.save_portfolio({'name':'另一组合','planned_amount':'800000','start_date':'2024-01-01'})
        x=self.buy('510300','100',q['id']);self.s.attach_instrument(q['id'],x['instrument_id'],{'target_pct':'100'})
        self.assertEqual(D(self.check(self.b,'30')['plan_funding']['total_target_pct']),30)
        for value in ('-1','100.1','NaN','Infinity'):
            with self.assertRaises(DomainError):self.plan(self.b,value)
        with self.assertRaises(DomainError):self.s.check_plan(q['id'],self.b['instrument_id'],'20')
    def test_dividend_cash_and_remaining_plan_use_same_net_input(self):
        self.plan(self.a,'70');before=self.check(self.b,'40')['plan_funding']
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':self.b['instrument_id'],'kind':'DIVIDEND',
            'date':'2024-02-01','pay_date':'2024-02-05','gross_amount':'1000','tax':'100','fx_rate':'1'})
        after=self.check(self.b,'40')['plan_funding']
        self.assertEqual(D(after['available_amount'])-D(before['available_amount']),900)
        self.assertEqual(D(after['required_amount'])-D(before['required_amount']),900)
        self.assertEqual(after['shortfall'],before['shortfall'])
    def test_concurrency_guard_remains_for_overallocated_plan(self):
        revision=self.check(self.b,'40')['revision'];self.plan(self.a,'70')
        with self.assertRaises(ConflictError):self.s.attach_instrument(self.p['id'],self.b['instrument_id'],{'target_pct':'40','expected_revision':revision})
    def test_unknown_net_input_and_zero_plan(self):
        snap={'portfolio':{'planned_amount':'100'},'available_cash':D('10'),
              'holdings':[{'instrument_id':'a','target_pct':D('50'),'net_invested_base':None}]}
        funding=plan_funding(snap);self.assertEqual(funding['status'],'unknown');self.assertIsNone(funding['shortfall']);self.assertIsNone(funding['required_amount'])
        funding=plan_funding(snap,instrument_id='a',target_pct=D(0));self.assertEqual(funding['status'],'surplus')
        snap['available_cash']=None;self.assertEqual(plan_funding(snap)['status'],'unknown')
    def test_negative_net_input_is_not_clamped_and_decimal_amounts_are_exact(self):
        snap={'portfolio':{'planned_amount':'1'},'available_cash':D('.3'),
              'holdings':[{'instrument_id':'a','target_pct':D(20),'net_invested_base':D('-.1')},
                          {'instrument_id':'b','target_pct':D(0),'net_invested_base':D('-100')}]}
        funding=plan_funding(snap);self.assertEqual(funding['required_amount'],D('.3'));self.assertEqual(funding['status'],'balanced')


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.e={'id':'trade','instrument_id':'a','portfolio_id':'p','kind':'BUY','date':'2024-01-31','price':'8','currency':'HKD'}
        self.state={'prices':[],'fx':[],'events':[self.e],'corporate_actions':[]}
    def quote(self,at,value,kind='raw',**kw):
        self.state['prices'].append({'instrument_id':'a','date':at,'price':str(value),'kind':kind,'source':'合成行情',**kw})
    def result(self,as_of='2024-05-01',event=None):return forward_performance(self.state,event or self.e,as_of=as_of)
    def test_calendar_months_prices_and_trade_reference_are_separate(self):
        self.quote('2024-01-31',10);self.quote('2024-02-29',12);self.quote('2024-04-30',9)
        result=self.result();one,three=result['windows']
        self.assertEqual((one['target_date'],three['target_date']),('2024-02-29','2024-04-30'))
        self.assertEqual(one['return'],D('.2'));self.assertEqual(one['relative_trade_return'],D('.5'))
        self.assertEqual(three['return'],D('-.1'));self.assertEqual(result['basis'],'raw')
    def test_not_yet_due_never_uses_future_cache(self):
        self.quote('2024-01-31',10);self.quote('2024-02-29',12)
        self.assertTrue(all(w['status']=='pending' and w['return'] is None for w in self.result('2024-02-15')['windows']))
    def test_missing_trade_day_is_not_replaced_by_previous_session_or_manual_price(self):
        self.quote('2024-01-30',10);self.quote('2024-01-31',11,manual=True);self.quote('2024-02-29',12)
        self.assertEqual(self.result()['windows'][0]['status'],'missing_base')
    def test_first_quote_after_target_bounded_by_seven_days_and_asof(self):
        self.e['date']='2024-01-03';self.quote('2024-01-03',10)
        self.quote('2024-02-05',11);self.quote('2024-02-06',13);self.quote('2024-04-11',14)
        one,three=self.result()['windows'];self.assertEqual(one['date'],'2024-02-05');self.assertEqual(three['status'],'missing_quote')
        self.assertEqual(self.result('2024-02-04')['windows'][0]['status'],'missing_quote')
    def test_adjusted_series_never_mixes_with_raw_endpoint(self):
        self.quote('2024-01-31',10);self.quote('2024-02-29',11);self.quote('2024-01-31',5,'adjusted')
        self.assertEqual(self.result()['basis'],'adjusted');self.assertEqual(self.result()['windows'][0]['status'],'missing_quote')
        self.quote('2024-02-29',6,'adjusted');self.assertEqual(self.result()['windows'][0]['return'],D('.2'))
    def test_raw_split_is_unknown_but_adjusted_remains_comparable(self):
        self.quote('2024-01-31',10);self.quote('2024-02-29',11);self.quote('2024-04-30',5)
        self.state['corporate_actions']=[{'instrument_id':'a','kind':'split_notice','date':'2024-03-01','factor':'2'}]
        one,three=self.result()['windows'];self.assertEqual(one['status'],'available');self.assertEqual(three['status'],'corporate_action');self.assertIsNone(three['return'])
        self.quote('2024-01-31',5,'adjusted');self.quote('2024-04-30',5,'adjusted')
        self.assertEqual(self.result()['windows'][1]['return'],D(0))
    def test_reinvest_uses_actual_reinvest_date_and_sell_does_not_flip_market_direction(self):
        self.quote('2024-01-31',10);self.quote('2024-02-29',12)
        e=dict(self.e,kind='REINVEST',date='2024-01-01',reinvest_date='2024-01-31',reinvest_price='8')
        self.assertEqual(self.result(event=e)['trade_date'],'2024-01-31')
        self.assertEqual(self.result(event=dict(self.e,kind='SELL'))['windows'][0]['return'],D('.2'))


class PlanAndForwardApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,testing=True,auto_sync=False)
        self.c=TestClient(self.app);self.c.__enter__();self.h={'x-csrf-token':self.c.get('/api/bootstrap').json()['csrf_token']}
        self.s=self.app.state.service;self.p=self.s.save_portfolio({'name':'API计划','planned_amount':'1000','start_date':'2024-01-01'})['id']
        self.e=self.s.save_event({'portfolio_id':self.p,'kind':'BUY','date':'2024-01-31','instrument':{'market':'CN','code':'510300','name':'API标的'},'quantity':'10','gross_amount':'100','fx_rate':'1'})
        self.base='/api/portfolios/'+self.p+'/instruments/'+self.e['instrument_id']
    def tearDown(self):self.c.__exit__(None,None,None);self.tmp.cleanup()
    def test_plan_check_readonly_schema_and_write(self):
        before=self.s.store.state();r=self.c.get(self.base+'/plan-check?target_pct=90');self.assertEqual(r.status_code,200)
        value=r.json();self.assertEqual(value['plan_funding']['required_amount'],'800');self.assertEqual(self.s.store.state(),before)
        r=self.c.put(self.base,json={'target_pct':'90','expected_revision':value['revision']},headers=self.h)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['plan_funding'],value['plan_funding'])
        schema=self.c.get('/api/openapi.json').json();self.assertIn('PlanCheck',schema['components']['schemas']);self.assertIn('ForwardPerformance',schema['components']['schemas'])
    def test_forward_reference_does_not_replace_actual_profit_and_cancel_hides_reference(self):
        self.s.cache_prices(self.e['instrument_id'],[{'date':'2024-01-31','price':'10'},{'date':'2024-02-29','price':'12'}])
        url='/api/portfolios/'+self.p+'/transactions/'+self.e['id'];value=self.c.get(url).json()
        self.assertEqual(value['forward_performance']['windows'][0]['return'],'0.2')
        self.assertEqual(value['actual'],self.s.trade_results(self.p)[0]['actual']);self.assertNotIn('review',value)
        self.assertEqual(self.c.get(url.replace(self.p,'other-portfolio')).status_code,422)
        self.s.delete_event(self.e['id'],'合成取消')
        self.assertIsNone(self.c.get(url).json()['forward_performance'])


if __name__=='__main__':unittest.main()
