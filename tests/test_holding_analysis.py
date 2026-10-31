"""Synthetic checks for independent labels, complete partitions and legacy recovery."""
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import ConflictError, DomainError
from backend.environment import claim_environment
from backend.holding_analysis import SCOPES
from backend.service import Service
from backend.store import Store


class HoldingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = claim_environment(Path(self.temp.name) / 'test', 'test')
        self.store = Store(self.directory)
        self.s = Service(self.store)
        self.p = self.s.save_portfolio(dict(name='合成标签组合', planned_amount='10000', start_date='2023-01-01'))

    def tearDown(self):
        self.temp.cleanup()

    def buy(self, code='600001', pid=None, market='CN', **extra):
        return self.s.save_event(dict(portfolio_id=pid or self.p['id'], kind='BUY', date='2023-01-02',
                                      instrument=dict(market=market, code=code, name='合成' + code),
                                      quantity='100', gross_amount='1000', fx_rate='1', **extra))

    def put(self, event, **labels):
        return self.s.holding_analysis.put(self.p['id'], event['instrument_id'],
                                           dict(expected_revision=self.store.state()['revision'], **labels))

    def report(self, basis='holdings'):
        return self.s.holding_analysis.report(self.p['id'], basis)

    def test_optional_labels_stock_inference_and_fund_uncertainty_are_read_only(self):
        stock, fund = self.buy(), self.buy('510001')
        before = self.store.state()
        a = self.s.holding_analysis.read(self.p['id'], stock['instrument_id'])
        b = self.s.holding_analysis.read(self.p['id'], fund['instrument_id'])
        self.assertEqual(a['labels']['path'], ['A股'])
        self.assertEqual(a['labels']['scope_source'], 'automatic')
        self.assertEqual(b['labels']['path'], ['待确认'])
        self.assertIn('海外债券', SCOPES); self.assertIn('大宗商品', SCOPES)
        self.assertEqual(self.store.state(), before)
        r = self.report(); self.assertTrue(r['eligible'])
        self.assertEqual(sum(D(n['weight']) for n in r['nodes']), D(1))

    def test_parent_direct_holdings_and_children_partition_exactly(self):
        stock, other = self.buy(), self.buy('600002')
        self.put(stock, direction='行业', detail='医药')
        r = self.report(); root = r['nodes'][0]
        self.assertEqual(root['value'], '2000')
        self.assertEqual({n['name'] for n in root['children']}, {'行业', '本级持仓'})
        self.assertEqual(sum(D(n['value']) for n in root['children']), D(root['value']))
        self.put(other, direction='行业', detail='医药')
        r = self.report(); leaf = r['nodes'][0]['children'][0]['children'][0]
        self.assertEqual(leaf['count'], 2); self.assertEqual(leaf['value'], '2000')
        self.assertEqual(len(r['nodes'][0]['children']), 1)

    def test_labels_do_not_change_plans_events_prices_or_financial_values(self):
        e = self.buy()
        self.s.attach_instrument(self.p['id'], e['instrument_id'], dict(target_pct='20', asset_class='权益'))
        before = self.store.state(); finance = self.s.dashboard(self.p['id'], view='summary')
        self.put(e, scope='大宗商品', direction='贵金属', detail='黄金')
        after = self.store.state(); updated = self.s.dashboard(self.p['id'], view='summary')
        for key in ('portfolios', 'allocations', 'events', 'prices', 'fx'):
            self.assertEqual(before[key], after[key])
        for key in ('total_assets', 'securities_total', 'pnl', 'cash_total', 'capital_summary'):
            self.assertEqual(finance[key], updated[key])
        self.s.attach_instrument(self.p['id'], e['instrument_id'], dict(target_pct='30'))
        self.assertEqual(self.s.holding_analysis.read(self.p['id'], e['instrument_id'])['labels']['path'], ['大宗商品', '贵金属', '黄金'])

    def test_same_security_and_suggestions_are_portfolio_scoped(self):
        e = self.buy(); self.put(e, direction='行业', detail='医药')
        q = self.s.save_portfolio(dict(name='另一合成组合', planned_amount='10000', start_date='2023-01-01'))
        with self.assertRaises(DomainError): self.s.holding_analysis.read(q['id'], e['instrument_id'])
        f = self.buy(pid=q['id']); self.assertEqual(e['instrument_id'], f['instrument_id'])
        r = self.s.holding_analysis.read(q['id'], f['instrument_id'])
        self.assertEqual(r['labels']['path'], ['A股']); self.assertEqual(r['options']['suggestions'], [])
        self.assertEqual(self.report()['nodes'][0]['value'], '1000')

    def test_validation_and_conflict_leave_records_unchanged(self):
        e = self.buy(); revision = self.store.state()['revision']
        self.put(e, scope='海外债券', direction='国债', detail='美国国债')
        before = self.store.state()
        with self.assertRaises(ConflictError):
            self.s.holding_analysis.put(self.p['id'], e['instrument_id'], dict(expected_revision=revision, scope='大宗商品'))
        for labels in (dict(scope='不存在'), dict(scope='大宗商品', direction='红利'), dict(detail='缺方向'), dict(direction='行业', detail='a\nb')):
            with self.assertRaises(DomainError): self.put(e, **labels)
        self.assertEqual(self.store.state(), before)
        self.put(e, scope='', direction='', detail='')
        self.assertEqual(self.s.holding_analysis.read(self.p['id'], e['instrument_id'])['labels']['path'], ['A股'])

    def test_both_bases_keep_unlabelled_holdings_and_system_cash(self):
        e = self.buy('510001'); self.put(e, scope='国内债券', direction='可转债')
        a, b = self.report(), self.report('assets')
        self.assertEqual(a['total'], '1000'); self.assertEqual(b['total'], '10000')
        self.assertEqual(a['nodes'][0]['weight'], '1'); self.assertEqual(b['nodes'][0]['weight'], '0.1')
        self.assertEqual(sum(D(n['value']) for n in b['nodes']), D(b['total']))
        self.assertEqual(next(n for n in b['nodes'] if n['kind'] == 'system')['value'], '9000')

    def test_missing_fx_disables_percentages_without_dropping_the_position(self):
        self.buy('AAA', market='US')
        r = self.report()
        self.assertFalse(r['eligible']); self.assertTrue(r['incomplete']); self.assertIsNone(r['total'])
        self.assertEqual(len(r['holdings']), 1); self.assertIsNone(r['holdings'][0]['weight'])

    def test_initial_buy_labels_preview_then_commit_are_atomic_and_not_event_fields(self):
        payload = dict(portfolio_id=self.p['id'], kind='BUY', date='2023-01-02', instrument=dict(market='CN', code='600003'),
                       quantity='100', gross_amount='1000', fx_rate='1', holding_labels=dict(scope='A股', direction='红利', detail='央企'))
        before = self.store.state(); preview = self.s.preview_event(payload)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(preview['holding_labels']['path'], ['A股', '红利', '央企'])
        event = self.s.save_event(dict(payload, expected_revision=preview['revision']))
        self.assertNotIn('holding_labels', event)
        self.assertEqual(self.s.holding_analysis.read(self.p['id'], event['instrument_id'])['labels']['detail'], '央企')
        invalid = dict(payload, instrument=dict(market='CN', code='600004'), holding_labels=dict(scope='大宗商品', direction='红利'))
        before = self.store.state()
        with self.assertRaises(DomainError): self.s.save_event(dict(invalid, expected_revision=before['revision']))
        self.assertEqual(self.store.state(), before)

    def test_backup_restore_and_schema7_upgrade_preserve_business_records(self):
        e = self.buy(); self.put(e, scope='海外债券', direction='投资级债')
        backup = self.store.backup('synthetic-labels')
        target = Store(Path(self.temp.name) / 'restore')
        target.restore(backup.read_bytes())
        self.assertEqual(target.state()['holding_labels'], self.store.state()['holding_labels'])
        path = Path(self.temp.name) / 'schema7'; old = Store(path)
        before = old.state()
        with sqlite3.connect(old.path) as db:
            db.execute('DROP TABLE holding_labels')
            for table in ('research_heads','research_snapshots','research_indices','research_meta','research_price_chunks','data_update_tasks','data_update_policies','data_update_receipts'):
                db.execute('DROP TABLE IF EXISTS '+table)
            db.execute("UPDATE meta SET value='7' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0006_storage'")
        upgraded = Store(path)
        self.assertEqual(upgraded.state()['holding_labels'], [])
        for key in ('portfolios','events','allocations','prices','fx'):
            self.assertEqual(before[key], upgraded.state()[key])

    def test_later_buy_cannot_attach_initial_labels_to_an_existing_position(self):
        bought = self.buy('600005')
        instrument = self.s.save_instrument(dict(portfolio_id=self.p['id'], market='SH', code='600006', type='stock'))
        opening = self.s.save_event(dict(portfolio_id=self.p['id'], instrument_id=instrument['id'], kind='OPENING_POSITION',
                                         date='2023-01-01', quantity='100', price='10', fx_rate='1'))
        for prior in (bought, opening):
            with self.subTest(kind=prior['kind']):
                before = self.store.state()
                payload = dict(portfolio_id=self.p['id'], instrument_id=prior['instrument_id'], kind='BUY',
                               date='2023-02-02', quantity='10', gross_amount='100', fx_rate='1',
                               holding_labels=dict(scope='A股', direction='红利'))
                with self.assertRaises(DomainError): self.s.preview_event(payload)
                with self.assertRaises(DomainError): self.s.save_event(dict(payload, expected_revision=before['revision']))
                self.assertEqual(self.store.state(), before)

    def test_later_inline_buy_and_portfolio_edits_preserve_saved_labels(self):
        event = self.buy(); self.put(event, scope='大宗商品', direction='贵金属', detail='黄金')
        labels = self.store.state()['holding_labels']
        later = self.s.save_event(dict(portfolio_id=self.p['id'], kind='BUY', date='2023-02-02',
                                      instrument=dict(market='CN', code='600001', name='合成更名'),
                                      quantity='10', gross_amount='100', fx_rate='1'))
        self.s.save_portfolio(dict(self.p, name='合成更名组合'), self.p['id'])
        self.assertEqual(later['instrument_id'], event['instrument_id'])
        self.assertEqual(self.store.state()['holding_labels'], labels)
        self.assertEqual(self.s.holding_analysis.read(self.p['id'], event['instrument_id'])['labels']['path'], ['大宗商品', '贵金属', '黄金'])

    def test_http_contract_revision_guard_and_retired_endpoint(self):
        e = self.buy()
        with TestClient(create_app(self.directory, testing=True, auto_sync=False), base_url='http://127.0.0.1:8876') as client:
            csrf = client.get('/api/bootstrap').json()['csrf_token']
            client.headers.update({'origin':'http://127.0.0.1:8876','x-csrf-token':csrf})
            url = f"/api/portfolios/{self.p['id']}/instruments/{e['instrument_id']}/labels"
            read = client.get(url).json()
            payload = dict(expected_revision=read['revision'], scope='大宗商品', direction='能源', detail='原油')
            self.assertEqual(client.put(url, json=payload).status_code, 200)
            self.assertEqual(client.put(url, json=payload).status_code, 409)
            self.assertEqual(client.put(url, json={'scope':'A股'}).status_code, 422)
            self.assertEqual(client.get('/api/tags').status_code, 404)
            schema = client.get('/api/openapi.json').json()
            self.assertNotIn('/api/tags', schema['paths'])
            self.assertIn('holding_analysis', client.get('/api/capabilities').json())


if __name__ == '__main__': unittest.main()
