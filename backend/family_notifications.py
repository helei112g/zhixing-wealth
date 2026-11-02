"""Local macOS scheduling. No notification preference is inferred from a restore."""
from __future__ import annotations
import hashlib
import json
import platform
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .common import DomainError, now, json_dumps
from .family_domain import read_state, schedule_projection
from .environment import PORTS

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('Asia/Shanghai')


def notification_plan(rows, current=None):
    current = current or datetime.now(TZ)
    end = current + timedelta(days=366)
    plan = []
    for row in rows:
        if row['status'] != 'pending' or row.get('kind') == 'dividend': continue
        due = datetime.fromisoformat(row['due_date']).replace(hour=9, tzinfo=TZ)
        dates = [(offset, due - timedelta(days=offset)) for offset in (7, 1, 0)]
        if row.get('snooze_until'):
            resume = datetime.fromisoformat(row['snooze_until']).replace(hour=9, tzinfo=TZ)
            dates = [(offset, date) for offset, date in dates if date >= resume]
            dates.append(('snooze', resume))
        seen = set()
        for offset, date in dates:
            if not current < date <= end or date in seen: continue
            seen.add(date)
            identity = f"family:{row['id']}:{row.get('version', 1)}:{offset}:{date.isoformat()}"
            plan.append({'id': hashlib.sha256(identity.encode()).hexdigest(), 'schedule_id': row['id'], 'schedule_version': row.get('version', 1),
                         'at': date.isoformat(), 'title': '知行财富 · 到期提醒',
                         'reminder_id': 'family:'+row['id'], 'body': '有一项还款或保单事项即将到期，点击打开提醒中心。'})
    return sorted(plan, key=lambda r: (r['at'], r['id']))


def next_notification(desired):
    """One OS slot; combine simultaneous reminders without losing their identities."""
    if not desired:
        return [], []
    ordered = sorted(desired, key=lambda r: (r['at'], r['id']))
    members = [r for r in ordered if r['at'] == ordered[0]['at']]
    request = dict(members[0])
    if len(members) > 1:
        identity = 'batch:' + json_dumps([r['id'] for r in members])
        request = dict(id=hashlib.sha256(identity.encode()).hexdigest(), at=members[0]['at'],
                       schedule_id='', schedule_version=1, reminder_id='', route='reminders',
                       title='知行财富 · 事项提醒', body='有多项事项需要关注，点击打开提醒中心。')
    request['schedule_keys'] = [r['schedule_id'] + ':' + str(r['schedule_version']) for r in members]
    return [request], members


def batch_result(result, queued, members):
    """Map the OS request's acceptance/error back to each business reminder."""
    if not queued:
        return result
    identity = queued[0]['id']
    mapped = dict(result)
    for key in ('pending_ids', 'delivered_ids'):
        mapped[key] = [r['id'] for r in members] if identity in result.get(key, []) else []
    errors = [e for e in result.get('request_errors', []) if e['id'] == identity]
    mapped['request_errors'] = [dict(e, id=r['id']) for e in errors for r in members]
    return mapped


def missing_details(desired, rows, result, current):
    accepted = set(result.get('pending_ids', [])) | set(result.get('delivered_ids', []))
    sources = {r['id']: r for r in rows}
    failures = {r['id']: r for r in result.get('request_errors', [])}
    details = []
    for request in desired:
        if request['id'] in accepted or datetime.fromisoformat(request['at']) <= current:
            continue
        row = sources.get(request['schedule_id'], {})
        failure = failures.get(request['id'], {})
        reason = failure.get('message') or '系统未在待发或已送达队列中返回此提醒，未提供具体原因。'
        if result.get('authorization') != 'authorized':
            reason = 'macOS 通知未授权，请打开系统通知设置并允许知行财富通知。'
        details.append(dict(id=request['id'], schedule_id=request['schedule_id'],
                            item_id=row.get('item_id'), portfolio_id=row.get('portfolio_id'),
                            name=row.get('name') or '分红待核对', kind=row.get('kind'),
                            due_date=row.get('due_date'), at=request['at'], reason=reason,
                            error_domain=failure.get('domain'), error_code=failure.get('code')))
    return details


