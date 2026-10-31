"""Dividend review uses only isolated, explicitly marked synthetic ledgers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import ConflictError, DomainError
from backend.environment import claim_environment
from backend.family_notifications import FamilyNotifications
from backend.family_service import FamilyService
from backend.reminders import ReminderService, dividend_reminders
from backend.service import Service
from backend.store import Store


class DividendReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = claim_environment(Path(self.temp.name) / 'synthetic', 'test')
        self.store = Store(self.directory)
        self.service = Service(self.store)
        self.portfolio = self.service.save_portfolio(dict(name='合成分红核对', start_date='2024-01-01', planned_amount='100000'))
        self.pid = self.portfolio['id']
        self.buy = self.service.save_event(dict(portfolio_id=self.pid, kind='BUY', date='2024-01-02',
            instrument=dict(market='HK', code='00700', name='合成港股'), quantity='100', gross_amount='1000', fx_rate='.91'))
        self.iid = self.buy['instrument_id']
        self.source = dict(code='00700', source_ref='synthetic-yahoo', ex_date='2024-01-11', record_date=None,
                           pay_date=None, per_share='1', currency='HKD', source='Yahoo现金分派参考', split_restore_factor='1')
        self.notifications = FamilyNotifications(self.store, FamilyService(self.store), 'test')
        self.center = ReminderService(self.store, self.notifications)

    def tearDown(self):
        self.temp.cleanup()

    def notice(self, **changes):
        self.source.update(changes)
        self.service.cache_dividends(self.iid, [self.source])
        return self.candidate()

    def candidate(self):
        return next(c for c in self.service.dividend_candidates(self.pid) if c['notice']['source_ref']=='synthetic-yahoo')

    def confirm(self, kind='DIVIDEND'):
        row = self.notice()
        event = self.service.confirm_dividend(row['notice']['id'], dict(row['suggested_event'], kind=kind,
            gross_amount='99.9876', tax='1.2345', net_amount='98.7531', tax_known=True,
            fee='0.12' if kind=='REINVEST' else '0', pay_date='2024-01-18', fx_rate='.9134', fx_source_date='2024-01-10',
            pay_fx_rate='.9234', pay_fx_source_date='2024-01-17', pay_fx_type='manual',
            reinvest_date='2024-01-19', reinvest_quantity='5.1234', reinvest_gross_amount='98.5',
            reinvest_fx_rate='.9334', reinvest_fx_source_date='2024-01-18', reinvest_fx_type='manual'))
        return event

    def body(self, row):
        return dict(portfolio_id=self.pid, event_id=row['event_id'], mode='keep_recorded',
                    review_basis=row['review_basis'], expected_revision=row['revision'], reason='合成凭证核对，保留实际到账')

    def keep(self, row=None):
        row = row or self.candidate()
        return self.service.review_dividend(row['notice']['id'], self.body(row))

    def reminders(self):
        return dividend_reminders(self.store, self.store.state())

    def assert_preserved(self, kind):
        event = self.confirm(kind)
        row = self.notice(per_share='1.01')
        self.assertEqual(row['status'], 'needs_review')
        before = self.store.state()
        finances = self.service.dashboard(self.pid, end='2024-03-01')['capital_summary']
        with self.store.connection() as db:
            columns = [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY id')]
        self.assertEqual(self.store.state(), before, 'candidate and dashboard reads cannot write')
        result = self.keep(row)
        after = self.store.state()
        self.assertEqual(after['events'], before['events'])
        with self.store.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM events ORDER BY id')], columns)
        self.assertEqual(result['event'], event)
        self.assertEqual(result['revision'], before['revision']+1)
        self.assertEqual(self.service.dashboard(self.pid, end='2024-03-01')['capital_summary'], finances)
        self.assertEqual(self.candidate()['status'], 'confirmed')
        audit = self.service.revisions('dividend_decision', result['decision']['id'])
        self.assertEqual(audit[0]['after_data']['mode'], 'keep_recorded')
        self.assertEqual(audit[0]['after_data']['event_id'], event['id'])
        self.assertEqual(audit[0]['reason'], self.body(row)['reason'])

    def test_keep_actual_amount_leaves_all_event_columns_and_finances_unchanged(self):
        self.assert_preserved('DIVIDEND')

    def test_reinvestment_keeps_confirmed_shares_fees_dates_and_all_locked_fx(self):
        self.assert_preserved('REINVEST')

    def test_yahoo_micro_changes_use_last_reviewed_anchor_and_never_touch_ledger(self):
        event = self.confirm()
        before = self.store.state()['events']
        for rate in ('1.00005', '1.00009', '1.0001'):
            row = self.notice(per_share=rate)
            self.assertEqual(row['status'], 'confirmed')
            self.assertEqual(self.reminders(), [])
        row = self.notice(per_share='1.00011')  # cumulative drift exceeds anchor, despite tiny last step
        self.assertEqual(row['status'], 'needs_review')
        self.assertEqual(row['recorded_event']['id'], event['id'])
        self.assertEqual(self.store.state()['events'], before)
        self.keep(row)
        self.assertEqual(self.notice(per_share='1.00016')['status'], 'confirmed')
        self.assertEqual(self.notice(per_share='1.00022')['status'], 'needs_review')

    def test_relative_and_absolute_caps_and_other_providers(self):
        self.confirm()
        self.keep(self.notice(per_share='.01'))
        self.assertEqual(self.notice(per_share='.010002')['status'], 'needs_review', 'relative cap required')
        self.keep(self.notice(per_share='100'))
        self.assertEqual(self.notice(per_share='100.00011')['status'], 'needs_review', 'absolute cap required')
        self.keep(self.notice(per_share='1', source='合成官方公告'))
        self.assertEqual(self.notice(per_share='1.000001')['status'], 'needs_review', 'no tolerance for official source')

    def test_any_entitlement_change_and_known_date_currency_or_split_change_reopens(self):
        self.confirm()
        self.keep(self.notice(per_share='1.01'))
        self.service.save_event(dict(portfolio_id=self.pid, instrument_id=self.iid, kind='BUY', date='2024-01-03',
                                     quantity='.00001', gross_amount='.01', fx_rate='.91'))
        row = self.candidate()
        self.assertEqual(row['status'], 'needs_review')
        self.assertIn('权益份额变化', row['review_changes'])
        self.keep(row)
        for changes, label in ((dict(pay_date='2024-01-20'), '来源派息日变化'),
                               (dict(record_date='2024-01-10'), '来源登记日变化'),
                               (dict(currency='USD'), '派息币种变化'),
                               (dict(split_restore_factor='2'), '来源拆分还原因子变化'),
                               (dict(ex_date='2024-01-12'), '除息日变化')):
            with self.subTest(changes=changes):
                row = self.notice(**changes)
                self.assertEqual(row['status'], 'needs_review')
                self.assertIn(label, row['review_changes'])
                self.keep(row)

    def test_missing_source_dates_never_clear_previously_reviewed_facts(self):
        event = self.confirm()
        self.keep(self.notice(record_date='2024-01-10', pay_date='2024-01-20', per_share='1.01'))
        row = self.notice(record_date=None, pay_date=None)
        self.assertEqual(row['status'], 'confirmed')
        self.assertEqual(row['review_basis']['pay_date'], '2024-01-20')
        self.assertEqual(row['review_basis']['notice_record_date'], '2024-01-10')
        self.assertEqual(self.store.state()['events'][-1], event)
        self.assertEqual(self.notice(record_date='2024-01-10', pay_date='2024-01-20')['status'], 'confirmed')

    def test_stale_revision_wrong_basis_wrong_event_and_repeated_post_never_write(self):
        self.confirm()
        row = self.notice(per_share='1.01')
        body = self.body(row)
        self.notice(per_share='1.01001')
        before = self.store.state()
        with self.assertRaises(ConflictError):
            self.service.review_dividend(row['notice']['id'], body)
        self.assertEqual(self.store.state(), before)
        row = self.candidate()
        for changes in (dict(event_id=self.buy['id']), dict(review_basis=body['review_basis']),
                        dict(expected_revision=None), dict(reason=' '), dict(mode='recalculate')):
            with self.subTest(changes=changes), self.assertRaises(DomainError):
                self.service.review_dividend(row['notice']['id'], self.body(row) | changes)
            self.assertEqual(self.store.state(), before)
        self.keep(row)
        after = self.store.state()
        with self.assertRaises(ConflictError):
            self.keep(row)
        with self.assertRaises(DomainError):
            self.keep(self.candidate())
        self.assertEqual(self.store.state(), after)
        self.assertEqual(len(after['events']), 2)

    def test_unrecorded_notice_cannot_use_keep_and_new_notice_still_reminds(self):
        row = self.notice()
        before = self.store.state()
        with self.assertRaises(DomainError):
            self.keep(row)
        self.assertEqual(self.store.state(), before)
        self.confirm()
        self.keep(self.notice(per_share='1.01'))
        self.service.cache_dividends(self.iid, [dict(self.source, source_ref='synthetic-new', ex_date='2024-03-01')])
        self.assertEqual(len(self.reminders()), 1)

    def test_manual_and_legacy_events_can_be_reviewed_without_rewriting_them(self):
        row = self.notice()
        manual = self.service.save_event(dict(row['suggested_event'], source_dividend_id='', dividend_basis=None,
            gross_amount='99', net_amount='98', tax='1', tax_known=True, pay_date='2024-01-18', fx_rate='.91'))
        self.assertEqual(self.candidate()['status'], 'needs_review')
        self.keep()
        self.assertEqual(self.candidate()['status'], 'manual_recorded')
        self.assertEqual(self.store.state()['events'][-1], manual)
        self.assertEqual(self.notice(per_share='1.00005')['status'], 'manual_recorded')

    def test_legacy_four_field_basis_requires_review_for_unknown_provider_then_stabilizes(self):
        event = self.confirm()
        legacy = {k:event['dividend_basis'][k] for k in ('eligible_quantity','per_share','estimated_gross','record_date')}
        event = self.service.save_event(dict(event, dividend_basis=legacy, change_reason='合成旧格式兼容'), event['id'])
        self.assertEqual(self.candidate()['status'], 'confirmed')
        before = self.store.state()['events']
        self.assertEqual(self.notice(per_share='1.00005')['status'], 'needs_review')
        self.assertEqual(self.store.state()['events'], before)
        self.keep()
        self.assertEqual(self.notice(per_share='1.00009')['status'], 'confirmed')
        self.assertEqual(self.store.state()['events'], before)

    def test_missing_record_date_still_replays_domestic_entitlement(self):
        event = self.service.save_event(dict(portfolio_id=self.pid,kind='BUY',date='2024-01-02',
            instrument=dict(market='CN',code='510300',name='合成登记日'),quantity='100',gross_amount='100',fx_rate='1'))
        source = dict(code='510300',source_ref='synthetic-domestic',record_date='2024-01-10',ex_date='2024-01-11',
                      pay_date='2024-01-15',per_share='.1',currency='CNY',source='合成官方公告')
        self.service.cache_dividends(event['instrument_id'],[source])
        candidate = next(c for c in self.service.dividend_candidates(self.pid) if c['instrument']['id']==event['instrument_id'])
        receipt = self.service.confirm_dividend(candidate['notice']['id'],dict(candidate['suggested_event'],tax_known=True))
        self.service.cache_dividends(event['instrument_id'],[dict(source,record_date=None,pay_date=None)])
        candidate = next(c for c in self.service.dividend_candidates(self.pid) if c['event_id']==receipt['id'])
        self.assertEqual(candidate['status'],'confirmed')
        self.assertEqual(candidate['eligible_quantity'],'100')
        self.service.save_event(dict(portfolio_id=self.pid,instrument_id=event['instrument_id'],kind='BUY',date='2024-01-05',quantity='1',gross_amount='1',fx_rate='1'))
        candidate = next(c for c in self.service.dividend_candidates(self.pid) if c['event_id']==receipt['id'])
        self.assertEqual(candidate['status'],'needs_review')
        self.assertEqual(candidate['eligible_quantity'],'101')
        self.assertEqual(next(e for e in self.store.state()['events'] if e['id']==receipt['id']),receipt)

    def test_recalculation_after_keep_still_updates_only_original_event(self):
        event = self.confirm()
        self.keep(self.notice(per_share='1.01'))
        row = self.notice(per_share='1.02')
        before = self.store.state()['events']
        preview = self.service.preview_event(row['suggested_event'],event['id'])
        self.assertEqual(self.store.state()['events'],before)
        updated = self.service.confirm_dividend(row['notice']['id'],dict(row['suggested_event'],tax_known=True,expected_revision=preview['revision']))
        self.assertEqual(updated['id'],event['id'])
        self.assertEqual(updated['gross_amount'],'102')
        self.assertEqual(len(self.store.state()['events']),len(before))
        self.assertEqual(self.candidate()['status'],'confirmed')

    def test_separate_portfolios_cannot_complete_each_others_review(self):
        self.confirm()
        row = self.notice(per_share='1.01')
        other = self.service.save_portfolio(dict(name='合成隔离组合', start_date='2024-01-01', planned_amount='100'))
        body = self.body(self.candidate()) | dict(portfolio_id=other['id'])
        before = self.store.state()
        with self.assertRaises(DomainError):
            self.service.review_dividend(row['notice']['id'], body)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.candidate()['status'], 'needs_review')

    def test_pending_and_review_notifications_are_stable_and_complete_together(self):
        self.notice()
        first = self.reminders()[0]['fingerprint']
        self.assertEqual(self.notice(per_share='1.00005')['status'], 'pending')
        self.assertEqual(self.reminders()[0]['fingerprint'], first)
        self.confirm()
        row = self.notice(per_share='1.01')
        first = self.reminders()[0]['fingerprint']
        self.notice(per_share='1.01001', coverage_note='合成来源说明更新')
        self.assertEqual(self.reminders()[0]['fingerprint'], first)
        self.assertEqual(self.notifications.dividend_rows(self.store.state())[0]['id'], first)
        self.notice(per_share='1.02')
        self.assertNotEqual(self.reminders()[0]['fingerprint'], first, 'material correction redispatches unresolved review')
        self.keep()
        self.assertEqual(self.notifications.dividend_rows(self.store.state()), [])
        self.assertFalse(any(r['kind']=='dividend' for r in self.center.read()['items']))
        self.notice(per_share='1.03')
        self.assertNotEqual(self.reminders()[0]['fingerprint'], first)

    def test_native_reconcile_reuses_dispatch_and_removes_completed_review(self):
        self.confirm()
        self.notice(per_share='1.01')
        native = self.notifications
        native.prepared = True
        native._save(native.preferences, dict(enabled=True))
        calls = []
        def helper(command, requests, active):
            calls.append((requests, active))
            return dict(authorization='authorized', pending_ids=[r['id'] for r in requests], delivered_ids=[])
        with patch.object(native, '_helper', side_effect=helper):
            native.reconcile(True)
            dispatched = native._read(native.result_path)['dividend_dispatches']
            self.assertEqual(len(dispatched), 1)
            self.notice(per_share='1.01001')
            native.reconcile(True)
            self.assertEqual(native._read(native.result_path)['dividend_dispatches'], dispatched)
            self.assertEqual(calls[0][0], calls[1][0])
            self.keep()
            native.reconcile(True)
            self.assertEqual(calls[-1], ([], []))
            self.assertEqual(native._read(native.result_path)['dividend_dispatches'], {})

    def test_legacy_basis_currency_correction_still_requires_review(self):
        event = self.confirm()
        legacy = {k:event['dividend_basis'][k] for k in ('eligible_quantity','per_share','estimated_gross','record_date')}
        self.service.save_event(dict(event,dividend_basis=legacy,change_reason='合成旧格式兼容'),event['id'])
        before = self.store.state()['events']
        row = self.notice(currency='USD')
        self.assertEqual(row['status'],'needs_review')
        self.assertIn('派息币种变化',row['review_changes'])
        self.assertEqual(len(self.reminders()),1)
        self.assertEqual(self.store.state()['events'],before)

    def test_known_entitlement_becoming_unknown_is_not_treated_as_unchanged(self):
        self.confirm()
        self.service.delete_event(self.buy['id'],'合成取消历史买入')
        self.assertEqual(self.candidate()['status'],'needs_review')
        self.service.save_portfolio(dict(self.portfolio,start_date='2024-01-11'),self.pid)
        row = self.candidate()
        self.assertIsNone(row['eligible_quantity'])
        self.assertEqual(row['status'],'needs_review')
        self.assertIn('权益份额变化',row['review_changes'])
        self.assertEqual(len(self.reminders()),1)

    def test_receipt_note_or_tax_correction_cannot_revert_last_reviewed_entitlement(self):
        event = self.confirm()
        added = self.service.save_event(dict(portfolio_id=self.pid,instrument_id=self.iid,kind='BUY',
            date='2024-01-03',quantity='1',gross_amount='10',fx_rate='.91'))
        self.keep()
        for changes in (dict(reason='合成备注更正'),dict(tax='2.2345',net_amount='97.7531')):
            event = self.service.save_event(dict(event,**changes,change_reason='合成凭证补充'),event['id'])
            row = self.candidate()
            self.assertEqual(row['status'],'confirmed')
            self.assertEqual(row['previous_basis']['eligible_quantity'],'101')
        self.service.delete_event(added['id'],'合成取消补录买入')
        row = self.candidate()
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual(row['previous_basis']['eligible_quantity'],'101')
        self.assertIn('权益份额变化',row['review_changes'])

    def manual_receipt(self):
        row = self.notice()
        return self.service.save_event(dict(row['suggested_event'],source_dividend_id='',dividend_basis=None,
            gross_amount='99',net_amount='98',tax='1',tax_known=True,pay_date='2024-01-18',fx_rate='.91'))

    def test_manual_receipt_date_correction_retains_binding_and_uses_original_event(self):
        event = self.manual_receipt()
        self.keep()
        changed = self.service.save_event(dict(event,date='2024-01-12',change_reason='合成除息日更正'),event['id'])
        row = self.candidate()
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual(row['event_id'],event['id'])
        self.assertEqual(row['recorded_event'],changed)
        self.assertIn('原流水发生日期变化',row['review_changes'])
        original = self.store.state()['events']
        self.keep(row)
        self.assertEqual(self.candidate()['status'],'manual_recorded')
        self.assertEqual(self.store.state()['events'],original)
        with self.assertRaises(DomainError):
            self.service.confirm_dividend(row['notice']['id'],dict(row['suggested_event'],expected_revision=self.store.state()['revision']))

    def test_manual_source_ex_date_correction_cannot_create_second_receipt(self):
        event = self.manual_receipt()
        self.keep()
        row = self.notice(ex_date='2024-01-12')
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual(row['event_id'],event['id'])
        self.assertIn('除息日变化',row['review_changes'])
        before = len(self.store.state()['events'])
        updated = self.service.confirm_dividend(row['notice']['id'],dict(row['suggested_event'],tax_known=True,expected_revision=row['revision']))
        self.assertEqual(updated['id'],event['id'])
        self.assertEqual(len(self.store.state()['events']),before)

    def test_bound_manual_receipt_remains_visible_when_entitlement_is_unknown(self):
        event = self.manual_receipt()
        self.keep()
        self.service.save_event(dict(event,date='2024-01-12',change_reason='合成日期更正'),event['id'])
        self.service.delete_event(self.buy['id'],'合成取消唯一买入')
        self.service.save_portfolio(dict(self.portfolio,start_date='2024-01-11'),self.pid)
        row = self.candidate()
        self.assertEqual(row['event_id'],event['id'])
        self.assertIsNone(row['eligible_quantity'])
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual(len(self.reminders()),1)

    def test_keep_succeeds_with_all_event_writes_denied_and_legacy_bytes_preserved(self):
        import json, sqlite3
        from contextlib import contextmanager
        event = self.confirm('REINVEST')
        with self.store.transaction() as db:
            raw = json.loads(db.execute('SELECT data FROM events WHERE id=?',(event['id'],)).fetchone()[0])
            raw.update(legacy_custom_fact={'synthetic':'preserve exactly'},tax_known=False)
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(raw,ensure_ascii=False,indent=2),event['id']))
        self.notice(per_share='1.01')
        with self.store.connection() as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY id')]
        connect = self.store.connection
        attempts = []
        @contextmanager
        def guarded_connection():
            with connect() as db:
                def authorize(action,table,*args):
                    if table=='events' and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                        attempts.append(action)
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                yield db
        with patch.object(self.store,'connection',guarded_connection):
            self.keep()
        with self.store.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM events ORDER BY id')],before)
        self.assertEqual(attempts,[])
        self.assertEqual(self.candidate()['status'],'confirmed')

    def test_keep_refuses_source_binding_to_non_receipt_without_writing(self):
        event = self.confirm()
        self.service.save_event(dict(event,kind='BUY',quantity='1',price=event['gross_amount'],change_reason='合成类型更正'),event['id'])
        row = self.candidate()
        self.assertEqual(row['status'],'needs_review')
        self.assertEqual(row['recorded_event']['kind'],'BUY')
        before = self.store.state()
        with self.assertRaisesRegex(DomainError,'本标的的分红或再投'):
            self.keep(row)
        self.assertEqual(self.store.state(),before)

    def test_keep_refuses_source_binding_to_another_instrument_without_writing(self):
        event = self.confirm()
        other = self.service.save_event(dict(portfolio_id=self.pid,kind='BUY',date='2024-01-02',
            instrument=dict(market='HK',code='00701',name='合成另一标的'),quantity='1',gross_amount='10',fx_rate='.91'))
        self.service.save_event(dict(event,instrument_id=other['instrument_id'],change_reason='合成标的更正'),event['id'])
        row = self.notice(per_share='1.01')
        self.assertEqual(row['status'],'needs_review')
        before = self.store.state()
        with self.assertRaisesRegex(DomainError,'本标的的分红或再投'):
            self.keep(row)
        self.assertEqual(self.store.state(),before)

    def test_http_rejects_non_receipt_review_with_422_and_no_write(self):
        event = self.confirm()
        self.service.save_event(dict(event,kind='BUY',quantity='1',price=event['gross_amount'],change_reason='合成类型更正'),event['id'])
        app = create_app(self.directory,testing=True,auto_sync=False)
        client = TestClient(app,base_url='http://127.0.0.1')
        bootstrap = client.get('/api/bootstrap').json()
        row = client.get('/api/portfolios/'+self.pid+'/dividends').json()[0]
        before = self.store.state()
        response = client.post('/api/dividends/'+row['notice']['id']+'/review',json=self.body(row),
            headers={'x-csrf-token':bootstrap['csrf_token'],'origin':'http://127.0.0.1'})
        self.assertEqual(response.status_code,422)
        self.assertIn('本标的的分红或再投',response.json()['error'])
        self.assertEqual(self.store.state(),before)

    def test_http_contract_required_revision_csrf_audit_and_notification_invalidation(self):
        self.confirm()
        self.notice(per_share='1.01')
        app = create_app(self.directory, testing=True, auto_sync=False)
        client = TestClient(app, base_url='http://127.0.0.1')
        bootstrap = client.get('/api/bootstrap').json()
        headers = {'x-csrf-token':bootstrap['csrf_token'], 'origin':'http://127.0.0.1'}
        row = client.get('/api/portfolios/'+self.pid+'/dividends').json()[0]
        url = '/api/dividends/'+row['notice']['id']+'/review'
        body = self.body(row)
        before = self.store.state()
        self.assertEqual(client.post(url, json=body).status_code, 403)
        self.assertEqual(client.post(url, json={k:v for k,v in body.items() if k!='expected_revision'}, headers=headers).status_code, 422)
        self.assertEqual(self.store.state(), before)
        paths = client.get('/api/openapi.json').json()['paths']
        self.assertIn('/api/dividends/{notice_id}/review', paths)
        self.assertFalse(client.get('/api/capabilities').json()['dividend_review']['event_write'])
        with patch.object(FamilyNotifications, 'invalidate') as invalidate:
            response = client.post(url, json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            invalidate.assert_called_once()
        self.assertEqual(response.json()['event'], row['recorded_event'])
        self.assertEqual(self.store.state()['events'], before['events'])
        self.assertEqual(client.post(url, json=body, headers=headers).status_code, 409)
        self.assertEqual(client.get('/api/portfolios/'+self.pid+'/dividends').json()[0]['status'], 'confirmed')
        decision_id = response.json()['decision']['id']
        self.assertEqual(client.get('/api/revisions/dividend_decision/'+decision_id).json()[0]['after_data']['mode'], 'keep_recorded')


if __name__=='__main__':
    unittest.main()
