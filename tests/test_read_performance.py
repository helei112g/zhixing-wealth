"""Synthetic-only checks for read reuse, invalidation and unchanged accounting."""
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import DomainError, wire
from backend.environment import claim_environment
from backend.family_service import FamilyService
from backend.ledger import Ledger, Market
from backend.service import Service
from backend.store import Store, READ_CACHE_LIMIT, cached_query


class ReadPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = claim_environment(Path(self.temp.name) / 'synthetic', 'test')
        self.store = Store(self.directory)
        self.service = Service(self.store)
        self.family = FamilyService(self.store)
        self.p = self.service.save_portfolio(dict(name='合成查询组合', start_date='2026-01-01', planned_amount='100000'))
        self.buy = self.service.save_event(dict(portfolio_id=self.p['id'], kind='BUY', date='2026-01-02',
            instrument=dict(market='US', code='SYNTH', name='合成海外标的'), quantity='10', gross_amount='100', fx_rate='7'))
        self.iid = self.buy['instrument_id']
        self.service.cache_prices(self.iid, [dict(date='2026-01-02', price='12', source='synthetic')])
        self.service.cache_fx([dict(currency='USD', date='2026-01-02', rate='7', source='synthetic')])

    def tearDown(self):
        self.temp.cleanup()

    def test_warm_queries_skip_full_state_and_keep_private_return_values(self):
        first = self.service.dashboard(self.p['id'], view='summary')
        expected = first['capital_summary']['market_value']
        first['capital_summary']['market_value'] = 'caller edit'
        first['holdings'].clear()
        with patch.object(self.store, 'state', side_effect=AssertionError('warm read loaded the full ledger')):
            second = self.service.dashboard(portfolio_id=self.p['id'], view='summary', grain='month')
        self.assertEqual(second['capital_summary']['market_value'], expected)
        self.assertEqual(len(second['holdings']), 1)

    def test_summary_omits_only_history_projections_and_keeps_financial_values(self):
        full = self.service.dashboard(self.p['id'])
        summary = self.service.dashboard(self.p['id'], view='summary')
        self.assertTrue({'curve', 'calendar', 'benchmark', 'curve_quality', 'periods'} <= full.keys())
        self.assertTrue({'curve', 'calendar', 'benchmark', 'curve_quality', 'periods'}.isdisjoint(summary))
        for key, value in summary.items():
            self.assertEqual(value, full[key], key)
        with self.assertRaises(DomainError):
            self.service.dashboard(self.p['id'], view='unrecognized')

    def test_price_fx_and_allocation_writes_invalidate_cached_values(self):
        before = self.service.dashboard(self.p['id'], view='summary')
        self.service.cache_prices(self.iid, [dict(date='2026-01-02', price='15', source='synthetic-correction')])
        priced = self.service.dashboard(self.p['id'], view='summary')
        self.assertNotEqual(before['capital_summary']['market_value'], priced['capital_summary']['market_value'])
        self.service.cache_fx([dict(currency='USD', date='2026-01-02', rate='8', source='synthetic-correction')])
        fx = self.service.dashboard(self.p['id'], view='summary')
        self.assertNotEqual(priced['capital_summary']['market_value'], fx['capital_summary']['market_value'])
        with self.store.transaction() as db:
            row = dict(self.store.state(db)['allocations'][0], target_pct='25')
            from backend.common import json_dumps
            db.execute('UPDATE allocations SET data=? WHERE portfolio_id=? AND instrument_id=?',
                       (json_dumps(row), self.p['id'], self.iid))
        updated = self.service.dashboard(self.p['id'], view='summary')
        self.assertEqual(updated['holdings'][0]['target_pct'], '25')

    def test_cache_invalidates_for_another_store_writer(self):
        before = self.service.bootstrap()
        other = Service(Store(self.directory))
        other.save_portfolio(dict(self.p, name='合成已修改组合'), self.p['id'])
        after = self.service.bootstrap()
        self.assertGreater(after['revision'], before['revision'])
        self.assertEqual(after['portfolios'][0]['name'], '合成已修改组合')

    def test_dates_portfolios_ranges_and_pages_are_separate_cache_keys(self):
        a = self.service.dashboard(self.p['id'], end='2026-01-02')
        b = self.service.dashboard(self.p['id'], end='2026-01-03', start='2026-01-03')
        self.assertNotEqual(a['date'], b['date'])
        self.assertEqual(b['curve'][0]['date'], '2026-01-03')
        p2 = self.service.save_portfolio(dict(name='合成独立组合', start_date='2026-01-01', planned_amount='100'))
        self.assertEqual(self.service.dashboard(p2['id'], view='summary')['holdings'], [])
        self.service.save_event(dict(portfolio_id=self.p['id'], instrument_id=self.iid, kind='BUY',
                                    date='2026-01-03', quantity='1', gross_amount='10', fx_rate='7'))
        first = self.service.transaction_page(self.p['id'], page=1, page_size=1)
        second = self.service.transaction_page(self.p['id'], page=2, page_size=1)
        self.assertNotEqual(first['items'][0]['event']['id'], second['items'][0]['event']['id'])
        self.assertEqual((first['page'], second['page']), (1, 2))

    def test_midnight_changes_query_clock_and_bootstrap_running_days(self):
        with patch('backend.service.today', return_value=date(2026, 1, 2)):
            first = self.service.bootstrap()
        with patch('backend.service.today', return_value=date(2026, 1, 3)):
            second = self.service.bootstrap()
        self.assertEqual(first['portfolios'][0]['running_days'], 2)
        self.assertEqual(second['portfolios'][0]['running_days'], 3)
        self.assertNotEqual(first['today'], second['today'])

    def test_job_and_sync_state_are_fresh_without_a_revision_change(self):
        before = self.service.bootstrap()
        self.service.syncing = True
        self.store.save_job(dict(id='synthetic-job', status='running', started_at='2026-01-02T00:00:00+08:00'))
        after = self.service.bootstrap()
        self.assertEqual(before['revision'], after['revision'])
        self.assertTrue(after['syncing'])
        self.assertEqual(after['jobs'][0]['status'], 'running')

    def test_restore_invalidates_all_previous_projection_copies(self):
        original = self.service.dashboard(self.p['id'], view='summary')
        archive = self.service.backup_download()
        self.service.cache_prices(self.iid, [dict(date='2026-01-02', price='18', source='synthetic-correction')])
        changed = self.service.dashboard(self.p['id'], view='summary')
        revision = self.service.bootstrap()['revision']
        self.assertNotEqual(original['capital_summary'], changed['capital_summary'])
        self.service.restore(archive)
        restored = self.service.dashboard(self.p['id'], view='summary')
        self.assertEqual(restored['capital_summary'], original['capital_summary'])
        self.assertGreater(self.service.bootstrap()['revision'], revision)

    def test_note_counts_refresh_without_caching_note_text(self):
        self.assertEqual(self.service.instrument_detail(self.p['id'], self.iid)['note_count'], 0)
        body = '合成正文不进入投影缓存'
        note = self.service.notes.save(self.p['id'], self.iid, dict(title='合成标题', body=body,
                                      expected_revision=self.store.state()['revision']))
        self.assertEqual(self.service.instrument_detail(self.p['id'], self.iid)['note_count'], 1)
        self.assertNotIn(body, repr(self.store.cache))
        self.service.notes.set_deleted(self.p['id'], self.iid, note['id'], deleted=True,
                                       expected_revision=self.store.state()['revision'])
        self.assertEqual(self.service.instrument_detail(self.p['id'], self.iid)['note_count'], 0)

    def test_concurrent_write_does_not_publish_an_old_projection(self):
        ready, release = threading.Event(), threading.Event()
        result, errors = [], []
        def slow_load():
            state = self.store.state()
            ready.set()
            self.assertTrue(release.wait(5))
            return state['revision']
        def read():
            try: result.append(self.store.cached_read(('racing-read',), slow_load))
            except BaseException as e: errors.append(e)
        thread = threading.Thread(target=read)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            old_revision = self.store.state()['revision']
            self.service.save_portfolio(dict(self.p, name='合成并发修改'), self.p['id'])
        finally:
            release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [old_revision])
        fresh = self.store.cached_read(('racing-read',), lambda: self.store.state()['revision'])
        self.assertGreater(fresh, old_revision)

    def test_lru_limit_failures_type_validation_and_directory_isolation(self):
        for n in range(READ_CACHE_LIMIT + 5): self.store.cached_read(('bounded', n), lambda: n)
        self.assertEqual(len(self.store.cache['read-responses']), READ_CACHE_LIMIT)
        calls = []
        def failed():
            calls.append(1); raise DomainError('合成错误')
        for _ in range(2):
            with self.assertRaises(DomainError): self.store.cached_read(('failure',), failed)
        self.assertEqual(len(calls), 2)
        class Probe:
            def __init__(self, store): self.store = store
            @cached_query
            def read(self, number):
                if isinstance(number, bool): raise DomainError('bool is not an integer page')
                return number
        probe = Probe(self.store)
        self.assertEqual(probe.read(1), 1)
        with self.assertRaises(DomainError): probe.read(True)
        other = Store(claim_environment(Path(self.temp.name) / 'other-synthetic', 'test'))
        self.store.cached_read(('same-key',), lambda: 'first')
        self.assertEqual(other.cached_read(('same-key',), lambda: 'second'), 'second')

    def test_family_summary_builds_one_market_and_matches_uncached_snapshots(self):
        self.family.command('link', dict(portfolio_id=self.p['id'], effective_on='2026-01-02', share='1'),
                            self.store.state()['revision'])
        self.service.cache_actions(self.iid, [dict(kind='split_notice', date='2026-02-01', factor='2', source='synthetic')])
        with patch('backend.family_summary.Market', wraps=Market) as shared, patch('backend.ledger.Market', wraps=Market) as nested:
            value = self.family.summary('month', '2026-02')
        self.assertEqual(shared.call_count, 1)
        self.assertEqual(nested.call_count, 0)
        self.assertIsNone(value['snapshot']['total_assets'])  # Pending splits remain unknown.
        state = self.store.state()
        market = Market(state)
        for at in ('2026-01-02', '2026-01-31', '2026-02-01'):
            independent = Ledger(state, self.p)
            reused = Ledger(state, self.p, market=market)
            self.assertEqual(wire(independent.run(at)), wire(reused.run(at)))
        with patch.object(self.store, 'state', side_effect=AssertionError('warm family read loaded state')):
            self.assertEqual(self.family.summary('month', '2026-02'), value)
        self.service.save_event(dict(portfolio_id=self.p['id'], instrument_id=self.iid, kind='SPLIT',
            date='2026-02-01', factor='2', fx_rate='7'))
        self.assertIsNotNone(self.family.summary('month', '2026-02')['snapshot']['total_assets'])

    def test_http_summary_contract_and_write_readback(self):
        app = create_app(self.directory, testing=True, auto_sync=False)
        client = TestClient(app)
        boot = client.get('/api/bootstrap').json()
        url = '/api/portfolios/' + self.p['id'] + '/dashboard'
        summary = client.get(url + '?view=summary').json()
        full = client.get(url).json()
        self.assertEqual(summary['capital_summary'], full['capital_summary'])
        self.assertNotIn('calendar', summary)
        updated = client.put('/api/portfolios/' + self.p['id'], json=dict(self.p,
            planned_amount='120000', expected_revision=boot['revision']), headers={'X-CSRF-Token': boot['csrf_token']})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertNotEqual(summary['portfolio']['planned_amount'], client.get(url + '?view=summary').json()['portfolio']['planned_amount'])

    def test_revision_poll_reports_midnight_without_a_business_write(self):
        client = TestClient(create_app(self.directory, testing=True, auto_sync=False))
        with patch('backend.family_api.today', return_value=date(2026, 1, 2)):
            before = client.get('/api/family/revision').json()
        with patch('backend.family_api.today', return_value=date(2026, 1, 3)):
            after = client.get('/api/family/revision').json()
        self.assertEqual(before['revision'], after['revision'])
        self.assertEqual((before['today'], after['today']), ('2026-01-02', '2026-01-03'))


if __name__ == '__main__': unittest.main()
