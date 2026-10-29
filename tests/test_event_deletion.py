"""Synthetic causal deletion, immutable previews and accounting guard regressions."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import DomainError, ConflictError
from backend.service import Service
from backend.store import Store


class EventDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(Store(Path(self.tmp.name)))
        self.p = self.s.save_portfolio(dict(name='合成删除验收', start_date='2024-01-01', planned_amount='100000'))
        self.a = self.buy('2024-01-02')

    def tearDown(self): self.tmp.cleanup()

    def buy(self, date, **kw):
        return self.s.save_event(dict(dict(portfolio_id=self.p['id'], kind='BUY', date=date,
            instrument=dict(market='CN', code='600000', name='合成股票'), quantity='100', gross_amount='1000', fx_rate='1'), **kw))

    def event(self, kind, date, **kw):
        return self.s.save_event(dict(portfolio_id=self.p['id'], instrument_id=self.a['instrument_id'],
            kind=kind, date=date, fx_rate='1', **kw))

    def remove(self, root):
        plan = self.s.preview_deletion(root['id'])
        result = self.s.delete_event(root['id'], '合成重复记录清理', plan['revision'], cascade=True, event_ids=plan['event_ids'])
        self.assertEqual(result['deleted_event_ids'], plan['event_ids'])
        return plan

    def test_recursive_reinvestment_split_sales_fees_and_audit(self):
        div = self.event('REINVEST', '2024-02-01', gross_amount='100', net_amount='100',
                         pay_date='2024-02-01', reinvest_date='2024-02-01', reinvest_quantity='10', reinvest_gross_amount='100')
        split = self.event('SPLIT', '2024-03-01', factor='2')
        sale = self.event('SELL', '2024-04-01', sell_lots=[dict(buy_event_id=div['id'], quantity='20')], gross_amount='150')
        fee = self.event('TAX', '2024-04-02', amount='5', related_event_id=sale['id'])
        later = self.buy('2024-05-01')
        before = self.s.store.state()
        plan = self.s.preview_deletion(self.a['id'])
        self.assertEqual(self.s.store.state(), before)
        self.assertEqual(set(plan['event_ids']), {self.a['id'], div['id'], split['id'], sale['id'], fee['id']})
        self.assertTrue(plan['can_delete'])
        self.remove(self.a)
        self.assertEqual([e['id'] for e in self.s.store.state()['events']], [later['id']])
        for eid in plan['event_ids']:
            self.assertTrue(self.s.transaction_detail(self.p['id'], eid)['cancelled'])
        with self.s.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events WHERE deleted=1').fetchone()[0], 5)

    def test_shared_records_are_whole_other_parents_and_portfolios_preserved(self):
        b = self.buy('2024-01-03')
        div = self.event('DIVIDEND', '2024-02-01', net_amount='100', pay_date='2024-02-01')
        split = self.event('SPLIT', '2024-03-01', factor='2')
        sale = self.event('SELL', '2024-04-01', sell_lots=[dict(buy_event_id=b['id'], quantity='20')], gross_amount='150')
        q = self.s.save_portfolio(dict(name='合成隔离组合', start_date='2024-01-01', planned_amount='10000'))
        other = self.buy('2024-01-04', portfolio_id=q['id'])
        plan = self.remove(self.a)
        self.assertEqual(set(plan['event_ids']), {self.a['id'], div['id'], split['id'], sale['id']})
        self.assertTrue(all(item['shared_with_other_lots'] for item in plan['items'] if not item['is_root']))
        self.assertEqual({e['id'] for e in self.s.store.state()['events']}, {b['id'], other['id']})
        self.assertEqual(next(e for e in self.s.store.state()['events'] if e['id'] == b['id']), b)

    def test_sale_deletion_keeps_buy_and_earlier_dividend(self):
        div = self.event('DIVIDEND', '2024-02-01', net_amount='100')
        sale = self.event('SELL', '2024-03-01', sell_lots=[dict(buy_event_id=self.a['id'], quantity='100')], gross_amount='1100')
        plan = self.remove(sale)
        self.assertEqual(plan['event_ids'], [sale['id']])
        self.assertFalse(plan['items'][0]['shared_with_other_lots'])
        self.assertEqual({e['id'] for e in self.s.store.state()['events']}, {self.a['id'], div['id']})

    def test_registry_date_excludes_later_purchase_and_sold_lots(self):
        sale = self.event('SELL', '2024-02-01', sell_lots=[dict(buy_event_id=self.a['id'], quantity='100')], gross_amount='1100')
        b = self.buy('2024-02-02')
        div = self.event('DIVIDEND', '2024-02-03', record_date='2024-01-31', net_amount='100')
        self.assertEqual(self.s.preview_deletion(b['id'])['event_ids'], [b['id']])
        self.assertEqual(set(self.s.preview_deletion(self.a['id'])['event_ids']), {self.a['id'], sale['id'], div['id']})

    def test_legacy_fifo_and_later_unrelated_batch(self):
        payload = dict(portfolio_id=self.p['id'], instrument_id=self.a['instrument_id'], kind='SELL',
                       date='2024-02-01', quantity='100', gross_amount='1100', fx_rate='1')
        from backend.ledger import normalize_event
        from backend.common import json_dumps
        inst = next(i for i in self.s.store.state()['instruments'] if i['id'] == self.a['instrument_id'])
        event = normalize_event(payload, self.p, {inst['id']: inst})
        event.update(id='synthetic-legacy-sale', sequence=1)
        with self.s.store.transaction() as db:
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,0)', (event['id'], self.p['id'], event['date'], 1, 'synthetic', json_dumps(event)))
        later = self.buy('2024-03-01')
        plan = self.remove(self.a)
        self.assertEqual(set(plan['event_ids']), {self.a['id'], event['id']})
        self.assertEqual([e['id'] for e in self.s.store.state()['events']], [later['id']])

    def test_guards_no_side_effect_and_cash_shortfall(self):
        div = self.event('DIVIDEND', '2024-02-01', net_amount='100')
        plan = self.s.preview_deletion(self.a['id'])
        before = self.s.store.state()
        for ids in ([self.a['id']], plan['event_ids'] + ['unreviewed'], plan['event_ids'] * 2):
            with self.assertRaises(DomainError):
                self.s.delete_event(self.a['id'], '合成', plan['revision'], cascade=True, event_ids=ids)
        with self.assertRaises(DomainError):
            self.s.delete_event(self.a['id'], '合成', cascade=True, event_ids=plan['event_ids'])
        self.assertEqual(self.s.store.state(), before)
        self.buy('2024-03-01')
        with self.assertRaises(ConflictError):
            self.s.delete_event(self.a['id'], '合成', plan['revision'], cascade=True, event_ids=plan['event_ids'])
        # A dividend can fund an independent purchase: never delete that purchase just to pass validation.
        p = self.s.save_portfolio(dict(name='合成资金校验', start_date='2024-01-01', planned_amount='1000'))
        a = self.buy('2024-01-02', portfolio_id=p['id'])
        d = self.s.save_event(dict(portfolio_id=p['id'], instrument_id=a['instrument_id'], kind='DIVIDEND', date='2024-02-01', net_amount='100', fx_rate='1'))
        self.s.save_event(dict(portfolio_id=p['id'], instrument_id=a['instrument_id'], kind='BUY', date='2024-03-01', quantity='10', gross_amount='100', fx_rate='1'))
        plan = self.s.preview_deletion(d['id'])
        self.assertFalse(plan['can_delete'])
        self.assertEqual(plan['event_ids'], [d['id']])
        before = self.s.store.state()
        with self.assertRaises(DomainError): self.s.delete_event(d['id'], '合成', plan['revision'], cascade=True, event_ids=plan['event_ids'])
        self.assertEqual(self.s.store.state(), before)

    def test_http_contract_preview_conflict_and_readback(self):
        with TestClient(create_app(self.tmp.name, testing=True, auto_sync=False)) as c:
            csrf = {'x-csrf-token': c.get('/api/bootstrap').json()['csrf_token']}
            path = '/api/events/' + self.a['id']
            before = self.s.store.state()
            preview = c.get(path + '/deletion-preview')
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(self.s.store.state(), before)
            plan = preview.json()
            body = dict(reason='合成API删除', cascade=True, event_ids=plan['event_ids'], expected_revision=plan['revision'])
            self.assertEqual(c.request('DELETE', path, json=dict(body, event_ids=[]), headers=csrf).status_code, 422)
            self.buy('2024-03-01')
            self.assertEqual(c.request('DELETE', path, json=body, headers=csrf).status_code, 409)
            plan = c.get(path + '/deletion-preview').json()
            response = c.request('DELETE', path, json=dict(body, expected_revision=plan['revision'], event_ids=plan['event_ids']), headers=csrf)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['deleted_event_ids'], plan['event_ids'])
            self.assertTrue(c.get('/api/portfolios/'+self.p['id']+'/transactions/'+self.a['id']).json()['cancelled'])

    def test_same_day_split_does_not_claim_the_new_purchase(self):
        split = self.event('SPLIT', '2024-02-01', factor='2')
        b = self.buy('2024-02-01')
        sale = self.event('SELL', '2024-02-02', sell_lots=[dict(buy_event_id=b['id'], quantity='100')], gross_amount='1100')
        plan = self.remove(self.a)
        self.assertEqual(set(plan['event_ids']), {self.a['id'], split['id']})
        self.assertEqual({e['id'] for e in self.s.store.state()['events']}, {b['id'], sale['id']})

    def test_fund_entitlement_waits_for_confirmation_and_hk_ignores_book_closure(self):
        fund = self.s.save_event(dict(portfolio_id=self.p['id'], kind='BUY', date='2024-01-02',
            instrument=dict(market='FUND', code='001257'), quantity='100', gross_amount='100',
            confirmation_date='2024-01-05', fx_rate='1'))
        early = self.s.save_event(dict(portfolio_id=self.p['id'], instrument_id=fund['instrument_id'],
            kind='DIVIDEND', date='2024-01-04', net_amount='10', fx_rate='1'))
        later = self.s.save_event(dict(portfolio_id=self.p['id'], instrument_id=fund['instrument_id'],
            kind='DIVIDEND', date='2024-01-06', net_amount='10', fx_rate='1'))
        self.assertEqual(set(self.s.preview_deletion(fund['id'])['event_ids']), {fund['id'], later['id']})
        hk = self.s.save_event(dict(portfolio_id=self.p['id'], kind='BUY', date='2024-02-02',
            instrument=dict(market='HK', code='00700'), quantity='100', gross_amount='1000', fx_rate='.9'))
        div = self.s.save_event(dict(portfolio_id=self.p['id'], instrument_id=hk['instrument_id'], kind='DIVIDEND',
            date='2024-02-03', record_date='2024-02-10', net_amount='100', fx_rate='.9'))
        self.assertEqual(set(self.s.preview_deletion(hk['id'])['event_ids']), {hk['id'], div['id']})

    def test_transfer_pair_and_transaction_failure_are_atomic(self):
        # Historical transfer records remain supported in budget-mode accounts.
        from backend.common import json_dumps
        q = self.s.save_portfolio(dict(name='合成转移对', start_date='2024-01-01', planned_amount='10000'))
        with self.s.store.transaction() as db:
            for n, (pid, kind) in enumerate([(self.p['id'], 'WITHDRAW'), (q['id'], 'DEPOSIT')]):
                event = dict(id='synthetic-transfer-'+str(n), portfolio_id=pid, kind=kind, date='2024-01-02',
                    sequence=10, amount='100', currency='CNY', fx_rate='1', fee='0', tax='0', transfer_id='synthetic-pair')
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,0)', (event['id'], pid, event['date'], 10, event['id'], json_dumps(event)))
        plan = self.s.preview_deletion('synthetic-transfer-0')
        self.assertEqual(plan['event_ids'], ['synthetic-transfer-0', 'synthetic-transfer-1'])
        from unittest.mock import patch
        before = self.s.store.state()
        original = self.s.store.audit
        calls = []
        def failing_audit(*args):
            calls.append(1)
            if len(calls) == 2: raise DomainError('合成事务中断')
            return original(*args)
        with patch.object(self.s.store, 'audit', side_effect=failing_audit):
            with self.assertRaises(DomainError):
                self.s.delete_event(plan['event_id'], '合成', plan['revision'], cascade=True, event_ids=plan['event_ids'])
        self.assertEqual(self.s.store.state(), before)
        self.remove({'id':plan['event_id']})

    def test_curve_quality_pending_split_historical_gap_and_clean_range(self):
        iid = self.a['instrument_id']
        self.s.cache_prices(iid, [dict(date='2024-01-02', price='10', source='synthetic')])
        self.s.cache_actions(iid, [dict(kind='split_notice', date='2024-03-01', factor='2', source='synthetic')])
        dash = self.s.dashboard(self.p['id'], end='2024-04-01')
        quality = dash['curve_quality']
        self.assertGreater(quality['missing_days'], 0)
        self.assertEqual(quality['from'], '2024-03-01')
        self.assertTrue(any('待确认送转' in r['message'] for r in quality['reasons']))
        self.assertEqual(self.s.dashboard(self.p['id'], end='2024-02-01')['curve_quality']['missing_days'], 0)
        self.event('SPLIT', '2024-03-01', factor='2')
        self.assertEqual(self.s.dashboard(self.p['id'], end='2024-04-01')['curve_quality']['missing_days'], 0)

    def test_curve_explains_historical_gap_even_after_latest_valuation_recovers(self):
        foreign = self.s.save_event(dict(portfolio_id=self.p['id'], kind='BUY', date='2024-01-02',
            instrument=dict(market='US', code='VTI'), quantity='1', gross_amount='100', fx_rate='7'))
        self.s.cache_prices(foreign['instrument_id'], [dict(date='2024-01-02', price='100', source='synthetic')])
        self.s.save_fx(dict(currency='USD', date='2024-01-10', rate='7', mode='daily'))
        dash = self.s.dashboard(self.p['id'], end='2024-01-20')
        self.assertFalse(dash['incomplete'])
        self.assertGreater(dash['curve_quality']['missing_days'], 0)
        self.assertTrue(any('USD' in r['message'] for r in dash['curve_quality']['reasons']))
        self.assertEqual(self.s.dashboard(self.p['id'], start='2024-01-11', end='2024-01-20')['curve_quality']['missing_days'], 0)


if __name__ == '__main__': unittest.main()
