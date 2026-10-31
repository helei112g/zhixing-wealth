import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from backend.common import DomainError, today
from backend.instruments import BENCHMARKS, describe
from backend.ledger import Ledger
from backend.market import benchmark_quotes, ProviderError
from backend.service import Service
from backend.store import Store
from backend.app import create_app
from fastapi.testclient import TestClient

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
        self.p=self.s.save_portfolio({'name':'预算测试','planned_amount':'100000','start_date':'2024-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def payload(self,**extra):
        return {'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-01',
                'instrument':{'market':'CN','code':'159307','name':'首次名称','currency':'CNY'},
                'quantity':'2000','gross_amount':'2136','fee':'5','fx_rate':'1',
                'asset_class':'权益','region':'国内',**extra}
    def buy(self,**extra):return self.s.save_event(self.payload(**extra))
    def snap(self,**extra):return self.s.dashboard(self.p['id'],end='2024-01-03',**extra)
    def test_budget_available_without_deposit(self):
        r=self.snap();self.assertEqual(D(r['available_cash']),100000);self.assertEqual(D(r['pnl']),0)
    def test_inline_preview_does_not_save_instrument_or_event(self):
        r=self.s.preview_event(self.payload());self.assertTrue(r['instrument_merge']['is_new'])
        self.assertEqual(r['event']['price'],'1.068');self.assertEqual(D(r['after']['available_cash']),97859)
        self.assertEqual(self.store.state()['instruments'],[]);self.assertEqual(self.store.state()['events'],[])
    def test_code_infers_exchange_and_cost_includes_fees(self):
        e=self.buy();r=self.snap();h=r['holdings'][0]
        self.assertEqual(h['instrument']['market'],'SZ');self.assertEqual(e['price'],'1.068')
        self.assertEqual(D(h['cost_including_fees_native']),2141);self.assertEqual(D(h['average_cost']),D('1.0705'))
        self.assertEqual(D(r['pnl']),-5);self.assertEqual(D(r['components']['fees']),5)
    def test_fractional_unit_price_does_not_change_authoritative_total(self):
        e=self.buy(quantity='3',gross_amount='10',fee='1');r=self.snap()
        self.assertEqual(D(e['gross_amount']),10);self.assertEqual(D(r['cash_total']),99989)
    def test_failed_buy_leaves_no_stub(self):
        with self.assertRaises(DomainError):self.buy(gross_amount='100001')
        self.assertEqual(self.store.state()['instruments'],[]);self.assertEqual(self.store.state()['allocations'],[])
    def test_same_code_merges_latest_name_but_preserves_central_classification_and_history(self):
        a=self.buy();self.s.attach_instrument(self.p['id'],a['instrument_id'],{'asset_class':'权益','region':'国内'});second=self.payload(date='2024-01-02',instrument={'market':'CN','code':'159307','name':'最新名称','currency':'CNY'},asset_class='债券')
        preview=self.s.preview_event(second);self.assertEqual(len(preview['instrument_merge']['differences']),1)
        self.assertEqual(self.store.state()['instruments'][0]['name'],'首次名称')
        b=self.s.save_event(second);state=self.store.state();self.assertEqual(a['instrument_id'],b['instrument_id'])
        self.assertEqual(len(state['instruments']),1);self.assertEqual(len(state['allocations']),1)
        self.assertEqual(state['instruments'][0]['name'],'最新名称');self.assertEqual(state['events'][0]['asset_class'],'权益')
        self.s.cache_prices(a['instrument_id'],[{'date':str(today()),'price':'1.2'}])
        holding=self.s.dashboard(self.p['id'],view='summary')['holdings'][0]
        self.assertTrue(all(lot['asset_class']=='权益' for lot in holding['lots']))
        self.assertEqual(sum(D(lot['value_base']) for lot in holding['lots']),4800)
    def test_same_security_other_portfolio_keeps_independent_classification(self):
        a=self.buy();q=self.s.save_portfolio({'name':'另一组合','planned_amount':'50000','start_date':'2024-01-01'})
        b=self.buy(portfolio_id=q['id'],asset_class='债券')
        self.assertEqual(a['instrument_id'],b['instrument_id'])
        self.s.attach_instrument(self.p['id'],a['instrument_id'],{'asset_class':'权益'});self.s.attach_instrument(q['id'],a['instrument_id'],{'asset_class':'债券'})
        alloc=self.store.state()['allocations'];self.assertEqual(next(r for r in alloc if r['portfolio_id']==self.p['id'])['asset_class'],'权益')
        self.assertEqual(next(r for r in alloc if r['portfolio_id']==q['id'])['asset_class'],'债券')
    def test_same_code_can_distinguish_stock_and_otc_fund_namespace(self):
        self.buy(instrument={'market':'CN','code':'000001','name':'股票'},quantity='10',gross_amount='100')
        self.buy(instrument={'market':'FUND','code':'000001','name':'场外基金'},quantity='10',gross_amount='100')
        self.assertEqual(len(self.store.state()['instruments']),2)
    def test_budget_deposit_and_fx_entries_are_rejected(self):
        for k in ('DEPOSIT','OPENING_CASH','WITHDRAW','FX'):
            with self.assertRaises(DomainError):self.s.save_event({'portfolio_id':self.p['id'],'kind':k,'date':'2024-01-01','amount':'100','fx_rate':'1'})
    def test_csv_buy_creates_instrument_and_ignores_retired_tags_and_roundtrips(self):
        text='kind,date,market,code,name,currency,quantity,gross_amount,fee,fx_rate,tags\nBUY,2024-01-01,CN,159307,第一名称,CNY,2000,2136,5,1,红利\nBUY,2024-01-02,CN,159307,第二名称,CNY,100,110,1,1,成长\n'
        r=self.s.csv_preview(text,'events',self.p['id']);self.assertEqual(r['errors'],[])
        self.assertEqual(self.store.state()['instruments'],[])
        self.assertEqual(self.s.csv_import(text,'events',self.p['id'])['imported'],2)
        state=self.store.state();self.assertEqual(len(state['instruments']),1)
        self.assertEqual(state['instruments'][0]['name'],'第二名称');self.assertTrue(all('tags' not in r for name in ('allocations','events') for r in state[name]))
        r=self.s.csv_import(self.s.csv_export(self.p['id']),'events',self.p['id']);self.assertEqual(r['imported'],0)
    def test_invalid_csv_creates_no_partial_instrument(self):
        text='kind,date,market,code,currency,quantity,gross_amount,fx_rate\nBUY,2024-01-01,CN,159307,CNY,100,100,1\nBUY,2024-01-02,CN,510300,CNY,100,999999,1\n'
        with self.assertRaises(DomainError):self.s.csv_import(text,'events',self.p['id'])
        self.assertEqual(self.store.state()['instruments'],[]);self.assertEqual(self.store.state()['events'],[])
    def test_sell_proceeds_return_to_budget_and_partial_cost_keeps_fees(self):
        e=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'kind':'SELL','date':'2024-01-02',
            'instrument_id':e['instrument_id'],'sell_lots':[{'buy_event_id':e['id'],'quantity':'1000'}],'quantity':'1000','gross_amount':'1200','fee':'2','fx_rate':'1'})
        r=self.snap();self.assertEqual(D(r['available_cash']),99057);h=r['holdings'][0]
        self.assertEqual(D(h['cost_including_fees_native']),D('1070.5'))
        self.assertEqual(D(h['average_cost']),D('1.0705'))
    def test_foreign_transactions_settle_to_cny_at_locked_rate(self):
        e=self.buy(instrument={'market':'HK','code':'700','name':'港股','currency':'HKD'},quantity='10',gross_amount='1000',fee='5',fx_rate='.9')
        self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.95','mode':'daily'})
        r=self.snap();self.assertEqual(D(r['cash_total']),D('99095.5'));self.assertEqual(D(r['pnl']),D('45.5'))
        self.assertEqual(r['events'][0]['fx_rate'],'0.9');self.assertEqual(D(next(c for c in r['cash'] if c['currency']=='HKD')['balance']),0)
    def test_unused_reinvestment_field_cannot_override_regular_buy_fx(self):
        self.buy(instrument={'market':'HK','code':'700','name':'港股','currency':'HKD'},quantity='10',gross_amount='1000',fee='5',fx_rate='.9',reinvest_fx_rate='.5')
        self.assertEqual(D(self.snap()['cash_total']),D('99095.5'))
    def test_reinvestment_fx_difference_spent_from_budget_is_own_net_input(self):
        e=self.buy(instrument={'market':'HK','code':'700','name':'港股','currency':'HKD'},quantity='10',gross_amount='1000',fee='0',fx_rate='.9')
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':'REINVEST','date':'2024-01-02',
            'gross_amount':'100','net_amount':'100','pay_date':'2024-01-02','reinvest_quantity':'10','reinvest_gross_amount':'100',
            'fx_rate':'.9','pay_fx_rate':'.9','reinvest_fx_rate':'.92'})
        r=self.snap();self.assertEqual(D(r['cash_total']),99098)
        self.assertEqual(D(r['holdings'][0]['net_invested_base']),902)
    def test_cash_dividend_reinvestment_does_not_add_budget_or_double_count(self):
        e=self.buy();p={'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':'REINVEST','date':'2024-01-02',
            'gross_amount':'10','net_amount':'10','pay_date':'2024-01-02','reinvest_quantity':'3',
            'reinvest_gross_amount':'10','fx_rate':'1'}
        self.s.save_event(p);r=self.snap();self.assertEqual(D(r['capital']),100000);self.assertEqual(D(r['cash_total']),97859)
        self.assertEqual(D(r['holdings'][0]['quantity']),2003)
    def test_budget_change_is_effective_today_and_not_profit(self):
        self.s.save_portfolio({**self.p,'planned_amount':'150000'},self.p['id'])
        old=self.s.dashboard(self.p['id'],end=str(today()-timedelta(days=1)))
        new=self.s.dashboard(self.p['id']);self.assertEqual(D(old['total_assets']),100000)
        self.assertEqual(D(new['total_assets']),150000);self.assertEqual(D(new['pnl']),0);self.assertEqual(D(new['twr']),0)
    def test_budget_cannot_be_lowered_below_committed_spending(self):
        self.buy()
        with self.assertRaises(DomainError):self.s.save_portfolio({**self.p,'planned_amount':'1000'},self.p['id'])
        self.assertEqual(self.s.portfolio(self.store.state(),self.p['id'])['planned_amount'],'100000')
    def test_builtin_benchmarks_need_no_holdings_attachment(self):
        self.assertEqual([b['code'] for b in self.s.bootstrap()['benchmarks']],['H00300','H00985','399606'])
        p=self.s.save_portfolio({**self.p,'benchmark_id':BENCHMARKS[0]['id']},self.p['id'])
        self.assertEqual(p['benchmark_basis'],'total_return');self.assertEqual(self.store.state()['allocations'],[])
        self.s.cache_prices(BENCHMARKS[0]['id'],[{'date':'2023-12-29','price':'100','kind':'total_return'},
            {'date':'2024-01-02','price':'110','kind':'total_return'}])
        b=self.snap()['benchmark'];self.assertFalse(b['comparable']);self.assertIsNone(b['excess']);self.assertEqual(D(b['return']),D('.1'))
    def test_upgrade_keeps_events_and_corrects_wrong_exchange(self):
        old=self.s.save_portfolio({'name':'旧账本','planned_amount':'10000','start_date':'2024-01-01','funding_mode':'cash'})
        i=self.s.save_instrument({'portfolio_id':old['id'],'code':'159307','market':'SH','type':'etf','name':'旧名称'})
        self.s.save_event({'portfolio_id':old['id'],'kind':'DEPOSIT','date':'2024-01-01','amount':'10000','fx_rate':'1'})
        e=self.s.save_event({'portfolio_id':old['id'],'kind':'BUY','date':'2024-01-01','instrument_id':i['id'],'quantity':'10','price':'1','fee':'1','fx_rate':'1'})
        with self.store.connection() as db:
            original=dict(old);original.pop('funding_mode');db.execute('UPDATE portfolios SET data=? WHERE id=?',(json.dumps(original),old['id']))
            db.execute("DELETE FROM meta WHERE key='experience_version'");db.commit()
        Service(self.store);r=self.s.dashboard(old['id'],end='2024-01-03')
        self.assertEqual(D(r['cash_total']),9989);self.assertEqual(D(r['pnl']),-1)
        self.assertEqual(next(t for t in r['events'] if t['id']==e['id'])['gross_amount'],'10')
        self.assertEqual(next(t for t in self.store.state()['instruments'] if t['id']==i['id'])['market'],'SZ')

