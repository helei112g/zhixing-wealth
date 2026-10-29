"""Dividend notices are proposals; only explicit confirmation changes the ledger."""
import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from backend import dividends
from backend.common import DomainError, today
from backend.ledger import Ledger
from backend.market import ProviderError
from backend.service import Service
from backend.store import Store


class DividendParserTests(unittest.TestCase):
    def test_fund_per_ten_and_rowspan_are_exact(self):
        html='''<script>ignore()</script><table><tr><th>年份</th><th>权益登记日</th><th>除息日</th><th>每10份分红</th><th>分红发放日</th></tr>
        <tr><td rowspan="2">2024</td><td>2024-06-10</td><td>2024-06-11</td><td>每10份派现金0.1100元</td><td>2024-06-14</td></tr>
        <tr><td>2024-03-10</td><td>2024-03-11</td><td>0.1500元</td><td>2024-03-14</td></tr></table>'''
        rows=dividends.parse_fund(html,'159307')
        self.assertEqual([D(r['per_share']) for r in rows],[D('.011'),D('.015')])
        self.assertEqual(rows[1]['record_date'],'2024-03-10')
        self.assertEqual(rows[0]['pay_date'],'2024-06-14')
        revised=dividends.parse_fund(html.replace('0.1100','0.1200').replace('06-14','06-15'),'159307')
        self.assertEqual(rows[0]['source_ref'],revised[0]['source_ref'])
    def test_unknown_table_and_bad_rate_fail_instead_of_zero(self):
        with self.assertRaises(ProviderError):dividends.parse_fund('<html>error</html>','159307')
        html='<table><tr><th>权益登记日</th><th>除息日</th><th>每10份分红</th><th>分红发放日</th></tr><tr><td>2024-01-01</td><td>2024-01-02</td><td>未知</td><td>--</td></tr></table>'
        with self.assertRaises(ProviderError):dividends.parse_fund(html,'159307')
    def test_stock_only_implemented_per_ten_no_fake_pay_date(self):
        row={'SECURITY_CODE':'600519','ASSIGN_PROGRESS':'实施分配','REPORT_DATE':'2024-12-31 00:00:00','EQUITY_RECORD_DATE':'2025-06-25','EX_DIVIDEND_DATE':'2025-06-26','PRETAX_BONUS_RMB':'280.2423','NOTICE_DATE':'2025-06-22'}
        result=dividends.parse_stock([row,dict(row,ASSIGN_PROGRESS='预披露')],'600519')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['per_share'],'28.02423');self.assertIsNone(result[0]['pay_date'])
        with self.assertRaises(ProviderError):dividends.parse_stock([row],'000001')
    def test_cash_notice_keeps_amount_review_separate_from_share_action_detection(self):
        row=dict(SECURITY_CODE='600000',ASSIGN_PROGRESS='实施分配',EX_DIVIDEND_DATE='2025-06-03',
                 PRETAX_BONUS_RMB='3',IMPL_PLAN_PROFILE='10派2.5元(含税)(中小股东10派2.8元;大股东10派2.3元)')
        notice=dividends.parse_stock([row],'600000')[0]
        self.assertEqual(notice['per_share'],'0.3')
        self.assertIn('差异化现金分红',notice['coverage_note'])
        self.assertIn('不一致',notice['coverage_note'])
        self.assertIn('实际到账',notice['coverage_note'])
        self.assertIsNone(notice['pay_date'])
    def test_hk_strict_cash_currency_and_dates(self):
        row={'SECURITY_CODE':'00700','YEAR':'2025','REPORT_TYPE':'年度分配','EX_DIVIDEND_DATE':'2026/05/15','DIVIDEND_DATE':'2026/06/01','TRANSFER_END_DATE':'2026/05/19-2026/05/20','PLAN_EXPLAIN':'每股派港币5.3元'}
        result=dividends.parse_hk([row,dict(row,PLAN_EXPLAIN='每10股送1股')],'00700')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['per_share'],'5.3');self.assertEqual(result[0]['currency'],'HKD')
        self.assertIsNone(result[0]['record_date']);self.assertEqual(result[0]['ex_date'],'2026-05-15')
        usd=dividends.parse_hk([dict(row,PLAN_EXPLAIN='每股派美元0.2元')],'00700');self.assertEqual(usd[0]['currency'],'USD')
        self.assertIsNone(dividends.parse_hk([dict(row,PLAN_EXPLAIN='未知方案')],'00700'))
    def test_report_pagination_and_unknown_response(self):
        responses=[json.dumps({'success':True,'code':0,'result':{'pages':2,'data':[{'id':n}]}}) for n in (1,2)]
        with patch('backend.dividends.market.fetch',side_effect=responses) as fetch:
            self.assertEqual(dividends.reports('https://example.test',{}),[{'id':1},{'id':2}]);self.assertIn('pageNumber=2',fetch.call_args[0][0])
        with patch('backend.dividends.market.fetch',return_value='{"code":9201}'):
            self.assertIsNone(dividends.reports('https://example.test',{}))
        with patch('backend.dividends.market.fetch',return_value='{}'):
            with self.assertRaises(ProviderError):dividends.reports('https://example.test',{})


class DividendFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
        self.p=self.s.save_portfolio({'name':'分红测试','planned_amount':'100000','start_date':'2024-01-01'})
        self.buy=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'159307','name':'红利ETF'},'quantity':'2000','gross_amount':'2000','fx_rate':'1','tags':['红利']})
        self.i=self.buy['instrument_id']
    def tearDown(self):self.tmp.cleanup()
    def notice(self,**kw):
        source={'source_ref':'test:159307:2024-01-11','code':'159307','record_date':'2024-01-10','ex_date':'2024-01-11','pay_date':'2024-01-15','per_share':'.011','currency':'CNY','source':'单元测试','source_url':'https://example.test','status':'published',**kw}
        self.s.cache_dividends(self.i,[source]);return self.s.dividend_candidates(self.p['id'],self.i)[0]
    def confirm(self,row,**kw):
        return self.s.confirm_dividend(row['notice']['id'],{**row['suggested_event'],'tax_known':True,**kw})
    def snap(self):return self.s.dashboard(self.p['id'],end='2024-01-20')
    def test_notice_is_not_income_and_uses_record_day_shares(self):
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':self.i,'kind':'SELL','date':'2024-01-09','sell_lots':[{'buy_event_id':self.buy['id'],'quantity':'500'}],'quantity':'500','gross_amount':'500','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-12','instrument_id':self.i,'quantity':'100','gross_amount':'100','fx_rate':'1'})
        before=self.snap();row=self.notice()
        self.assertEqual(row['eligible_quantity'],'1500');self.assertEqual(D(row['estimated_gross']),D('16.50'))
        self.assertEqual(self.snap()['available_cash'],before['available_cash']);self.assertEqual(self.snap()['components']['dividends'],'0');self.assertEqual(len(self.store.state()['events']),3)
    def test_source_revisions_update_notice_without_duplicate(self):
        row=self.notice();revised=self.notice(per_share='.012',pay_date='2024-01-16')
        self.assertEqual(row['notice']['id'],revised['notice']['id']);self.assertEqual(D(revised['estimated_gross']),24)
        self.assertEqual(len(self.store.state()['corporate_actions']),1)
    def test_cash_confirmation_edit_and_repeat_guard(self):
        row=self.notice();event=self.confirm(row,net_amount='21',tax='1',gross_amount='22')
        self.assertEqual(event['source_dividend_id'],row['notice']['id']);self.assertEqual(D(self.snap()['available_cash']),98021)
        self.assertEqual(D(self.snap()['components']['dividends']),22)
        self.assertEqual(self.s.dividend_candidates(self.p['id'])[0]['status'],'confirmed')
        with self.assertRaises(DomainError):self.confirm(row)
        edited=self.s.save_event({**event,'net_amount':'20','tax':'2','change_reason':'实际税费修正'},event['id'])
        self.assertEqual(edited['source_dividend_id'],row['notice']['id']);self.assertEqual(D(self.snap()['available_cash']),98020)
    def test_reinvestment_does_not_create_new_principal_or_budget_cash(self):
        row=self.notice();event=self.confirm(row,kind='REINVEST',reinvest_date='2024-01-15',reinvest_quantity='20',reinvest_gross_amount='22',tags=['红利'])
        r=self.snap();self.assertEqual(D(r['available_cash']),98000);self.assertEqual(D(r['holdings'][0]['quantity']),2020)
        self.assertEqual(D(r['capital']),100000);self.assertEqual(event['source_dividend_id'],row['notice']['id'])
    def test_confirmation_failure_rolls_back_everything(self):
        row=self.notice();before=self.store.state()
        with self.assertRaises(DomainError):self.confirm(row,kind='REINVEST',reinvest_quantity='0')
        after=self.store.state();self.assertEqual(before['events'],after['events']);self.assertEqual(before['corporate_actions'],after['corporate_actions']);self.assertEqual(before['instruments'],after['instruments'])
    def test_ignore_restore_no_financial_effect(self):
        row=self.notice();cash=self.snap()['available_cash']
        self.s.ignore_dividend(row['notice']['id'],{'portfolio_id':self.p['id']});self.assertEqual(self.s.dividend_candidates(self.p['id'])[0]['status'],'ignored')
        self.s.ignore_dividend(row['notice']['id'],{'portfolio_id':self.p['id'],'ignored':False});self.assertEqual(self.s.dividend_candidates(self.p['id'])[0]['status'],'pending')
        self.assertEqual(self.snap()['available_cash'],cash)
    def test_existing_manual_dividend_is_not_proposed_again(self):
        row=self.notice();self.s.save_event(row['suggested_event']|{'source_dividend_id':'','tax_known':True})
        self.assertEqual(self.s.dividend_candidates(self.p['id'])[0]['status'],'manual_recorded')
        with self.assertRaises(DomainError):self.confirm(row)
    def test_future_notice_cannot_post(self):
        ex=(today()+timedelta(days=5)).isoformat();row=self.notice(record_date=(today()+timedelta(days=4)).isoformat(),ex_date=ex,pay_date=ex)
        self.assertEqual(row['status'],'future')
        with self.assertRaises(DomainError):self.confirm(row)
        self.s.ignore_dividend(row['notice']['id'],{'portfolio_id':self.p['id']})
        with self.assertRaises(DomainError):self.confirm(row)
    def test_currency_mismatch_is_never_treated_as_instrument_currency(self):
        row=self.notice(currency='USD');self.assertIsNone(row['estimated_gross'])
        with self.assertRaises(DomainError):self.confirm(row,net_amount='22',gross_amount='22')
    def test_same_notice_in_two_portfolios_has_separate_entitlement(self):
        q=self.s.save_portfolio({'name':'第二组合','planned_amount':'10000','start_date':'2024-01-01'})
        self.s.save_event({'portfolio_id':q['id'],'instrument':{'market':'CN','code':'159307'},'kind':'BUY','date':'2024-01-03','quantity':'100','gross_amount':'100','fx_rate':'1'})
        row=self.notice();self.confirm(row);other=self.s.dividend_candidates(q['id'])[0]
        self.assertEqual(other['status'],'pending');self.assertEqual(D(other['estimated_gross']),D('1.10'));self.confirm(other)
        self.assertEqual(len([e for e in self.store.state()['events'] if e['kind']=='DIVIDEND']),2)
    def test_sold_before_registry_has_no_entitlement(self):
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':self.i,'kind':'SELL','date':'2024-01-09','sell_lots':[{'buy_event_id':self.buy['id'],'quantity':'2000'}],'quantity':'2000','gross_amount':'2000','fx_rate':'1'})
        self.s.cache_dividends(self.i,[{'source_ref':'x','code':'159307','record_date':'2024-01-10','ex_date':'2024-01-11','pay_date':'2024-01-15','per_share':'.01','currency':'CNY','source':'test'}])
        self.assertEqual(self.s.dividend_candidates(self.p['id']),[])
    def test_fund_uses_confirmed_shares_only(self):
        e=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-09','confirmation_date':'2024-01-12','instrument':{'market':'FUND','code':'001257'},'quantity':'100','gross_amount':'100','fx_rate':'1'})
        self.s.cache_dividends(e['instrument_id'],[{'source_ref':'x','code':'001257','record_date':'2024-01-10','ex_date':'2024-01-11','pay_date':'2024-01-15','per_share':'.01','currency':'CNY','source':'test'}])
        self.assertEqual(self.s.dividend_candidates(self.p['id'],e['instrument_id']),[])
    def test_hk_uses_pre_ex_date_not_transfer_end_and_reference_fx(self):
        e=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'HK','code':'700'},'quantity':'100','gross_amount':'1000','fx_rate':'.9'})
        self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.9','mode':'daily'})
        self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.5','mode':'manual'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-11','instrument_id':e['instrument_id'],'quantity':'10','gross_amount':'100','fx_rate':'.9'})
        self.s.cache_dividends(e['instrument_id'],[{'source_ref':'hk','code':'00700','record_date':None,'ex_date':'2024-01-11','pay_date':'2024-01-15','per_share':'5.3','currency':'HKD','source':'test'}])
        row=self.s.dividend_candidates(self.p['id'],e['instrument_id'])[0]
        self.assertEqual(row['eligibility_date'],'2024-01-10');self.assertEqual(row['eligible_quantity'],'100');self.assertEqual(row['suggested_event']['fx_rate'],'0.9')
    def test_csv_and_backup_keep_provenance_and_decisions(self):
        row=self.notice();e=self.confirm(row);csv=self.s.csv_export(self.p['id']);self.assertIn(e['source_dividend_id'],csv)
        self.assertEqual(self.s.csv_import(csv,'events',self.p['id'])['imported'],0)
        backup=self.s.backup_download();self.s.ignore_dividend(row['notice']['id'],{'portfolio_id':self.p['id']})
        self.s.restore(backup);state=self.store.state()
        self.assertTrue(any(x.get('source_dividend_id')==row['notice']['id'] for x in state['events']));self.assertTrue(any(x.get('kind')=='dividend_decision' for x in state['corporate_actions']))
    def test_actual_holding_weight_excludes_unused_budget(self):
        e=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'515080'},'quantity':'1000','gross_amount':'1000','fx_rate':'1'})
        r=self.snap();weights={h['instrument_id']:D(h['holding_weight']) for h in r['holdings']}
        self.assertAlmostEqual(weights[self.i],D(2)/3);self.assertAlmostEqual(weights[e['instrument_id']],D(1)/3)
        self.assertEqual(D(r['invested_position']),D('.03'));self.assertEqual(D(r['holdings'][0]['weight']),D('.02'))
    def test_unknown_valuation_does_not_fake_complete_weight(self):
        e=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'HK','code':'700'},'quantity':'100','gross_amount':'1000','fx_rate':'.9'})
        r=self.snap();self.assertIsNone(r['securities_total']);self.assertTrue(all(h['holding_weight'] is None for h in r['holdings']))
    def test_no_holdings_has_no_ratio(self):
        q=self.s.save_portfolio({'name':'空组合','planned_amount':'1000','start_date':'2024-01-01'})
        r=self.s.dashboard(q['id'],end='2024-01-20');self.assertEqual(r['securities_total'],'0');self.assertEqual(r['invested_position'],'0')

