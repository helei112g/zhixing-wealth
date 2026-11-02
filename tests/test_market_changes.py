import copy
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.market_changes import instrument_market_changes


class QuoteChangeTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(prices=[], events=[], corporate_actions=[], fx=[])
        self.instrument = dict(id='a', market='FUND', currency='CNY')

    def quote(self, at, price, kind='raw', **extra):
        self.state['prices'].append(dict(instrument_id='a', date=at, price=str(price),
                                         kind=kind, source='合成行情', **extra))

    def result(self, at='2026-10-30'):
        return instrument_market_changes(self.state, self.instrument, at)

    def test_daily_and_weekly_use_prices_not_sum_of_daily_returns(self):
        self.quote('2026-10-23', 10); self.quote('2026-10-29', 11); self.quote('2026-10-30', 12)
        value = self.result()
        self.assertEqual(value['daily']['ratio'], D(12)/11-1)
        self.assertEqual(value['weekly']['ratio'], D('.2'))
        self.assertEqual(value['daily']['from_date'], '2026-10-29')
        self.assertEqual(value['weekly']['from_date'], '2026-10-23')
        self.assertEqual(value['weekly']['to_source'], '合成行情')

    def test_weekend_uses_current_week_but_new_week_waits_for_first_quote(self):
        self.quote('2026-10-23', 10); self.quote('2026-10-30', 12)
        self.assertEqual(self.result('2026-11-01')['weekly']['ratio'], D('.2'))
        monday = self.result('2026-11-02')
        self.assertEqual(monday['weekly']['status'], 'pending')
        self.assertIsNone(monday['weekly']['ratio'])
        self.assertEqual(monday['daily']['ratio'], D('.2'))
        self.quote('2026-11-02', 9)
        self.assertEqual(self.result('2026-11-02')['weekly']['ratio'], D('-.25'))

    def test_holiday_anchor_is_last_quote_before_monday(self):
        self.quote('2026-10-22', 10); self.quote('2026-10-27', 11)
        self.assertEqual(self.result()['weekly']['from_date'], '2026-10-22')
        self.assertEqual(self.result()['weekly']['ratio'], D('.1'))

    def test_missing_and_zero_change_are_distinct(self):
        self.assertEqual(self.result()['daily']['status'], 'missing_quote')
        self.quote('2026-10-29', 10)
        self.assertEqual(self.result()['daily']['status'], 'missing_base')
        self.quote('2026-10-30', 10)
        self.assertEqual(self.result()['daily']['ratio'], D(0))
        self.assertEqual(self.result()['weekly']['status'], 'missing_base')

    def test_future_manual_cancelled_and_other_instrument_quotes_are_excluded(self):
        self.quote('2026-10-23', 10); self.quote('2026-10-30', 12)
        baseline = self.result()
        self.quote('2026-10-30', 900, manual=True)
        self.quote('2026-10-30', 800, cancelled=True)
        self.quote('2026-11-02', 500)
        self.state['prices'].append(dict(instrument_id='other', date='2026-10-30', price='999', kind='raw'))
        self.assertEqual(self.result(), baseline)

    def test_quote_correction_uses_latest_version_without_mutating_ledger(self):
        self.quote('2026-10-23', 10, version_id=1); self.quote('2026-10-30', 12, version_id=2)
        self.quote('2026-10-30', 11, version_id=3)
        before = copy.deepcopy(self.state)
        self.assertEqual(self.result()['weekly']['ratio'], D('.1'))
        self.assertEqual(self.state, before)

    def test_adjusted_series_never_mixes_endpoints_and_requires_latest_session(self):
        self.instrument['market'] = 'SH'
        self.quote('2026-10-23', 10); self.quote('2026-10-30', 6)
        self.quote('2026-10-23', 5, 'adjusted')
        stale = self.result()
        self.assertEqual(stale['basis'], 'adjusted')
        self.assertEqual(stale['daily']['status'], 'missing_quote')
        self.quote('2026-10-30', 6, 'adjusted')
        self.assertEqual(self.result()['weekly']['ratio'], D('.2'))
        self.assertEqual(self.result()['weekly']['from_price'], '5')

    def test_fund_uses_unit_nav_and_no_future_adjusted_series(self):
        self.quote('2026-10-23', 10); self.quote('2026-10-30', 11)
        self.quote('2026-10-23', 50, 'adjusted'); self.quote('2026-10-30', 60, 'adjusted')
        self.assertEqual(self.result()['basis'], 'raw')
        self.assertEqual(self.result()['daily']['ratio'], D('.1'))
        self.instrument['market'] = 'HK'
        self.assertEqual(self.result('2026-10-22')['basis'], 'raw')

    def test_known_raw_split_is_not_reported_as_loss(self):
        self.quote('2026-10-23', 10); self.quote('2026-10-29', 5); self.quote('2026-10-30', 5)
        self.state['corporate_actions'].append(dict(instrument_id='a',kind='split_notice',date='2026-10-28'))
        self.assertEqual(self.result()['daily']['ratio'], D(0))
        self.assertEqual(self.result()['weekly']['status'], 'corporate_action')
        self.state['corporate_actions'] = []
        self.state['events'].append(dict(instrument_id='a',kind='ADJUSTMENT',date='2026-10-28'))
        self.assertIsNone(self.result()['weekly']['ratio'])

    def test_excessive_gap_and_invalid_prices_are_unknown(self):
        self.quote('2026-09-01', 10); self.quote('2026-10-30', 11)
        self.assertEqual(self.result()['daily']['status'], 'missing_base')
        self.assertEqual(self.result()['weekly']['status'], 'missing_base')
        self.quote('2026-10-29', 0)
        self.assertEqual(self.result()['daily']['status'], 'missing_quote')


