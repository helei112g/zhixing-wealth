"""Synthetic histories: confirmations precede backfilled trades in entry order."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import DomainError
from backend.service import Service
from backend.store import Store


class SplitBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(Store(Path(self.tmp.name)))
        self.p = self.s.save_portfolio(dict(name='合成补录送转', start_date='2021-01-01', planned_amount='100000'))
        self.a = self.event('BUY', '2021-02-01', instrument=dict(market='CN', code='600000', name='合成股票'), quantity='80', gross_amount='800')
        self.iid = self.a['instrument_id']
        self.s.cache_actions(self.iid, [dict(kind='split_notice', date='2024-07-01', record_date='2024-06-30', factor='3', source='synthetic')])
        self.split = self.event('SPLIT', '2024-07-01', factor='3')

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, kind, date, **kw):
        return dict(portfolio_id=self.p['id'], instrument_id=getattr(self, 'iid', None), kind=kind, date=date, fx_rate='1', **kw)

    def event(self, kind, date, **kw):
        return self.s.save_event(self.payload(kind, date, **kw))

    def sale_payload(self, quantity, date='2023-04-01', buy=None):
        return self.payload('SELL', date, sell_lots=[dict(buy_event_id=(buy or self.a)['id'], quantity=quantity)], gross_amount='900')

    def effect(self, split=None):
        return self.s.transaction_detail(self.p['id'], (split or self.split)['id'])['facts']['split']

    def test_confirm_then_backfill_buy_and_sale_reassigns_only_remaining_lots(self):
        b = self.event('BUY', '2022-03-01', quantity='120', gross_amount='1200')
        self.assertEqual(self.effect()['quantity_after'], '600')
        before = self.s.store.state()
        payload = self.sale_payload('80')
        preview = self.s.preview_event(payload)
        self.assertEqual(self.s.store.state(), before)
        change = preview['split_changes'][0]
        self.assertEqual(change['before']['quantity_before'], '200')
        self.assertEqual(change['after']['quantity_before'], '120')
        self.assertEqual(change['after']['quantity_after'], '360')
        self.assertEqual([r['buy_event_id'] for r in change['after']['lots']], [b['id']])
        sale = self.s.save_event(dict(payload, expected_revision=preview['revision']))
        # Later purchases and sales never retroactively change split eligibility.
        self.event('BUY', '2024-12-02', quantity='50', gross_amount='500')
        self.s.save_event(self.sale_payload('60', '2024-12-03', b))
        self.assertEqual(self.effect(), change['after'])
        self.assertEqual([r['available_quantity'] for r in self.s.sale_lots(self.p['id'], self.iid, '2025-01-01')], ['300', '50'])
        rows = {r['event']['id']: r for r in self.s.transaction_rows(self.p['id'])}
        self.assertEqual(rows[self.split['id']]['facts']['split'], self.effect())
        self.assertEqual(rows[self.a['id']]['actual']['remaining_quantity'], '0')
        self.assertEqual(rows[b['id']]['actual']['remaining_quantity'], '300')
        self.assertEqual(rows[sale['id']]['event']['sell_lots'], payload['sell_lots'])
        self.assertEqual(rows[self.a['id']]['event'], self.a)
        self.assertEqual(rows[self.split['id']]['event'], self.split)
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id']), [])

    def test_partial_sale_correction_and_cancellation_refresh_effect(self):
        sale = self.s.save_event(self.sale_payload('30'))
        self.assertEqual((self.effect()['quantity_before'], self.effect()['quantity_after']), ('50', '150'))
        update = dict(sale, sell_lots=[dict(buy_event_id=self.a['id'], quantity='60')], price=None, change_reason='合成更正')
        preview = self.s.preview_event(update, sale['id'])
        self.assertEqual(preview['split_changes'][0]['after']['quantity_after'], '60')
        self.s.save_event(dict(update, expected_revision=preview['revision']), sale['id'])
        self.assertEqual(self.effect()['quantity_after'], '60')
        self.s.delete_event(sale['id'], '合成取消卖出')
        self.assertEqual(self.effect()['quantity_after'], '240')
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id']), [])

    def test_fully_sold_before_split_preserves_history_without_phantom_shares(self):
        self.s.save_event(self.sale_payload('80'))
        effect = self.effect()
        self.assertEqual(effect['status'], 'no_position')
        self.assertEqual(effect['quantity_after'], '0')
        self.assertEqual(effect['lots'], [])
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id']), [])
        later = self.event('BUY', '2024-07-02', quantity='10', gross_amount='100')
        self.assertEqual(self.effect(), effect)
        self.assertEqual(self.s.sale_lots(self.p['id'], self.iid, '2025-01-01')[0]['buy_event_id'], later['id'])
        self.assertEqual(self.s.sale_lots(self.p['id'], self.iid, '2025-01-01')[0]['available_quantity'], '10')

    def test_same_day_buy_excluded_sale_uses_adjusted_remainder(self):
        b = self.event('BUY', '2024-07-01', quantity='20', gross_amount='200')
        self.s.save_event(self.sale_payload('100', '2024-07-01'))
        self.assertEqual(self.effect()['quantity_before'], '80')
        self.assertEqual(self.effect()['quantity_after'], '240')
        self.assertEqual([r['buy_event_id'] for r in self.effect()['lots']], [self.a['id']])
        self.assertEqual({r['buy_event_id']: r['available_quantity'] for r in self.s.sale_lots(self.p['id'], self.iid, '2024-07-02')}, {self.a['id']: '140', b['id']: '20'})

    def test_backfill_replays_sequential_split_and_reverse_split(self):
        second = self.event('SPLIT', '2024-08-01', factor='0.5')
        preview = self.s.preview_event(self.sale_payload('30'))
        self.assertEqual([(c['after']['quantity_before'], c['after']['quantity_after']) for c in preview['split_changes']], [('50', '150'), ('150', '75')])
        self.s.save_event(self.sale_payload('30'))
        self.assertEqual(self.effect(second)['quantity_after'], '75')

    def test_buy_edit_cancel_and_split_correction(self):
        b = self.event('BUY', '2022-03-01', quantity='120', gross_amount='1200')
        self.s.save_event(dict(b, quantity='60', price=None, change_reason='合成更正数量'), b['id'])
        self.assertEqual(self.effect()['quantity_after'], '420')
        self.s.delete_event(b['id'], '合成取消买入')
        self.assertEqual(self.effect()['quantity_after'], '240')
        self.s.save_event(dict(self.split, factor='2', change_reason='合成比例更正'), self.split['id'])
        self.assertEqual(self.effect()['quantity_after'], '160')
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id'])[0]['type'], 'verify_split')
        self.s.delete_event(self.split['id'], '合成撤销确认')
        self.assertIsNone(self.effect())
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id'])[0]['type'], 'confirm_split')

    def test_pending_notice_tracks_backfills_in_both_directions(self):
        self.s.delete_event(self.split['id'], '合成撤销确认')
        self.assertEqual(len(self.s.todo_items(self.s.store.state(), self.p['id'])), 1)
        sale = self.s.save_event(self.sale_payload('80'))
        self.assertEqual(self.s.todo_items(self.s.store.state(), self.p['id']), [])
        self.s.delete_event(sale['id'], '合成恢复除权前持仓')
        self.assertEqual(len(self.s.todo_items(self.s.store.state(), self.p['id'])), 1)

    def test_same_day_duplicate_blocked_but_portfolios_isolated(self):
        before = self.s.store.state()
        for factor in ('3', '2'):
            payload = self.payload('SPLIT', '2024-07-01', factor=factor, allow_duplicate=True)
            with self.assertRaisesRegex(DomainError, '已有送转'):
                self.s.preview_event(payload)
            with self.assertRaisesRegex(DomainError, '已有送转'):
                self.s.save_event(payload)
        self.assertEqual(self.s.store.state(), before)
        q = self.s.save_portfolio(dict(name='合成隔离组合', start_date='2021-01-01', planned_amount='10000'))
        self.s.attach_instrument(q['id'], self.iid, {})
        self.s.save_event(dict(self.payload('BUY', '2021-02-01', quantity='10', gross_amount='100'), portfolio_id=q['id']))
        other = self.s.save_event(dict(self.payload('SPLIT', '2024-07-01', factor='3'), portfolio_id=q['id']))
        self.assertEqual(self.s.transaction_detail(q['id'], other['id'])['facts']['split']['quantity_after'], '30')
        self.assertEqual(self.effect()['quantity_after'], '240')

    def test_http_preview_readback_and_revision_conflict(self):
        with TestClient(create_app(self.tmp.name, testing=True, auto_sync=False)) as client:
            headers = {'x-csrf-token': client.get('/api/bootstrap').json()['csrf_token']}
            body = self.sale_payload('30')
            preview = client.post('/api/events/preview', json=body, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            data = preview.json()
            self.assertEqual(data['split_changes'][0]['after']['quantity_after'], '150')
            # A later backfill must invalidate the reviewed preview, not silently apply it.
            self.event('BUY', '2022-01-01', quantity='10', gross_amount='100')
            result = client.post('/api/events', json=dict(body, expected_revision=data['revision']), headers=headers)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(self.effect()['quantity_after'], '270')
            fresh = client.post('/api/events/preview', json=body, headers=headers).json()
            saved = client.post('/api/events', json=dict(body, expected_revision=fresh['revision']), headers=headers)
            self.assertEqual(saved.status_code, 200, saved.text)
            path = '/api/portfolios/'+self.p['id']+'/transactions'
            rows = client.get(path+'?page=1').json()['items']
            effect = next(r for r in rows if r['event']['id']==self.split['id'])['facts']['split']
            self.assertEqual(effect['quantity_after'], '180')
            self.assertEqual(client.get(path+'/'+self.split['id']).json()['facts']['split'], effect)
            cap = client.get('/api/capabilities').json()['corporate_actions']
            self.assertIn('split_changes', cap['preview_changes'])
            self.assertIn('EventPreview', client.get(client.get('/api/capabilities').json()['openapi']).json()['components']['schemas'])

    def test_legacy_confirmation_moved_to_registry_day_reviews_original_instead_of_new_split(self):
        wrong = self.s.save_event(dict(self.split,date='2024-06-30',fx_source_date='2024-06-30',change_reason='合成日期更正'),self.split['id'])
        registry_buy = self.event('BUY','2024-06-30',quantity='20',gross_amount='200')
        before = self.s.store.state()
        todo = self.s.todo_items(before,self.p['id'])[0]
        self.assertEqual(todo['type'],'verify_split')
        self.assertEqual(todo['event']['id'],self.split['id'])
        self.assertEqual(todo['notice']['date'],'2024-07-01')
        self.assertEqual(todo['notice']['record_date'],'2024-06-30')
        self.assertEqual(before,self.s.store.state())
        with self.assertRaisesRegex(DomainError,'更正原记录'):
            self.event('SPLIT','2024-07-01',factor='3',allow_duplicate=True)
        with self.assertRaisesRegex(DomainError,'更正原记录'):
            self.s.save_event(dict(registry_buy,kind='SPLIT',date='2024-07-01',factor='3',change_reason='合成改类型绕过检查'),registry_buy['id'])
        payload=dict(todo['event'],date=todo['notice']['date'],change_reason='合成核对除权日')
        preview=self.s.preview_event(payload,self.split['id'])
        self.assertEqual(preview['split_changes'][0]['before']['quantity_after'],'240')
        self.assertEqual(preview['split_changes'][0]['after']['quantity_after'],'300')
        saved=self.s.save_event(dict(payload,expected_revision=preview['revision']),self.split['id'])
        self.assertEqual(saved['source_split_id'],todo['notice']['id'])
        self.assertEqual(self.s.todo_items(self.s.store.state(),self.p['id']),[])
        self.assertEqual(len([e for e in self.s.store.state()['events'] if e['kind']=='SPLIT']),1)
        self.assertIn(registry_buy['id'],[r['buy_event_id'] for r in self.effect()['lots']])

    def test_linked_confirmation_keeps_identity_after_date_edit_and_csv_roundtrip(self):
        self.s.delete_event(self.split['id'],'合成重新确认关联')
        todo=self.s.todo_items(self.s.store.state(),self.p['id'])[0]
        linked=self.s.save_event(dict(todo['prefill'],portfolio_id=self.p['id']))
        payload=dict(linked,date='2024-06-20',fx_source_date='2024-06-20',change_reason='合成更正至其他日期')
        payload.pop('source_split_id')
        moved=self.s.save_event(payload,linked['id'])
        self.assertEqual(moved['source_split_id'],linked['source_split_id'])
        review=self.s.todo_items(self.s.store.state(),self.p['id'])[0]
        self.assertEqual(review['type'],'verify_split')
        self.assertEqual(review['event']['id'],linked['id'])
        self.assertEqual(self.s.csv_import(self.s.csv_export(self.p['id']),'events',self.p['id'])['imported'],0)
        with self.assertRaisesRegex(DomainError,'不能再次确认'):
            self.s.save_event(dict(todo['prefill'],portfolio_id=self.p['id']))
        self.s.save_event(dict(moved,date=todo['notice']['date'],change_reason='合成恢复除权日'),linked['id'])
        self.assertEqual(self.s.todo_items(self.s.store.state(),self.p['id']),[])

    def test_notice_link_rejects_wrong_instrument_and_stays_portfolio_scoped(self):
        notice=next(a for a in self.s.store.state()['corporate_actions'] if a['kind']=='split_notice')
        other=self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2021-03-01',instrument=dict(market='CN',code='600001'),quantity='10',gross_amount='100',fx_rate='1'))
        with self.assertRaisesRegex(DomainError,'关联无效'):
            self.s.save_event(dict(self.payload('SPLIT','2024-07-01',factor='3',source_split_id=notice['id']),instrument_id=other['instrument_id']))
        q=self.s.save_portfolio(dict(name='合成公告隔离',start_date='2021-01-01',planned_amount='10000'))
        self.s.attach_instrument(q['id'],self.iid,{})
        self.s.save_event(dict(self.payload('BUY','2021-02-01',quantity='10',gross_amount='100'),portfolio_id=q['id']))
        todo=self.s.todo_items(self.s.store.state(),q['id'])[0]
        linked=self.s.save_event(dict(todo['prefill'],portfolio_id=q['id']))
        self.assertEqual(linked['source_split_id'],notice['id'])
        self.assertEqual(self.s.todo_items(self.s.store.state(),q['id']),[])
        self.assertEqual(self.effect()['quantity_after'],'240')


if __name__ == '__main__': unittest.main()