class FamilyNotifications:
    def __init__(self, store, family, environment):
        self.store, self.family, self.environment = store, family, environment
        self.directory = store.directory / '.native-notifications'
        self.preferences = self.directory / 'preferences.json'
        self.result_path = self.directory / 'status.json'
        self.app = self.directory / 'FamilyNotifications.app'
        self.lock = threading.RLock()
        self.dirty = True
        self.prepared = False
        self.worker = None

    def _read(self, path):
        try: return json.loads(path.read_text())
        except (FileNotFoundError, ValueError): return {}

    def _save(self, path, value):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_suffix('.tmp')
        temp.write_text(json_dumps(value)); temp.chmod(0o600); temp.replace(path)

    def status(self):
        enabled = bool(self._read(self.preferences).get('enabled'))
        result = self._read(self.result_path)
        return dict(result, enabled=enabled, supported=platform.system() == 'Darwin',
                    environment=self.environment, authorization=result.get('authorization', 'not_requested'),
                    pending_count=len(result.get('pending_ids', [])),
                    note='每次仅安排最近一条，同一时刻的事项合并提醒；后续排程需要本机服务运行。服务停止后仅保留已登记的一条。勿扰或关机可能影响通知；页面显示未来35天的到期提醒，分红仍需在组合中核对。')

    def _helper(self, command, requests=None, active_schedules=None):
        if platform.system() != 'Darwin': raise DomainError('系统通知当前仅支持 macOS')
        if not self.app.exists():
            raise DomainError('通知组件尚未构建，请先启用系统通知')
        request = self.directory / (uuid.uuid4().hex + '.request.json')
        output = request.with_suffix('.result.json')
        request.write_text(json_dumps({'command': command, 'requests': requests or [], 'active_schedules': active_schedules or []}))
        request.chmod(0o600)
        try:
            subprocess.run(['/usr/bin/open', '-W', '-n', str(self.app), '--args', str(request)],
                           capture_output=True, timeout=100 if command == 'authorize' else 25, check=True)
            result = json.loads(output.read_text())
            if result.get('error'): raise DomainError(result['error'])
            return result
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise DomainError('macOS 通知组件未完成响应，请检查系统通知权限后重试') from exc
        finally:
            request.unlink(missing_ok=True); output.unlink(missing_ok=True)

    def _build(self):
        from scripts.build_notifications import build
        build(ROOT, self.directory, self.store.directory, self.environment, PORTS[self.environment], sys.executable)

    def invalidate(self):
        self.dirty = True
        if self._read(self.preferences).get('enabled'):
            # Every committed mutation queues a reconciliation. The native lock serializes
            # them, including a write that races with the preceding worker's final return.
            self.worker = threading.Thread(target=self.reconcile, kwargs={'force': True}, name='family-notifications', daemon=False)
            self.worker.start()

    def dividend_rows(self, state):
        key = (state['revision'], now()[:10])
        if getattr(self,'dividend_cache_key',None)==key: return list(self.dividend_cache)
        from .reminders import dividend_reminders
        rows = [dict(id=r['fingerprint'],reminder_id=r['id'],kind='dividend',portfolio_id=r['portfolio_id'],version=1,status='pending')
                for r in dividend_reminders(self.store,state)]
        self.dividend_cache_key=key;self.dividend_cache=rows
        return list(rows)

    def reconcile(self, force=False):
        with self.lock:
            if not self._read(self.preferences).get('enabled'): return self.status()
            old = self._read(self.result_path)
            try:
                if not self.prepared:
                    self._build(); self.prepared = True
                with self.store.connection() as db:
                    rows = schedule_projection(read_state(db), now()[:10], 366)
                    state = self.store.state(db)
                    rows += self.dividend_rows(state)
                    source_revision = state['revision']
                # Building the helper and projecting the ledger may take seconds.
                # Immediate requests must use a fresh clock after those operations.
                current = datetime.now(TZ)
                desired = notification_plan(rows, current)
                imminent = (datetime.fromisoformat(desired[0]['at']) if desired else None)
                if imminent and imminent > current+timedelta(seconds=45):
                    imminent = None
                # Preserve a test long enough to display, but let a newly added
                # imminent business reminder take over before its deadline.
                if old.get('test_pending_until') and current < datetime.fromisoformat(old['test_pending_until']) and not imminent:
                    return self.status()
                dispatched = old.get('dividend_dispatches', {})
                active_dividends = {r['id'] for r in rows if r.get('kind') == 'dividend'}
                dispatched = {k:v for k,v in dispatched.items() if k in active_dividends}
                for row in rows:
                    if row.get('kind') != 'dividend': continue
                    at = dispatched.setdefault(row['id'], (current+timedelta(seconds=5)).replace(microsecond=0).isoformat())
                    if datetime.fromisoformat(at) > current:
                        # Do not evict a timed reminder just before it fires: the
                        # next five-second tick could already be past its deadline.
                        if imminent:
                            at = max(datetime.fromisoformat(at), imminent+timedelta(seconds=5)).isoformat()
                            dispatched[row['id']] = at
                        desired.append(dict(id=row['id'],schedule_id=row['id'],schedule_version=1,at=at,
                                            route='reminders',reminder_id=row.get('reminder_id',''),portfolio_id=row['portfolio_id'],
                                            title='知行财富 · 分红提醒',body='有分红资料需要核对，点击打开提醒中心核对。'))
                queued, members = next_notification(desired)
                fingerprint = hashlib.sha256(json_dumps(desired).encode()).hexdigest()
                if not force and not self.dirty and old.get('fingerprint') == fingerprint and old.get('checked_at') and (datetime.now(TZ) - datetime.fromisoformat(old['checked_at'])).total_seconds() < 45:
                    return self.status()
                # Requery the OS even without mutations: permissions may change in System Settings.
                active_schedules = [r['id'] + ':' + str(r['version']) for r in rows if r['status'] == 'pending']
                result = self._helper('sync', queued, active_schedules)
                with self.store.connection() as db:
                    latest_revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                mapped = batch_result(result, queued, members)
                accepted = set(mapped.get('pending_ids', [])) | set(mapped.get('delivered_ids', []))
                missing = missing_details(members, rows, mapped, datetime.now(TZ))
                covered = [r['at'] for r in members if r['id'] in accepted]
                previous = old.get('queued_request') or {}
                replaced = (set(previous.get('dividend_ids', []))
                            if previous.get('id') in result.get('removed_pending_ids', []) else set())
                # Only selected/accepted dividends are dispatched. Future entries
                # replaced by an earlier reminder must be eligible again.
                dispatches = {k:v for k,v in dispatched.items()
                              if k in accepted or (k in old.get('dividend_dispatches', {})
                                  and datetime.fromisoformat(v) <= current and k not in replaced)}
                result.update(checked_at=now(), scheduling_mode='single_next', desired_count=len(desired),
                              selected_count=len(members), deferred_count=len(desired)-len(members),
                              next_notification_at=covered[0] if covered else None,
                              missing_count=len(missing), missing_requests=missing,
                              coverage_until=max(covered) if covered else None, fingerprint=fingerprint,
                              queued_request=(dict(id=queued[0]['id'], at=queued[0]['at'],
                                                   dividend_ids=sorted(accepted & active_dividends)) if covered else None),
                              dividend_dispatches=dispatches,
                              error=(f'{len(missing)} 条提醒未成功排入系统通知队列，请查看下方事项并重试排程' if missing else None))
                if result.get('authorization') != 'authorized':
                    result['error'] = 'macOS 通知未授权；请在系统设置中允许知行财富通知'
                elif result.get('alerts') == 1:
                    result['error'] = 'macOS 已关闭通知横幅；请在系统设置中检查提醒方式'
                self.dirty = latest_revision != source_revision
                if self.dirty:
                    result['error'] = '计划在排程期间发生变化，正在重新核对'
                self._save(self.result_path, result)
            except DomainError as exc:
                result = self._read(self.result_path)
                result.update(error=str(exc), checked_at=now())
                self._save(self.result_path, result)
            return self.status()

    def action(self, action):
        with self.lock:
            if action == 'open_settings':
                if platform.system() != 'Darwin': raise DomainError('系统通知当前仅支持 macOS')
                try:
                    subprocess.run(['/usr/bin/open', 'x-apple.systempreferences:com.apple.Notifications-Settings.extension'], check=True, timeout=10, capture_output=True)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise DomainError('无法打开系统通知设置，请在 macOS 系统设置中选择“通知”') from exc
                return dict(self.status(), settings_opened=True)
            if action == 'enable':
                self._build()
                self.prepared = True
                result = self._helper('authorize')
                self._save(self.preferences, {'enabled': True})
                self._save(self.result_path, result)
                return self.reconcile(True)
            if action == 'disable':
                if self.app.exists(): self._save(self.result_path, self._helper('cancel'))
                self._save(self.preferences, {'enabled': False})
                return self.status()
            if action == 'refresh': return self.reconcile(True)
            if action == 'test':
                if not self._read(self.preferences).get('enabled'): raise DomainError('请先启用系统通知')
                old = self._read(self.result_path)
                previous = old.get('queued_request') or {}
                current = datetime.now(TZ)
                if previous.get('at') and current <= datetime.fromisoformat(previous['at']) <= current+timedelta(seconds=45):
                    raise DomainError('最近一条业务提醒即将触发，请稍后重试测试通知，以免覆盖该提醒')
                try:
                    result = self._helper('test')
                    if not result.get('test_id') or result['test_id'] not in set(result.get('pending_ids', [])) | set(result.get('delivered_ids', [])):
                        raise DomainError('测试通知未出现在系统队列中，请检查通知权限后重试')
                except DomainError:
                    # The helper may have removed the former request before an
                    # add failure. Restore business scheduling, then report it.
                    self.dirty = True
                    self.reconcile(True)
                    raise
                revoked = (set(previous.get('dividend_ids', []))
                           if previous.get('id') in result.get('removed_pending_ids', []) else set())
                old.update(result, queued_request=None, error=None, missing_count=0, missing_requests=[],
                           selected_count=0, deferred_count=old.get('desired_count', 0),
                           coverage_until=None, next_notification_at=None,
                           dividend_dispatches={k:v for k,v in old.get('dividend_dispatches', {}).items() if k not in revoked},
                           test_pending_until=(datetime.now(TZ)+timedelta(seconds=10)).isoformat())
                self._save(self.result_path, old)
                self.dirty = True
                return dict(self.status(), test_scheduled=True)
            raise DomainError('未知通知操作')