class QuoteChangeApiTests(unittest.TestCase):
    def test_detail_schema_scope_cashflow_independence_and_midnight_cache(self):
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(temp, testing=True, auto_sync=False)) as c:
            s = c.app.state.service
            p = s.save_portfolio(dict(name='合成行情组合',planned_amount='1000',start_date='2026-10-01'))
            e = s.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2026-10-02',
                instrument=dict(market='FUND',code='000001',name='合成净值基金'),quantity='10',gross_amount='100',fx_rate='1'))
            s.cache_prices(e['instrument_id'],[dict(date='2026-10-23',price='10',source='合成净值'),dict(date='2026-10-30',price='11',source='合成净值')])
            endpoint = '/api/portfolios/'+p['id']+'/instruments/'+e['instrument_id']
            c.get('/api/bootstrap')
            before = s.store.state()
            with patch('backend.service.today', return_value=date(2026,10,30)):
                response = c.get(endpoint); self.assertEqual(response.status_code, 200)
                value = response.json()
                self.assertEqual(value['market_changes']['weekly']['ratio'], '0.1')
                self.assertIn('transactions', value); self.assertIn('holding', value)
            self.assertEqual(s.store.state(), before)
            with patch('backend.service.today', return_value=date(2026,11,2)):
                self.assertEqual(c.get(endpoint).json()['market_changes']['weekly']['status'], 'pending')
            q = s.save_portfolio(dict(name='合成隔离组合',planned_amount='1000',start_date='2026-10-01'))
            self.assertEqual(c.get(endpoint.replace(p['id'],q['id'])).status_code, 422)
            s.save_event(dict(portfolio_id=p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2026-10-26',sell_all=True,gross_amount='105',fx_rate='1'))
            with patch('backend.service.today', return_value=date(2026,10,30)):
                closed = c.get(endpoint).json()
                self.assertTrue(closed['holding']['performance']['closed'])
                self.assertEqual(closed['market_changes'], value['market_changes'])
            schema = c.get('/api/openapi.json').json()['components']['schemas']
            self.assertIn('InstrumentMarketChanges',schema)
            self.assertIn('unallocated_target_amount',schema['PlanFunding']['properties'])
            self.assertIn('instrument_market_changes',c.get('/api/capabilities').json())
