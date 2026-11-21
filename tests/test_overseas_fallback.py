import io
import json
import tempfile
import time
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from backend import dividends, market, tiingo, us_distributions, yahoo
from backend.service import Service
from backend.store import Store


class OverseasFallback(unittest.TestCase):
    def test_sync_with_continuous_yahoo_403_and_failed_item_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            service = Service(store)
            pid = service.save_portfolio(dict(name='合成', start_date='2020-01-01', planned_amount='10000'))['id']
            today = datetime.now(ZoneInfo('America/New_York')).date()
            first, last = str(today - timedelta(days=5)), str(today - timedelta(days=2))
            buy = service.save_event(dict(portfolio_id=pid, kind='BUY', date=first,
                instrument=dict(market='US', code='TEST', type='etf'), quantity='1', gross_amount='100', fx_rate='7'))
            iid = buy['instrument_id']
            events = store.state()['events']
            upstream, failures = [], []
            blocked = True

            def fetch(url, *args, **kw):
                if 'finance.yahoo.com/' in url:
                    failures.append(url)
                    raise market.ProviderHTTPError(403)
                self.assertIn('/TEST/dividends?assetclass=etf', url)
                return json.dumps({'data': {'dividends': {'rows': []}}})

            def vendor(path, params, token):
                upstream.append(path)
                if blocked:
                    raise market.ProviderHTTPError(403)
                if path == 'TEST':
                    return dict(ticker='TEST', exchangeCode='NASDAQ', startDate=first, endDate=last)
                self.assertEqual(params['startDate'], first)
                return [dict(date=first+'T00:00:00Z', close=120, adjClose=59, splitFactor=1, divCash=0),
                        dict(date=last+'T00:00:00Z', close=61, adjClose=61, splitFactor=2, divCash='0.25')]

            def sync(trigger, **kwargs):
                job_id = service.start_sync(iid, trigger=trigger, **kwargs)['job_id']
                end = time.monotonic()+5
                while time.monotonic() < end:
                    jobs = store.jobs()
                    if not service.syncing:
                        return next(j for j in jobs if j['id'] == job_id)
                    time.sleep(.01)
                self.fail('sync timed out')

            with (patch.object(market, 'fetch', side_effect=fetch),
                  patch.object(tiingo, 'api_token', return_value='synthetic'),
                  patch.object(tiingo, 'request', side_effect=vendor),
                  patch.object(tiingo, '_CACHE', OrderedDict()), patch.object(tiingo, '_BLOCKED_UNTIL', 0),
                  patch.object(yahoo, '_CACHE', {}), patch.object(yahoo, '_RETRY_AFTER', 0),
                  patch.object(yahoo, '_BLOCKED_STATUS', 429), patch.object(dividends, '_NOTICE_CACHE', {})):
                first_job = sync('manual_update', scopes={'quotes'})
                self.assertEqual(first_job['status'], 'partial')
                self.assertTrue(all(it['status'] == 'error' for it in first_job['items']))
                self.assertFalse(store.state()['prices'])
                blocked = False
                upstream.clear()
                second = sync('retry', full=True)
                self.assertEqual(second['status'], 'complete', second['items'])
                self.assertEqual(upstream, ['TEST', 'TEST/prices'])
                self.assertEqual(len(failures), 1)
                state = store.state()
                self.assertEqual(state['events'], events)
                px = {(r['date'], r['kind']): r['price'] for r in state['prices']}
                self.assertEqual((px[first, 'raw'], px[first, 'adjusted']), ('120', '59'))
                split, = [a for a in state['corporate_actions'] if a['kind'] == 'split_notice']
                self.assertEqual((split['date'], split['factor']), (last, '2'))
                cash, = [a for a in state['corporate_actions'] if a['kind'] == 'dividend_notice']
                self.assertEqual((cash['ex_date'], cash['per_share']), (last, '0.25'))
                self.assertEqual(sync('retry', full=True)['items'], [])

    def test_delayed_retry_only_replays_still_failed_overseas_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(Store(Path(tmp)))
            instruments = [dict(id=k, market=m) for k, m in (('a', 'US'), ('b', 'HK'), ('c', 'SH'))]
            items = [dict(instrument_id=k, source_kind='prices', status='error', message='HTTP 403')
                     for k in ('a', 'b', 'c')]
            with patch('backend.service.time.monotonic', return_value=100):
                service._track_overseas_results(instruments, items, automatic=True)
                service._track_overseas_results(instruments, items, automatic=True)
                self.assertEqual(service.overseas_retries, {'a': 700, 'b': 700})
                service.dispatch_overseas_retries(lambda _: self.fail('early retry'))
            service._track_overseas_results(instruments, [dict(items[1], status='ok')], automatic=False)
            with (patch('backend.service.time.monotonic', return_value=701),
                  patch.object(service.store, 'connection'),
                  patch.object(service.store, 'jobs', return_value=[dict(items=[]) for _ in range(20)])):
                queued = []
                service.dispatch_overseas_retries(queued.append)
                self.assertEqual(queued, ['a'])
                self.assertEqual(service.overseas_retries, {})

    def test_current_session_split_keeps_previous_close(self):
        current = datetime.now(tiingo.ZONE).date()
        previous = (current - timedelta(days=1)).isoformat()
        daily = [(previous,'100','50','1','0'), (str(current),'50','50','2','0')]
        with patch.object(tiingo, 'history', return_value=('Synthetic', daily)):
            result = tiingo.quotes(dict(code='TEST'), previous, str(current))
        self.assertEqual({r['date'] for r in result['rows']}, {previous})
        self.assertEqual([(r['date'], r['factor']) for r in result['actions']], [(str(current), '2')])

    def test_truncated_tiingo_history_is_not_cached_as_a_success(self):
        with patch.object(tiingo, '_CACHE', OrderedDict()), patch.object(tiingo, 'api_token', return_value='synthetic'):
            last = datetime.now(tiingo.ZONE).date().isoformat()
            meta = dict(ticker='TEST', exchangeCode='NASDAQ', startDate='2019-01-01', endDate=last)
            price = dict(date=last, close=1, adjClose=1, splitFactor=1, divCash=0)
            with (patch.object(tiingo, 'request', side_effect=[meta, [price]]),
                  self.assertRaisesRegex(market.ProviderError, '未覆盖')):
                tiingo.history(dict(market='US', currency='USD', code='TEST'))
            self.assertFalse(tiingo._CACHE)

    def test_hk_declared_amount_equivalent_and_explicit_no_dividend(self):
        empty = dict(SECURITY_CODE='00999', PLAN_EXPLAIN='未派发或宣派股息', IS_BFP='1')
        self.assertEqual(dividends.parse_hk([empty], '00999'), [])
        self.assertIsNone(dividends.parse_hk([], '00999'))
        self.assertIsNone(dividends.parse_hk([dict(empty, PLAN_EXPLAIN='方案待定')], '00999'))
        row = dict(empty, PLAN_EXPLAIN='每股派人民币2.5元（相当于港币2.7元(计算值)）（可选择以股代息）',
                   EX_DIVIDEND_DATE='2024/06/01', IS_BFP='0')
        item, = dividends.parse_hk([empty, row], '00999')
        self.assertEqual((item['currency'], item['per_share'], item['equivalent_currency'],
                          item['equivalent_per_share'], item['equivalent_calculated'], item['scrip_option']),
                         ('CNY', '2.5', 'HKD', '2.7', True, True))
        with self.assertRaises(market.ProviderError):
            dividends.parse_hk([empty], '00111')

    def test_hk_upgrades_single_existing_reference_without_duplicate_or_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(Store(Path(tmp)))
            pid = service.save_portfolio(dict(name='合成', start_date='2020-01-01', planned_amount='10000'))['id']
            iid = service.save_event(dict(portfolio_id=pid, kind='BUY', date='2021-01-04',
                instrument=dict(market='HK',code='00999'), quantity='1',gross_amount='10',fx_rate='1'))['instrument_id']
            secondary = dict(code='00999',ex_date='2024-06-01',currency='HKD',per_share='2.7',
                             source_ref='cash:HK:00999:2024-06-01:HKD',source='Yahoo现金分派参考')
            primary = dict(secondary, currency='CNY', per_share='2.5', source_ref='cash:HK:00999:2024-06-01:CNY',
                           source='东方财富·港股现金分红公告')
            service.cache_dividends(iid, [secondary])
            before, = service.store.state()['corporate_actions']
            service.cache_dividends(iid, [primary])
            service.cache_dividends(iid, [secondary])
            after, = service.store.state()['corporate_actions']
            self.assertEqual((after['id'], after['currency'], after['source']), (before['id'], 'CNY', primary['source']))

    def test_cash_components_and_incomplete_dates(self):
        ordinary = dict(SECURITY_CODE='00999', EX_DIVIDEND_DATE='2024/06/01', DIVIDEND_DATE='2024/06/14',
                        YEAR='2024', REPORT_TYPE='普通', PLAN_EXPLAIN='每股派美元0.4元(相当于港币3.2元,可选择以股代息)')
        special = dict(ordinary, REPORT_TYPE='特别', PLAN_EXPLAIN='每股派美元0.1元(可选择以股代息)(相当于港币0.8元)')
        unknown = dict(ordinary, EX_DIVIDEND_DATE='2024/09/01', PLAN_EXPLAIN='每股现金分派金额待确认')
        inst = dict(market='HK', currency='HKD', code='00999')
        with patch.object(dividends, 'reports', return_value=[ordinary, special, special, unknown]):
            result = dividends.overseas_notices(inst)
        self.assertIn('2024-09-01', result.coverage_error)
        row, = result
        self.assertEqual((row['per_share'], row['equivalent_per_share'], len(row['components'])), ('0.5','4',2))
        terms = dividends.hk_cash_terms('每股派0.4美元(相当于港币3.2(计算值))(可选择以股代息、汇率美元1元:港币8元)')
        self.assertEqual((terms['per_share'], terms['equivalent_calculated'], terms['scrip_option']), ('0.4', True, True))
        conflict = dict(special, DIVIDEND_DATE='2024/06/20')
        ambiguous = dividends.parse_hk([ordinary, conflict], '00999')
        self.assertEqual(ambiguous, [])
        self.assertIn('2024-06-01', ambiguous.coverage_error)

    def test_issuer_record_survives_nasdaq_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(Store(Path(tmp)))
            pid = service.save_portfolio(dict(name='合成', start_date='2020-01-01', planned_amount='10000'))['id']
            iid = service.save_event(dict(portfolio_id=pid, kind='BUY', date='2021-01-04',
                instrument=dict(market='US',code='TEST'), quantity='1',gross_amount='10',fx_rate='7'))['instrument_id']
            primary = dict(source='State Street·ETF分派表', source_ref='cash:US:TEST:2024-06-01:USD',
                           code='TEST', ex_date='2024-06-01', pay_date='2024-06-05', per_share='0.45678', currency='USD')
            service.cache_dividends(iid, [primary])
            service.cache_dividends(iid, [dict(primary, source='Nasdaq现金派息表', per_share='0.46', pay_date=None)])
            action, = service.store.state()['corporate_actions']
            self.assertEqual((action['source'], action['per_share'], action['pay_date']),
                             (primary['source'], '0.45678', '2024-06-05'))

    def test_vanguard_recent_rows_survive_history_failure_and_retry_can_complete(self):
        inst = dict(market='US', currency='USD', code='VIG', type='stock')
        recent = dict(ex_date='2024-06-01', source='issuer', per_share='1')
        history = [dict(recent, ex_date='2024-03-01'), dict(recent, per_share='2')]
        with (patch.object(us_distributions, 'notices', side_effect=lambda inst: [dict(recent)]),
              patch.object(dividends, '_NOTICE_CACHE', {}),
              patch.object(dividends, 'us_reference_history', side_effect=[market.ProviderHTTPError(403), history])):
            partial = dividends.fetch_notices(inst)
            self.assertIn('403', partial.coverage_error)
            self.assertEqual(partial, [recent])
            complete = dividends.fetch_notices(inst)
            self.assertIsNone(complete.coverage_error)
            self.assertEqual(complete, [recent, history[0]])

    def test_issuer_filters_other_listings_and_sums_cash_components(self):
        book = Workbook()
        sh = book.active
        sh.title = 'dividend'
        sh.append(['TICKER','CUSIP','EX-DATE','RECORD DATE','PAYABLE DATE','DIVIDEND ($)',
                   'SHORT TERM CAPITAL GAIN ($)','LONG TERM CAPITAL GAIN ($)'])
        row = ['SPY','78462F103','01/02/2024','01/03/2024','01/04/2024',1,None,'.1']
        sh.append(['SPYX'] + row[1:])
        sh.append(row)
        f = io.BytesIO()
        book.save(f)
        item, = us_distributions.state_street(f.getvalue())
        self.assertEqual((item['ex_date'], item['per_share'], item['pay_date']), ('2024-01-02','1.1','2024-01-04'))
        vg = dict(dashboard={'ticker':'VIG'}, overview={'cusip':'921908844'}, distributions={'incomeCapitalGains':[
            dict(type='Dividend', share='$0.42', reInvestDate='01/02/2024', recordDate='01/03/2024', payableDate='01/04/2024')]})
        item, = us_distributions.vanguard(vg)
        self.assertEqual((item['ex_date'],item['record_date']), ('2024-01-02','2024-01-03'))
        vg['dashboard']['ticker'] = 'VIGI'
        with self.assertRaises(market.ProviderError):
            us_distributions.vanguard(vg)