class BenchmarkProviderTests(unittest.TestCase):
    def test_csi_discards_first_boundary_record_and_uses_total_return_identity(self):
        value={'code':'200','data':[{'tradeDate':'20240101','indexCode':'H00300','close':110},
                {'tradeDate':'20240105','indexCode':'H00300','close':100},{'tradeDate':'20240108','indexCode':'H00300','close':110}]}
        with patch('backend.market.fetch',return_value=json.dumps(value)) as call:
            r=benchmark_quotes(BENCHMARKS[0],'2024-01-01','2024-01-08')
        self.assertEqual([x['date'] for x in r['rows']],['2024-01-05','2024-01-08'])
        self.assertIn('startDate=20231201',call.call_args.args[0]);self.assertTrue(all(x['kind']=='total_return' for x in r['rows']))
    def test_cni_uses_column_names_and_ignores_incorrect_total_zero(self):
        value={'code':200,'total':0,'data':{'indexCode':'399606','item':['close','timestamp'],'data':[[123,1704124800000]]}}
        with patch('backend.market.fetch',return_value=json.dumps(value)):
            r=benchmark_quotes(BENCHMARKS[2],'2024-01-01','2024-01-03')
        self.assertEqual(r['rows'][0]['price'],'123');self.assertEqual(r['rows'][0]['date'],'2024-01-02')
    def test_wrong_index_code_is_rejected(self):
        value={'code':'200','data':[{'tradeDate':'20231201','indexCode':'000300','close':100},
                                  {'tradeDate':'20240102','indexCode':'000300','close':110}]}
        with patch('backend.market.fetch',return_value=json.dumps(value)):
            with self.assertRaises(ProviderError):benchmark_quotes(BENCHMARKS[0],'2024-01-01','2024-01-03')
    def test_exchange_inference(self):
        self.assertEqual(describe({'market':'CN','code':'159307'})['market'],'SZ')
        self.assertEqual(describe({'market':'CN','code':'510300'})['market'],'SH')
        self.assertEqual(describe({'market':'CN','code':'600519'})['market'],'SH')

