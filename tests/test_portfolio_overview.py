from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app


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
        self.quotes(j, [('2025-02-04', '200'), ('2025-02-06', '190')])
        self.service.save_portfolio(dict(b, archived=True), b['id'])

        before = self.app.state.store.state()
        result = self.overview()
        self.assertEqual(result['revision'], before['revision'])
        self.assertEqual(self.app.state.store.state(), before)
        self.assertEqual(result['running_days'], 70)
        self.assertEqual(result['total_assets'], '13450')
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
            self.assertEqual(row['cumulative']['amount'], detail['capital_summary']['profit'])
        self.assertTrue(next(row for row in result['portfolios'] if row['id'] == b['id'])['archived'])

        self.service.save_portfolio(dict(a, planned_amount='6000'), a['id'])
        updated = self.overview()
        self.assertGreater(updated['revision'], result['revision'])
        self.assertEqual(updated['total_assets'], '14450')
        for key in ('annualized', 'cumulative', 'year_to_date', 'month_to_date', 'latest_trading_day'):
            self.assertEqual(updated[key], result[key])

    def test_empty_missing_quote_and_unconfirmed_split_are_distinct(self):
        empty = self.overview()
        self.assertEqual(empty['portfolios'], [])
        self.assertIsNone(empty['total_assets'])
        self.assertIsNone(empty['running_days'])

        a = self.service.save_portfolio(dict(name='组合甲', planned_amount='5000', start_date='2025-02-03'))
        i = self.buy(a, '2025-02-03', '600001', '10', '1000')
        self.assertIsNone(self.overview()['latest_trading_day']['end_date'])
        self.quotes(i, [('2025-02-07', '50')])
        self.service.cache_actions(i, [dict(kind='split_notice', date='2025-02-07', factor='2', source='synthetic')])

        result = self.overview()
        self.assertTrue(result['incomplete'])
        self.assertIsNone(result['total_assets'])
        for key in ('annualized', 'cumulative', 'year_to_date', 'month_to_date', 'latest_trading_day'):
            self.assertIsNone(result[key]['amount'])
            self.assertIsNone(result[key]['rate'])
        self.assertEqual(result['latest_trading_day']['end_date'], '2025-02-07')