class DividendRecalculationTests(unittest.TestCase):
    setUp=DividendFlowTests.setUp
    tearDown=DividendFlowTests.tearDown
    notice=DividendFlowTests.notice
    confirm=DividendFlowTests.confirm
    snap=DividendFlowTests.snap
    def extra_buy(self,quantity='200',date='2024-01-05'):
        return self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':self.i,'kind':'BUY','date':date,'quantity':quantity,'gross_amount':quantity,'fx_rate':'1'})
    def test_pending_notices_recalculate_after_history_insert(self):
        self.notice();self.extra_buy();row=self.s.dividend_candidates(self.p['id'])[0]
        self.assertEqual(row['eligible_quantity'],'2200');self.assertEqual(D(row['estimated_gross']),D('24.20'));self.assertEqual(row['status'],'pending')
        summary=self.snap()['dividend_summary'];self.assertEqual(summary['pending_count'],1);self.assertEqual(D(summary['estimated_gross']),D('24.20'))
    def test_confirmed_dividend_becomes_recheck_and_updates_same_event(self):
        event=self.confirm(self.notice());self.extra_buy();row=self.s.dividend_candidates(self.p['id'])[0]
        self.assertEqual(row['status'],'needs_review');self.assertEqual(D(row['gross_difference']),D('2.20'))
        self.assertEqual(next(e for e in self.store.state()['events'] if e['id']==event['id'])['gross_amount'],'22')
        self.s.preview_event(row['suggested_event'],event['id']);updated=self.confirm(row)
        self.assertEqual(updated['id'],event['id']);self.assertEqual(D(updated['gross_amount']),D('24.20'));self.assertEqual(len(self.store.state()['events']),3)
        self.assertEqual(self.s.dividend_candidates(self.p['id'])[0]['status'],'confirmed')
    def test_source_revision_needs_recheck_without_silent_money_change(self):
        event=self.confirm(self.notice());row=self.notice(per_share='.012')
        self.assertEqual(row['status'],'needs_review');self.assertEqual(D(row['estimated_gross']),24)
        self.assertEqual(next(e for e in self.store.state()['events'] if e['id']==event['id'])['gross_amount'],'22')
    def test_stale_open_proposal_is_rejected(self):
        row=self.notice();self.extra_buy()
        with self.assertRaisesRegex(DomainError,'已变化'):self.confirm(row)
        self.assertEqual(len(self.store.state()['events']),2)
    def test_zero_entitlement_does_not_hide_previously_confirmed_income(self):
        event=self.confirm(self.notice());self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':self.i,'kind':'SELL','date':'2024-01-09','sell_all':True,'gross_amount':'2000','fx_rate':'1'})
        row=self.s.dividend_candidates(self.p['id'])[0];self.assertEqual(row['status'],'needs_review');self.assertEqual(D(row['estimated_gross']),0)
        self.s.delete_event(event['id'],'实际无权益，取消原分红');self.assertEqual(self.s.dividend_candidates(self.p['id']),[])
    def test_reinvest_change_recalculates_subsequent_confirmed_distribution(self):
        first=self.notice();e=self.confirm(first,kind='REINVEST',reinvest_date='2024-01-15',reinvest_quantity='20',reinvest_gross_amount='22')
        second=self.notice(source_ref='second',record_date='2024-01-17',ex_date='2024-01-18',pay_date='2024-01-19')
        self.confirm(second);self.s.save_event({**e,'reinvest_quantity':'40','change_reason':'实际确认份额补正'},e['id'])
        row=next(r for r in self.s.dividend_candidates(self.p['id']) if r['notice']['source_ref']=='second')
        self.assertEqual(row['eligible_quantity'],'2040');self.assertEqual(row['status'],'needs_review');self.assertEqual(D(row['estimated_gross']),D('22.44'))

if __name__=='__main__':unittest.main()
