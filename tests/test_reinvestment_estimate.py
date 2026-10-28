import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import json_dumps


class ReinvestmentEstimateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app = create_app(Path(self.temp.name) / 'synthetic', testing=True, auto_sync=False)
        self.s = app.state.service
        self.c = TestClient(app).__enter__()
        self.p = self.s.save_portfolio({'name': '合成再投验证', 'start_date': '2024-01-01', 'planned_amount': '10000'})
        self.buy = self.s.save_event({'portfolio_id': self.p['id'], 'kind': 'BUY', 'date': '2024-01-02',
            'instrument': {'market': 'FUND', 'code': '000001', 'name': '合成基金'},
            'quantity': '100', 'gross_amount': '100', 'fx_rate': '1'})
        self.i = self.buy['instrument_id']
        self.path = f"/api/portfolios/{self.p['id']}/instruments/{self.i}/reinvestment-estimate"
        self.s.cache_prices(self.i, [dict(date='2024-06-03', price='1.25', kind='raw', source='合成单位净值'),
                                    dict(date='2024-06-04', price='1.5', kind='raw', source='合成单位净值')])

    def tearDown(self):
        self.c.__exit__(None, None, None)
        self.temp.cleanup()

    def estimate(self, date='2024-06-03', gross='10'):
        response = self.c.get(self.path, params={'date': date, 'gross_amount': gross})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_exact_day_decimal_rounding_and_no_side_effects(self):
        before = self.s.store.state()
        value = self.estimate()
        self.assertEqual(value['quantity'], '8')
        self.assertEqual(value['reference'], {'date': '2024-06-03', 'nav': '1.25', 'source': '合成单位净值'})
        self.assertEqual(value['currency'], 'CNY')
        self.assertEqual(value['revision'], before['revision'])
        self.assertEqual(self.estimate('2024-06-04')['quantity'], '6.67')
        self.assertEqual(self.estimate(gross='0.09375')['quantity'], '0.08')
        self.assertEqual(self.estimate(gross='0.1')['quantity'], '0.08')
        self.assertEqual(self.s.store.state(), before)

    def test_never_borrows_neighboring_or_adjusted_or_manual_quotes(self):
        self.s.cache_prices(self.i, [dict(date='2024-06-05', price='8', kind='adjusted', source='合成复权'),
                                    dict(date='2024-06-06', price='9', kind='total_return', source='合成全收益')])
        with self.s.store.transaction() as db:
            manual = dict(instrument_id=self.i, date='2024-06-05', price='99', kind='raw', manual=True, source='历史人工')
            db.execute('INSERT INTO prices(instrument_id,date,kind,manual,data) VALUES (?,?,?,?,?)',
                       (self.i, manual['date'], 'raw', 1, json_dumps(manual)))
        for date in ('2024-06-02', '2024-06-05', '2024-06-06', '2024-06-07'):
            value = self.estimate(date)
            self.assertEqual(value['status'], 'missing_nav')
            self.assertIsNone(value['quantity'])
            self.assertIsNone(value['reference'])

    def test_latest_correction_and_too_small_quantity(self):
        self.s.cache_prices(self.i, [dict(date='2024-06-03', price='2', kind='raw', source='合成源修正')])
        self.assertEqual(self.estimate()['quantity'], '5')
        value = self.estimate(gross='0.001')
        self.assertEqual(value['status'], 'rounded_zero')
        self.assertIsNone(value['quantity'])

    def test_scope_inputs_and_nonfund_market(self):
        other = self.s.save_portfolio({'name': '合成隔离', 'start_date': '2024-01-01', 'planned_amount': '10000'})
        for path in (self.path.replace(self.p['id'], other['id']), self.path.replace(self.i, 'missing')):
            self.assertEqual(self.c.get(path, params={'date': '2024-06-03', 'gross_amount': '10'}).status_code, 422)
        for date, gross in [('2999-01-01', '10'), ('invalid', '10'), ('2024-06-03', '0'),
                            ('2024-06-03', '-1'), ('2024-06-03', 'NaN'), ('2024-06-03', 'Infinity')]:
            self.assertEqual(self.c.get(self.path, params={'date': date, 'gross_amount': gross}).status_code, 422)
        stock = self.s.save_event({'portfolio_id': self.p['id'], 'kind': 'BUY', 'date': '2024-01-02',
            'instrument': {'market': 'CN', 'code': '510300'}, 'quantity': '10', 'gross_amount': '100', 'fx_rate': '1'})
        self.s.cache_prices(stock['instrument_id'], [dict(date='2024-06-03', price='5', kind='raw', source='合成收盘价')])
        value = self.c.get(self.path.replace(self.i, stock['instrument_id']), params={'date': '2024-06-03', 'gross_amount': '10'}).json()
        self.assertEqual(value['status'], 'unsupported_market')
        self.assertIsNone(value['quantity'])

    def test_discovery_preview_save_and_manual_override(self):
        cap = self.c.get('/api/capabilities').json()['reinvestment_estimate']
        self.assertTrue(cap['read_only'])
        spec = self.c.get('/api/openapi.json').json()
        self.assertIn(cap['endpoint'], spec['paths'])
        self.assertIn('ReinvestmentEstimate', spec['components']['schemas'])
        value = self.estimate()
        payload = dict(portfolio_id=self.p['id'], instrument_id=self.i, kind='REINVEST', date='2024-06-03',
                       pay_date='2024-06-03', reinvest_date='2024-06-03', gross_amount='12', tax='1',
                       reinvest_gross_amount='10', fee='1', reinvest_quantity=value['quantity'], fx_rate='1')
        before = self.s.store.state()
        preview = self.s.preview_event(payload)
        self.assertEqual(self.s.store.state(), before)
        self.assertEqual(preview['event']['reinvest_quantity'], '8')
        saved = self.s.save_event(dict(payload, expected_revision=preview['revision']))
        self.assertEqual(saved['net_amount'], '11')
        self.assertEqual(saved['reinvest_price'], '1.25')
        manual = dict(saved, reinvest_quantity='7.99', change_reason='按合成确认单补正')
        corrected = self.s.save_event(manual, saved['id'])
        self.assertEqual(corrected['reinvest_quantity'], '7.99')
        self.estimate()
        self.assertEqual(next(e for e in self.s.store.state()['events'] if e['id'] == saved['id'])['reinvest_quantity'], '7.99')


if __name__ == '__main__':
    unittest.main()