class AutomaticUpdateTests(unittest.TestCase):
    def test_saving_buy_and_builtin_benchmark_dispatches_automatic_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,testing=True,auto_sync=True);client=TestClient(app)
            header={'x-csrf-token':client.get('/api/bootstrap').json()['csrf_token']}
            with patch.object(app.state.research.updates,'start'),patch.object(app.state.service,'start_sync') as sync:
                r=client.post('/api/portfolios',headers=header,json={'name':'自动更新测试','planned_amount':'10000','start_date':'2024-01-01','benchmark_id':BENCHMARKS[0]['id']})
                self.assertEqual(r.status_code,200,r.text);p=r.json()
                job=app.state.research.jobs()[0]
                self.assertEqual(job['trigger'],'portfolio_create')
                self.assertIn({'scope':'quotes','instrument_id':BENCHMARKS[0]['id'],'full':True},job['resources'])
                r=client.post('/api/events',headers=header,json={'portfolio_id':p['id'],'kind':'BUY','date':'2024-01-01',
                    'instrument':{'market':'CN','code':'159307','name':'手工名称','currency':'CNY'},'quantity':'100','gross_amount':'100','fx_rate':'1'})
                self.assertEqual(r.status_code,200,r.text)
                job=app.state.research.jobs()[0]
                self.assertEqual(job['trigger'],'event_create')
                self.assertIn({'scope':'quotes','instrument_id':r.json()['instrument_id'],'full':True},job['resources'])
                sync.assert_not_called()  # Execution belongs to the durable planner.
            client.close()
