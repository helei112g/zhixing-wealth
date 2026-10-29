"""Synthetic share distributions; no investor ledger or live prices."""
import tempfile
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from backend import dividends, market
from backend.service import Service
from backend.store import Store
from backend.reminders import sync_reminders


def report(**extra):
    return dict(SECURITY_CODE='600000', ASSIGN_PROGRESS='实施分配',
                EQUITY_RECORD_DATE='2025-06-02', EX_DIVIDEND_DATE='2025-06-03',
                BONUS_RATIO='8', IT_RATIO='12', BONUS_IT_RATIO='20', **extra)


class CorporateActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)); self.service=Service(self.store)
        self.p=self.service.save_portfolio(dict(name='合成送转股',start_date='2025-06-01',planned_amount='10000'))
        self.buy=self.event('BUY','2025-06-02',instrument={'market':'CN','code':'600000','name':'合成股票'},quantity='100',gross_amount='3000')
        self.iid=self.buy['instrument_id']
        self.service.cache_prices(self.iid,[dict(date='2025-06-02',price='30'),dict(date='2025-06-03',price='9'),dict(date='2025-06-04',price='10')])

    def tearDown(self):self.temp.cleanup()

    def event(self,kind,date,**extra):
        return self.service.save_event(dict(portfolio_id=self.p['id'],kind=kind,date=date,fx_rate='1',**extra))

    def test_implemented_bonus_and_conversion_without_cash(self):
        actions=dividends.parse_stock_splits([report(PRETAX_BONUS_RMB='0')],'600000')
        self.assertEqual(len(actions),1);self.assertEqual(actions[0]['factor'],'3')
        self.assertEqual(actions[0]['date'],'2025-06-03')
        self.assertEqual(dividends.parse_stock_splits([{**report(),'ASSIGN_PROGRESS':'预披露'}],'600000'),[])
        self.assertEqual(len(dividends.parse_stock_splits([report(),report()],'600000')),1)
        for change in ({'SECURITY_CODE':'000001'},{'BONUS_IT_RATIO':'21'},
                       {'EQUITY_RECORD_DATE':None},{'BONUS_RATIO':None,'IT_RATIO':None,'BONUS_IT_RATIO':None}):
            with self.subTest(change=change),self.assertRaises(market.ProviderError):
                dividends.parse_stock_splits([{**report(),**change}],'600000')

    def test_confirmation_preserves_cost_and_recomputes_profit_without_fake_loss(self):
        before=self.store.state()['events']
        self.service.cache_actions(self.iid,dividends.parse_stock_splits([report()],'600000'))
        self.assertEqual(before,self.store.state()['events'])
        pending=self.service.dashboard(self.p['id'],end='2025-06-03')
        self.assertIsNone(pending['capital_summary']['profit'])
        self.assertIsNone(pending['curve'][-1]['portfolio_return'])
        self.assertEqual(pending['todos'][0]['type'],'confirm_split')
        self.assertEqual(self.service.instrument_detail(self.p['id'],self.iid)['todos'][0]['type'],'confirm_split')
        split=self.event('SPLIT','2025-06-03',instrument_id=self.iid,factor='3')
        self.event('DIVIDEND','2025-06-03',instrument_id=self.iid,gross_amount='300',tax='0',tax_known=True,pay_date='2025-06-03')
        snap=self.service.dashboard(self.p['id'],end='2025-06-03')
        h=snap['holdings'][0]
        self.assertEqual(h['quantity'],'300');self.assertEqual(D(h['cost_including_fees_native']),3000)
        self.assertEqual(D(snap['capital_summary']['profit']),0)
        self.assertEqual(D(snap['curve'][-1]['portfolio_return']),0)
        self.assertEqual(snap['todos'],[])
        self.assertEqual(next(e for e in self.store.state()['events'] if e['id']==self.buy['id']),self.buy)
        self.service.cache_actions(self.iid,dividends.parse_stock_splits([report()],'600000'))
        self.assertEqual(self.service.dashboard(self.p['id'],end='2025-06-03')['holdings'][0]['quantity'],'300')
        self.assertEqual(len([e for e in self.store.state()['events'] if e['kind']=='SPLIT']),1)
        self.event('SELL','2025-06-04',instrument_id=self.iid,quantity='100',gross_amount='1000',sell_lots=[{'buy_event_id':self.buy['id'],'quantity':'100'}])
        sold=self.service.dashboard(self.p['id'],end='2025-06-04')
        self.assertEqual(sold['holdings'][0]['quantity'],'200')
        self.assertEqual(D(sold['capital_summary']['profit']),300)
        self.assertEqual(D(sold['curve'][-1]['portfolio_return']),D('.1'))

    def test_cash_only_null_ratios_do_not_hide_share_distribution(self):
        cash={**report(), 'BONUS_RATIO':None, 'IT_RATIO':None, 'BONUS_IT_RATIO':None,
              'PRETAX_BONUS_RMB':'2.50', 'IMPL_PLAN_PROFILE':'10派2.50元(含税,扣税后2.25元)'}
        reports=[{**cash,'EX_DIVIDEND_DATE':'2026-06-03'},report(),{**cash,'EX_DIVIDEND_DATE':'2024-06-03'}]
        actions=dividends.parse_stock_splits(reports,'600000')
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0]['date'],'2025-06-03')
        self.assertEqual(actions[0]['factor'],'3')
        self.assertEqual(dividends.parse_stock_splits([cash],'600000'),[])
        # Null is never generally equivalent to zero. Unknown plans, partial share
        # ratios and plans with shares must remain explicit sync failures.
        for change in ({'IMPL_PLAN_PROFILE':None}, {'PRETAX_BONUS_RMB':None},
                       {'BONUS_RATIO':'1'}, {'IMPL_PLAN_PROFILE':'10送1股派2.50元(含税)'},
                       {'IMPL_PLAN_PROFILE':'10派2.50元(含税)，另送1股'},
                       {'IMPL_PLAN_PROFILE':'10派0元(含税)'}, {'PRETAX_BONUS_RMB':'-1'}):
            with self.subTest(change=change),self.assertRaises(market.ProviderError):
                dividends.parse_stock_splits([{**cash,**change}],'600000')

    def test_cash_only_differential_and_conflicting_amounts_do_not_mask_real_split(self):
        cash={**report(),'BONUS_RATIO':None,'IT_RATIO':None,'BONUS_IT_RATIO':None,
              'PRETAX_BONUS_RMB':'2.5','IMPL_PLAN_PROFILE':'10派2.5元（含税，扣税后2.25元）（中小股东10派2.8元；大股东10派2.3元）'}
        conflict={**cash,'EX_DIVIDEND_DATE':'2024-06-03','PRETAX_BONUS_RMB':'3',
                  'IMPL_PLAN_PROFILE':'10派2.5元(含税,扣税后2.25元)'}
        notes=[]
        actions=dividends.parse_stock_splits([cash,conflict,report()],'600000',notes=notes)
        self.assertEqual(len(actions),1);self.assertEqual(actions[0]['factor'],'3')
        self.assertEqual(len(notes),2)
        self.assertIn('差异化现金分红',notes[0]);self.assertIn('2024-06-03',notes[1]);self.assertIn('不一致',notes[1])
        for profile in ('10派2.5元(中小股东10派2.8元;大股东10派2.3元)，另转1股',
                        '10派2.5元(中小股东10派2.8元;大股东10派0元)',
                        '10派2.5元(中小股东10派2.8元;其他股东待定)'):
            with self.subTest(profile=profile),self.assertRaises(market.ProviderError):
                dividends.parse_stock_splits([{**cash,'IMPL_PLAN_PROFILE':profile}],'600000')

    def test_successful_cash_only_retry_clears_legacy_failure_without_ledger_changes(self):
        cash={**report(),'BONUS_RATIO':None,'IT_RATIO':None,'BONUS_IT_RATIO':None,
              'PRETAX_BONUS_RMB':'3','IMPL_PLAN_PROFILE':'10派2.5元(含税)(中小股东10派2.8元;大股东10派2.3元)'}
        self.store.save_job(dict(id='legacy-failure',started_at='2024-01-01T01:00',status='partial',items=[
            dict(source_kind='corporate_actions',instrument_id=self.iid,name='600000 送转股',status='error',message='旧解析失败')]))
        old=sync_reminders(self.store.jobs())[0]
        self.assertEqual(old['title'],'600000 企业行动数据 · 同步未完成')
        self.assertEqual(old['action']['instrument_id'],self.iid)
        before=self.store.state()['events']
        quote=dict(rows=[dict(date='2025-06-04',price='10',kind='raw',source='synthetic')],source='synthetic',notes=[])
        with patch('backend.dividends.stock_records',return_value=[cash]),patch('backend.dividends.fetch_notices',return_value=[]),\
             patch('backend.market.quotes',return_value=quote),patch('backend.market.exchange_rates',side_effect=lambda c,*a:[dict(currency=c,date='2025-06-04',rate='1',source='synthetic')]):
            self.service.start_sync(self.iid,trigger='retry')
            for _ in range(200):
                if not self.service.syncing:break
                time.sleep(.01)
            self.assertFalse(self.service.syncing)
        jobs=self.store.jobs();self.assertTrue(any(j['id']=='legacy-failure' for j in jobs))
        item=next(i for i in jobs[0]['items'] if i['source_kind']=='corporate_actions')
        self.assertEqual(item['status'],'ok');self.assertEqual(item['count'],0)
        self.assertTrue(any('不一致' in n for n in item['notes']))
        self.assertEqual(sync_reminders(jobs),[])
        self.assertEqual(self.store.state()['events'],before)
        self.assertEqual(self.store.state()['corporate_actions'],[])
        self.assertEqual(self.service.dashboard(self.p['id'],end='2025-06-04')['todos'],[])

    def test_split_replay_precedes_same_day_buy_even_when_recorded_later(self):
        other=self.event('BUY','2025-06-03',instrument_id=self.iid,quantity='10',gross_amount='90')
        self.event('SPLIT','2025-06-03',instrument_id=self.iid,factor='3')
        snap=self.service.dashboard(self.p['id'],end='2025-06-03')
        self.assertEqual(snap['holdings'][0]['quantity'],'310')
        lots=self.service.sale_lots(self.p['id'],self.iid,'2025-06-03',sequence=other['sequence'])
        self.assertEqual(lots[0]['available_quantity'],'300')
        self.assertEqual(len(lots),1)

    def test_unconfirmed_distribution_is_not_hidden_by_later_sale(self):
        self.event('SELL','2025-06-03',instrument_id=self.iid,quantity='100',gross_amount='900',sell_lots=[{'buy_event_id':self.buy['id'],'quantity':'100'}])
        self.service.cache_actions(self.iid,dividends.parse_stock_splits([report()],'600000'))
        snap=self.service.dashboard(self.p['id'],end='2025-06-03')
        self.assertIsNone(snap['capital_summary']['profit'])
        self.assertIsNone(snap['holdings'][0]['performance']['profit'])
        self.assertEqual(snap['todos'][0]['type'],'confirm_split')
        for row in self.service.trade_results(self.p['id'],self.iid):
            self.assertIsNone(row['actual']['profit'])
        self.event('SPLIT','2025-06-03',instrument_id=self.iid,factor='3')
        self.assertEqual(self.service.dashboard(self.p['id'],end='2025-06-03')['holdings'][0]['quantity'],'200')

    def test_sync_discovers_share_action_even_if_price_source_fails(self):
        initial=self.store.state()['events']
        cash={**report(), 'EX_DIVIDEND_DATE':'2024-06-03', 'BONUS_RATIO':None, 'IT_RATIO':None,
              'BONUS_IT_RATIO':None,'PRETAX_BONUS_RMB':'2.50','IMPL_PLAN_PROFILE':'10派2.50元(含税)'}
        with patch('backend.market.exchange_rates',return_value=[dict(currency='USD',date='2025-06-01',rate='7',source='synthetic')]),patch('backend.market.quotes',side_effect=market.ProviderError('synthetic outage')),patch('backend.dividends.stock_records',return_value=[cash,report()]),patch('backend.dividends.fetch_notices',return_value=[]):
            self.service.start_sync(self.iid)
            for _ in range(200):
                if not self.service.syncing:break
                time.sleep(.01)
            self.assertFalse(self.service.syncing)
        self.assertEqual(self.store.state()['events'],initial)
        self.assertEqual(self.service.dashboard(self.p['id'],end='2025-06-03')['todos'][0]['type'],'confirm_split')
        self.assertIsNone(self.service.dashboard(self.p['id'],end='2025-06-03')['curve'][-1]['portfolio_return'])

    def test_adjusted_reference_refreshes_full_span_but_raw_stays_incremental(self):
        calls=[]
        def prices(inst,start,end,adjusted=False):
            calls.append((start,adjusted))
            return [dict(date=start,price='10',kind='adjusted' if adjusted else 'raw',source='synthetic')],None
        with patch('backend.market.eastmoney',side_effect=prices):
            result=market.quotes({'market':'SH','code':'600000'},'2025-06-01','2025-06-04',adjusted_start='2024-01-01')
        self.assertEqual(calls,[('2025-06-01',False),('2024-01-01',True)])
        self.service.cache_prices(self.iid,result['rows'])
        detail=self.service.instrument_detail(self.p['id'],self.iid)
        self.assertEqual(detail['price_basis'],'adjusted')


if __name__=='__main__':unittest.main()
