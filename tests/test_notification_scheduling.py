"""Synthetic one-slot OS queue; never launches or authorizes native notifications."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.common import DomainError
from backend.environment import claim_environment
from backend.family_notifications import FamilyNotifications
from backend.family_service import FamilyService
from backend.store import Store


TZ = ZoneInfo('Asia/Shanghai')


class NotificationSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(claim_environment(Path(self.temp.name)/'synthetic', 'dev'))
        self.native = FamilyNotifications(self.store, FamilyService(self.store), 'dev')
        self.native.prepared = True
        self.native._save(self.native.preferences, {'enabled': True})
        self.current = datetime(2026, 11, 2, 8, 59, 50, tzinfo=TZ)
        self.rows = []
        self.dividends = []
        self.pending = {}
        self.delivered = set()
        self.calls = []
        self.authorized = True
        self.reject = False
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.current

        for mock in (patch('backend.family_notifications.datetime', Clock),
                     patch('backend.family_notifications.now', side_effect=lambda: self.current.isoformat()),
                     patch('backend.family_notifications.schedule_projection', side_effect=lambda *a: copy.deepcopy(self.rows)),
                     patch.object(self.native, 'dividend_rows', side_effect=lambda *a: copy.deepcopy(self.dividends)),
                     patch.object(self.native, '_helper', side_effect=self.os_call)):
            mock.start()
            self.addCleanup(mock.stop)

    def os_call(self, command, requests=None, active=None):
        for identity, request in list(self.pending.items()):
            if datetime.fromisoformat(request['at']) <= self.current:
                self.delivered.add(identity)
                del self.pending[identity]
        self.calls.append((command, copy.deepcopy(requests), copy.deepcopy(active)))
        test_id = None
        if command == 'test':
            test_id = 'family-test-'+str(len(self.calls))
            requests = [dict(id=test_id, at=(self.current+timedelta(seconds=3)).isoformat())]
        requests = requests or []
        self.assertLessEqual(len(requests), 1, 'production must never submit a batch to the one-slot OS')
        desired_ids = {r['id'] for r in requests}
        removed = [identity for identity in self.pending if identity not in desired_ids]
        for identity in removed:
            del self.pending[identity]
        failures = []
        for request in requests:
            if not self.authorized or self.reject:
                failures.append(dict(id=request['id'], domain='SyntheticOS', code=7, message='合成提交失败'))
            elif request['id'] not in self.pending:
                # Model the observed system behavior: only the newest is retained.
                self.pending = {request['id']: copy.deepcopy(request)}
        return dict(authorization='authorized' if self.authorized else 'denied', alerts=2,
                    pending_ids=list(self.pending), delivered_ids=sorted(self.delivered),
                    removed_pending_ids=removed, request_errors=failures, test_id=test_id)

    def row(self, identity='schedule', due='2026-11-09', **extra):
        return dict(id=identity, item_id='item-'+identity, name='合成事项', kind='debt',
                    status='pending', version=1, due_date=due, **extra)

    def dividend(self, identity='dividend'):
        return dict(id=identity, portfolio_id='synthetic-portfolio', kind='dividend',
                    status='pending', version=1, reminder_id='dividend:'+identity)

    def reconcile(self):
        return self.native.reconcile(True)

    def test_many_reminders_use_one_slot_and_deferred_are_not_failures(self):
        self.rows = [self.row(str(i), due=str((self.current+timedelta(days=7+i)).date())) for i in range(25)]
        before = self.store.state()
        result = self.reconcile()
        self.assertEqual(result['desired_count'], 75)
        self.assertEqual(result['pending_count'], 1)
        self.assertEqual(result['selected_count'], 1)
        self.assertEqual(result['deferred_count'], 74)
        self.assertEqual(result['missing_count'], 0)
        self.assertIsNone(result['error'])
        self.assertEqual(result['next_notification_at'], '2026-11-02T09:00:00+08:00')
        self.assertEqual(self.store.state(), before)

    def test_simultaneous_items_are_combined_and_click_opens_global_center(self):
        self.rows = [self.row('a'), self.row('b')]
        result = self.reconcile()
        request = next(iter(self.pending.values()))
        self.assertEqual(result['selected_count'], 2)
        self.assertEqual(result['deferred_count'], 4)
        self.assertEqual(request['reminder_id'], '')
        self.assertEqual(request['route'], 'reminders')
        self.assertEqual(set(request['schedule_keys']), {'a:1', 'b:1'})
        self.assertNotIn('合成事项', request['body'])
        self.rows.reverse()
        self.assertEqual(self.reconcile()['queued_request']['id'], request['id'])

    def test_after_delivery_rolls_to_next_time_without_resubmitting_old_items(self):
        self.rows = [self.row('a'), self.row('b', due='2026-11-10')]
        first = self.reconcile()['queued_request']['id']
        self.current += timedelta(seconds=15)
        result = self.reconcile()
        self.assertIn(first, self.delivered)
        self.assertNotIn(first, self.pending)
        self.assertEqual(result['next_notification_at'], '2026-11-03T09:00:00+08:00')
        self.assertIsNone(result['error'])

    def test_cancel_version_change_and_snooze_replace_only_the_selected_request(self):
        self.rows = [self.row()]
        first = self.reconcile()['queued_request']['id']
        self.rows[0]['version'] = 2
        second = self.reconcile()['queued_request']['id']
        self.assertNotEqual(first, second)
        self.assertNotIn(first, self.pending)
        self.rows[0]['snooze_until'] = '2026-11-10'
        self.assertEqual(self.reconcile()['next_notification_at'], '2026-11-10T09:00:00+08:00')
        self.rows[0]['status'] = 'cancelled'
        result = self.reconcile()
        self.assertEqual(result['desired_count'], 0)
        self.assertEqual(self.pending, {})
        self.assertIsNone(result['next_notification_at'])

    def test_combined_rejection_retains_each_item_reason_but_excludes_deferred(self):
        self.rows = [self.row('a'), self.row('b')]
        self.reject = True
        result = self.reconcile()
        self.assertEqual(result['missing_count'], 2)
        self.assertEqual(result['deferred_count'], 4)
        self.assertEqual({r['item_id'] for r in result['missing_requests']}, {'item-a', 'item-b'})
        self.assertTrue(all(r['error_domain'] == 'SyntheticOS' for r in result['missing_requests']))
        self.reject = False
        result = self.reconcile()
        self.assertEqual(result['missing_requests'], [])
        self.assertIsNone(result['error'])

    def test_dividends_combine_deduplicate_and_all_remain_retryable_when_denied(self):
        self.dividends = [self.dividend('a'), self.dividend('b')]
        self.authorized = False
        denied = self.reconcile()
        self.assertEqual(denied['dividend_dispatches'], {})
        self.assertEqual(denied['missing_count'], 2)
        self.current += timedelta(seconds=20)
        self.authorized = True
        result = self.reconcile()
        self.assertEqual(set(result['dividend_dispatches']), {'a', 'b'})
        first = result['queued_request']['id']
        self.assertEqual(self.reconcile()['queued_request']['id'], first)
        self.current += timedelta(seconds=10)
        result = self.reconcile()
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(set(result['dividend_dispatches']), {'a', 'b'})

    def test_deferred_dividend_is_not_marked_dispatched_and_is_scheduled_next(self):
        self.current = self.current.replace(second=58)
        self.rows = [self.row()]
        self.dividends = [self.dividend()]
        result = self.reconcile()
        self.assertEqual(result['dividend_dispatches'], {})
        self.assertEqual(next(iter(self.pending.values()))['schedule_id'], 'schedule')
        self.current += timedelta(seconds=7)
        result = self.reconcile()
        self.assertIn('dividend', result['dividend_dispatches'])
        self.assertEqual(next(iter(self.pending.values()))['id'], 'dividend')

    def test_preempted_dividend_is_retried_instead_of_assumed_delivered(self):
        self.current = self.current.replace(second=58)
        self.dividends = [self.dividend()]
        self.reconcile()
        self.rows = [self.row()]
        result = self.reconcile()
        self.assertNotIn('dividend', result['dividend_dispatches'])
        self.current += timedelta(seconds=7)
        result = self.reconcile()
        self.assertGreater(datetime.fromisoformat(result['dividend_dispatches']['dividend']), self.current)
        self.assertEqual(set(self.pending), {'dividend'})

    def test_test_notification_reserves_slot_then_restores_business_reminder(self):
        self.rows = [self.row(due='2026-11-10')]
        original = self.reconcile()['queued_request']['id']
        result = self.native.action('test')
        self.assertTrue(result['test_scheduled'])
        test_id = result['test_id']
        self.assertEqual(set(self.pending), {test_id})
        self.assertEqual(result['dividend_dispatches'], {})
        calls = len(self.calls)
        self.current += timedelta(seconds=5)
        self.reconcile()
        self.assertEqual(len(self.calls), calls)
        self.current += timedelta(seconds=6)
        result = self.reconcile()
        self.assertIn(test_id, self.delivered)
        self.assertEqual(set(self.pending), {original})
        self.assertNotIn('test_pending_until', result)

    def test_test_notification_cannot_displace_an_imminent_business_reminder(self):
        self.dividends = [self.dividend()]
        self.reconcile()
        calls = len(self.calls)
        with self.assertRaisesRegex(DomainError, '即将触发'):
            self.native.action('test')
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(set(self.pending), {'dividend'})

    def test_failed_test_restores_business_queue_and_does_not_claim_success(self):
        self.rows = [self.row(due='2026-11-10')]
        original = self.reconcile()['queued_request']['id']

        def rejected_test(command, *args):
            if command == 'test':
                self.pending.clear()
                return dict(authorization='authorized', test_id='family-test-new',
                            pending_ids=[], delivered_ids=['family-test-old'])
            return self.os_call(command, *args)

        with patch.object(self.native, '_helper', side_effect=rejected_test):
            with self.assertRaisesRegex(DomainError, '未出现在系统队列'):
                self.native.action('test')
        self.assertEqual(set(self.pending), {original})
        self.assertNotIn('test_pending_until', self.native.status())

    def test_restarted_service_resumes_next_slot_and_keeps_dividend_deduplication(self):
        self.rows = [self.row(due='2026-11-10')]
        self.dividends = [self.dividend()]
        self.reconcile()
        self.current += timedelta(seconds=15)
        restarted = FamilyNotifications(self.store, FamilyService(self.store), 'dev')
        restarted.prepared = True
        with patch.object(restarted, 'dividend_rows', return_value=self.dividends), patch.object(restarted, '_helper', side_effect=self.os_call):
            result = restarted.reconcile(True)
        self.assertEqual(set(result['dividend_dispatches']), {'dividend'})
        self.assertEqual(result['next_notification_at'], '2026-11-03T09:00:00+08:00')

    def test_new_dividend_cannot_preempt_imminent_family_reminder(self):
        self.current = self.current.replace(second=54)
        self.rows = [self.row()]
        original = self.reconcile()['queued_request']['id']
        self.dividends = [self.dividend()]
        result = self.reconcile()
        self.assertEqual(set(self.pending), {original})
        self.assertNotIn('dividend', result['dividend_dispatches'])
        self.current += timedelta(seconds=7)
        result = self.reconcile()
        self.assertIn(original, self.delivered)
        self.assertEqual(set(self.pending), {'dividend'})
        self.assertGreater(datetime.fromisoformat(result['dividend_dispatches']['dividend']), self.current)

    def test_new_imminent_family_reminder_interrupts_test_slot(self):
        self.current = self.current.replace(second=55)
        self.reconcile()
        test_id = self.native.action('test')['test_id']
        self.current += timedelta(seconds=1)
        self.rows = [self.row()]
        result = self.reconcile()
        self.assertNotIn('test_pending_until', result)
        original = result['queued_request']['id']
        self.assertEqual(set(self.pending), {original})
        self.assertNotIn(test_id, self.pending)
        self.current += timedelta(seconds=10)
        self.reconcile()
        self.assertIn(original, self.delivered)

    def test_immediate_dividend_time_is_calculated_after_slow_build(self):
        self.native.prepared = False
        self.dividends = [self.dividend()]

        def build():
            self.current += timedelta(seconds=20)

        with patch.object(self.native, '_build', side_effect=build):
            result = self.reconcile()
        self.assertEqual(datetime.fromisoformat(result['next_notification_at']), self.current+timedelta(seconds=5))

    def test_revision_race_keeps_accepted_dividend_identity_for_next_reconcile(self):
        self.dividends = [self.dividend()]
        original = self.os_call

        def concurrent_write(*args):
            result = original(*args)
            with self.store.connection() as db:
                db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                db.commit()
            return result

        with patch.object(self.native, '_helper', side_effect=concurrent_write):
            result = self.reconcile()
        self.assertIn('计划在排程期间发生变化', result['error'])
        self.assertIn('dividend', result['dividend_dispatches'])
        self.current += timedelta(seconds=10)
        self.assertIsNone(self.reconcile()['error'])
        self.assertEqual(self.pending, {})


if __name__ == '__main__':
    unittest.main()
