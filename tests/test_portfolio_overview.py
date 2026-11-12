from datetime import date, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.portfolio_overview import portfolio_overview


class PortfolioOverviewTests(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.as_of = date(2025, 2, 9)
        self.enterContext(patch('backend.service.today', return_value=self.as_of))
        self.app = create_app(directory, testing=True, auto_sync=False)
        self.service = self.app.state.service
        self.client = self.enterContext(TestClient(self.app))
        self.client.get('/api/bootstrap').raise_for_status()

    def overview(self):
        return self.client.get('/api/portfolios/overview').raise_for_status().json()

    def buy(self, portfolio, at, code, quantity, gross):
        return self.service.save_event({
            'portfolio_id': portfolio['id'], 'kind': 'BUY', 'date': at,
            'instrument': {'market': 'CN', 'code': code},
            'quantity': quantity, 'gross_amount': gross, 'fx_rate': '1',
        })['instrument_id']

    def quotes(self, instrument, rows):
        self.service.cache_prices(instrument, [
            {'date': d, 'price': price, 'source': 'synthetic'} for d, price in rows
        ])

    def test_aggregate_cash_flows_and_common_periods_include_archived(self):
        a = self.service.save_portfolio(dict(name='组合甲', planned_amount='5000', start_date='2024-12-02'))
        b = self.service.save_portfolio(dict(name='组合乙', planned_amount='8000', start_date='2025-02-04'))
        i = self.buy(a, '2024-12-02', '510300', '10', '1000')
        j = self.buy(b, '2025-02-04', '510500', '5', '1000')
        self.quotes(i, [('2024-12-02', '100'), ('2024-12-31', '110'), ('2025-01-31', '130'),
                        ('2025-02-06', '140'), ('2025-02-07', '150')])
        self.quotes(j, [('2025-02-04', '200'), ('2025-02-06', '190'), ('2025-02-07', '190')])
        self.service.save_portfolio(dict(b, archived=True), b['id'])

        before = self.app.state.store.state()
        result = self.overview()
        self.assertEqual(result['revision'], before['revision'])
        self.assertEqual(self.app.state.store.state(), before)
        self.assertEqual(result['running_days'], 70)
        self.assertEqual(result['total_assets'], '13450')
        self.assertEqual(result['market_value'], '2450')
        self.assertEqual(result['cumulative'], {'amount': '450', 'rate': '0.225'})
        self.assertEqual(result['year_to_date']['amount'], '350')
        self.assertEqual(result['year_to_date']['rate'], '0.175')
        self.assertEqual(result['month_to_date']['amount'], '150')
        self.assertEqual(result['month_to_date']['rate'], '0.075')
        self.assertEqual(result['latest_trading_day'], {
            'amount': '100', 'rate': '0.05', 'start_date': '2025-02-07', 'end_date': '2025-02-07', 'estimated': False,
        })
        annual = result['annualized']
        rate = float(annual['rate'])
        self.assertAlmostEqual(-1000 * (1 + rate)**(69/365) - 1000 * (1 + rate)**(5/365) + 2450, 0, places=7)
        self.assertEqual(Decimal(annual['amount']), Decimal(annual['rate']) * 2000)
        for row in result['portfolios']:
            detail = self.service.dashboard(row['id'], view='summary')
            self.assertEqual(row['total_assets'], detail['total_assets'])
            self.assertEqual(row['market_value'], detail['capital_summary']['market_value'])
            self.assertEqual(row['cumulative']['amount'], detail['capital_summary']['profit'])
        self.assertTrue(next(row for row in result['portfolios'] if row['id'] == b['id'])['archived'])

        self.service.save_portfolio(dict(a, planned_amount='6000'), a['id'])
        updated = self.overview()
        self.assertGreater(updated['revision'], result['revision'])
        self.assertEqual(updated['total_assets'], '14450')
        for key in ('market_value', 'annualized', 'cumulative', 'year_to_date', 'month_to_date', 'latest_trading_day'):
            self.assertEqual(updated[key], result[key])

    def test_idle_foreign_cash_does_not_change_market_value_or_returns(self):
        p = self.service.save_portfolio(dict(name='实盘组合', planned_amount='5000', start_date='2025-01-02',
                                             funding_mode='cash'))
        self.service.save_event(dict(portfolio_id=p['id'], kind='DEPOSIT', date='2025-01-02',
                                     currency='CNY', amount='3000', fx_rate='1'))
        i = self.buy(p, '2025-01-02', '510300', '10', '1000')
        self.quotes(i, [('2025-01-02', '100'), ('2025-01-31', '100'), ('2025-02-06', '105'), ('2025-02-07', '110')])
        before = self.overview()
        self.service.save_event(dict(portfolio_id=p['id'], kind='DEPOSIT', date='2025-02-04',
                                     currency='USD', amount='1000', fx_rate='7'))

        missing_cash_fx = self.overview()
        self.assertIsNone(missing_cash_fx['total_assets'])
        self.assertFalse(missing_cash_fx['incomplete'])
        for at, rate in [('2025-02-04', '7'), ('2025-02-06', '7.1'), ('2025-02-07', '7.2')]:
            self.service.save_fx(dict(currency='USD', date=at, rate=rate, mode='daily'))
        with_cash_fx = self.overview()
        self.assertEqual(with_cash_fx['total_assets'], '10300')
        for key in ('market_value', 'cumulative', 'annualized', 'year_to_date', 'month_to_date', 'latest_trading_day'):
            self.assertEqual(missing_cash_fx[key], before[key])
            self.assertEqual(with_cash_fx[key], before[key])
        self.assertEqual(with_cash_fx['latest_trading_day']['amount'], '50')

    def test_realized_sales_and_dividends_are_returns_after_holdings_reach_zero(self):
        p = self.service.save_portfolio(dict(name='清仓组合', planned_amount='5000', start_date='2025-01-02'))
        i = self.buy(p, '2025-01-02', '510300', '10', '1000')
        self.quotes(i, [('2025-01-02', '100'), ('2025-01-31', '100'), ('2025-02-06', '110'), ('2025-02-07', '120')])
        self.service.save_event(dict(portfolio_id=p['id'], kind='DIVIDEND', date='2025-02-07', pay_date='2025-02-10',
                                     instrument_id=i, net_amount='20', fx_rate='1'))
        self.service.save_event(dict(portfolio_id=p['id'], kind='SELL', date='2025-02-07', payment_date='2025-02-10',
                                     instrument_id=i, sell_all=True, gross_amount='1200', fx_rate='1'))

        result = self.overview()
        self.assertEqual(result['market_value'], '0')
        self.assertEqual(result['portfolios'][0]['market_value'], '0')
        self.assertEqual(result['total_assets'], '5220')
        self.assertEqual(result['cumulative'], {'amount': '220', 'rate': '0.22'})
        self.assertEqual(result['month_to_date']['amount'], '220')
        self.assertEqual(result['latest_trading_day']['amount'], '120')
        self.assertTrue(result['latest_trading_day']['estimated'])

    def test_latest_session_flags_missing_close_in_another_portfolio(self):
        with patch('backend.service.today', return_value=date(2026, 11, 12)):
            a = self.service.save_portfolio(dict(name='组合甲', planned_amount='5000', start_date='2026-11-10'))
            b = self.service.save_portfolio(dict(name='组合乙', planned_amount='8000', start_date='2026-11-10'))
            i = self.buy(a, '2026-11-10', '510300', '10', '1000')
            j = self.buy(b, '2026-11-10', '510500', '10', '1000')
            self.quotes(i, [('2026-11-11', '100'), ('2026-11-12', '110')])
            self.quotes(j, [('2026-11-11', '100')])

            result = self.overview()
            self.assertEqual(result['latest_trading_day'], {
                'amount': '100', 'rate': '0.05', 'start_date': '2026-11-12', 'end_date': '2026-11-12', 'estimated': True,
            })
            self.quotes(j, [('2026-11-12', '105')])
            day_return = self.overview()['latest_trading_day']
            self.assertEqual(day_return['amount'], '150')
            self.assertFalse(day_return['estimated'])

    def test_latest_session_flags_missing_previous_close(self):
        with patch('backend.service.today', return_value=date(2026, 11, 12)):
            a = self.service.save_portfolio(dict(name='组合甲', planned_amount='5000', start_date='2026-11-10'))
            i = self.buy(a, '2026-11-10', '510300', '10', '1000')
            self.quotes(i, [('2026-11-10', '100'), ('2026-11-12', '120')])

            daily = self.overview()['latest_trading_day']
            self.assertEqual(daily['amount'], '200')
            self.assertTrue(daily['estimated'])
            self.quotes(i, [('2026-11-11', '110')])
            daily = self.overview()['latest_trading_day']
            self.assertEqual(daily['amount'], '100')
            self.assertFalse(daily['estimated'])

    def test_many_distinct_investment_cash_flow_dates_in_aggregate(self):
        start = self.as_of - timedelta(days=1050)
        projections = []
        for i in range(3):
            buys = [{'date': str(start + timedelta(days=offset)), 'amount': Decimal(-100)}
                    for offset in range(i, 1050, 3)]
            cost = Decimal(35000)
            portfolio = {'id': str(i), 'name': f'组合 {i}', 'start_date': buys[0]['date']}
            snapshot = {
                'total_assets': cost, 'investment_estimated': False,
                'history': [{'date': str(self.as_of), 'investment_estimated': False,
                             'investment_incomplete': False, 'has_investment_quote': True,
                             'pending_close': False}],
            }
            summary = {
                'profit': Decimal(0), 'cost': cost, 'market_value': cost, 'return_on_cost': Decimal(0), 'provisional': False,
                'pending_settlement': False, 'cash_flows': [*buys, {'date': str(self.as_of), 'amount': cost}],
            }
            projections.append((portfolio, snapshot, summary))

        result = portfolio_overview(projections, self.as_of, revision=1, investment_at=lambda *_: self.fail('no prior snapshot'))
        self.assertEqual(result['total_assets'], '105000')
        self.assertEqual(result['annualized']['rate'], '0')
        self.assertEqual(result['annualized']['amount'], '0')

    def test_empty_missing_quote_and_unconfirmed_split_are_distinct(self):
        empty = self.overview()
        self.assertEqual(empty['portfolios'], [])
        self.assertIsNone(empty['total_assets'])
        self.assertIsNone(empty['market_value'])
        self.assertIsNone(empty['running_days'])

        a = self.service.save_portfolio(dict(name='组合甲', planned_amount='5000', start_date='2025-02-03'))
        i = self.buy(a, '2025-02-03', '600001', '10', '1000')
        self.assertIsNone(self.overview()['latest_trading_day']['end_date'])
        self.quotes(i, [('2025-02-07', '50')])
        self.service.cache_actions(i, [dict(kind='split_notice', date='2025-02-07', factor='2', source='synthetic')])

        result = self.overview()
        self.assertTrue(result['incomplete'])
        self.assertIsNone(result['total_assets'])
        self.assertIsNone(result['market_value'])
        for key in ('annualized', 'cumulative', 'year_to_date', 'month_to_date', 'latest_trading_day'):
            self.assertIsNone(result[key]['amount'])
            self.assertIsNone(result[key]['rate'])
        self.assertEqual(result['latest_trading_day']['end_date'], '2025-02-07')
