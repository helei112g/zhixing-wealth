"""Local-only reads; source collection shares the application's durable update worker."""
import hashlib
import json
import secrets
from copy import deepcopy

from ..common import ConflictError, DomainError, day, dec, ds, json_dumps, now, today
from ..research.snapshots import encode, decode
from . import sources, validation
from .catalog import GROUPS, INDICES, LIMITATIONS, SERIES
from .metrics import METHODS, METHOD_VERSION, analyze, comparison, difference, earnings_spread, score


class WaterlevelService:
    def __init__(self, research):
        self.research = research
        self.store = research.store
        self.cache = {}

    def source(self, group):
        with self.store.connection() as db:
            row = db.execute('''SELECT s.id,s.sha256,s.data FROM market_heads h
                                JOIN market_snapshots s ON s.id=h.snapshot_id WHERE h.group_id=?''', (group,)).fetchone()
        if not row:
            return None
        value = decode(row[2], row[1])
        if value.get('publisher') != GROUPS[group]['publisher']:
            return None
        return dict(value, snapshot_id=row[0], sha256=row[1])

    def collect(self, group, asof):
        previous = self.source(group)
        options = {'access': self.research.access} if group == 'full_market' else {}
        value = sources.collect(group, asof, previous, self.research.updates.stopping.is_set, **options)
        validation.source(value)
        if self.research.updates.stopping.is_set():
            raise DomainError('市场更新已中断，未发布新快照')
        if previous:
            for key, rows in value['series'].items():
                old_rows = previous['series'].get(key, [])
                if old_rows and rows[-1]['date'] < old_rows[-1]['date']:
                    raise DomainError('来源返回旧日期，保留有效历史')
        sid = secrets.token_hex(16)
        sha, raw = encode(value)
        with self.research.transaction(bump=True) as db:
            if db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] > 400 * 1024 * 1024:
                raise DomainError('数据库接近备份容量限制，保留原数据')
            db.execute('INSERT INTO market_snapshots VALUES (?,?,?,?,?)', (sid, group, now(), sha, raw))
            db.execute('''INSERT INTO market_heads VALUES (?,?) ON CONFLICT(group_id)
                          DO UPDATE SET snapshot_id=excluded.snapshot_id''', (group, sid))
        latest = max((rows[-1]['date'] for rows in value['series'].values()), default=None)
        return [dict(scope='market', resource=dict(scope='market', group=group), name=GROUPS[group]['label'],
                     status='complete', snapshot_id=sid, date=latest)]

    def settings(self):
        with self.store.connection() as db:
            row = db.execute("SELECT data FROM market_settings WHERE id='settings'").fetchone()
            revision = self.research.revision('settings_revision')
        value = json.loads(row[0]) if row else dict(automatic=False, groups=list(GROUPS))
        return dict(value, automatic=False, expected_settings_revision=revision,
                    policies=[dict(group=key, **entry) for key, entry in GROUPS.items()])

    def save_settings(self, body):
        value = {key: body[key] for key in ('automatic', 'groups')}
        validation.settings(value)
        if value['automatic']:
            raise DomainError('市场水位仅支持手动更新')
        with self.research.transaction('settings_revision', body['expected_settings_revision'], bump=True) as db:
            db.execute("INSERT INTO market_settings VALUES ('settings',?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (json_dumps(value),))
        return self.settings()

    def update(self, groups, request_id):
        return self.research.run(dict(scopes=['market'], request_id=request_id,
                                      resources=[dict(scope='market', group=group) for group in groups]))

    @staticmethod
    def _failures(jobs):
        failures = {}
        for job in jobs:
            for item in reversed(job.get('items', [])):
                resource = item.get('resource', {})
                key = resource.get('group')
                if resource.get('module', item.get('module')) == 'valuations':
                    key = resource.get('target', item.get('target'))
                if key and key not in failures:
                    failures[key] = item.get('error') if item.get('status') == 'error' else None
        return failures

    def status(self):
        with self.store.lock:
            failures = self._failures(self.research.updates.jobs())
            return dict(research_revision=self.research.revision(), updating=self.research.updating(),
                        errors_version=hashlib.sha256(json_dumps(failures).encode()).hexdigest())

    def dashboard(self, method='mcw', asof=None):
        if method not in METHODS:
            raise DomainError('市场估值方法只支持mcw、ew、ewpvo、median')
        asof = str(day(asof)) if asof else str(today())
        if asof > str(today()):
            raise DomainError('市场研究截止日不能晚于今天')
        with self.store.lock:
            revision = self.research.revision()
            recent = self.research.updates.jobs()
            failures = self._failures(recent)
            key = json_dumps([revision, method, asof, failures])
            if key in self.cache:
                return deepcopy(self.cache[key])
            source_data = {group: self.source(group) for group in GROUPS}
            profiles = {profile['id']: profile for profile in self.research.profiles()}
            index_data = {iid: self.research.module(iid, 'valuations') for iid, _ in INDICES if iid in profiles}
        value = self._assemble(method, asof, revision, source_data, index_data, profiles, failures)
        with self.store.lock:
            if self.research.revision() == revision:
                self.cache[key] = deepcopy(value)
                while len(self.cache) > 8:
                    self.cache.pop(next(iter(self.cache)))
        return value

    def _assemble(self, method, asof, revision, source_data, index_data, profiles, failures):
        sources_list, groups, items, raw_series, scoring = [], [], [], {}, []
        for group, definition in GROUPS.items():
            source = source_data[group]
            status = 'error' if failures.get(group) else 'available' if source else 'missing'
            groups.append(dict(group=group, **definition, status=status, error=failures.get(group),
                               notes=(source or {}).get('notes', []),
                               checked_at=source.get('checked_at') if source else None))
            if source:
                sources_list.append(dict(kind='market', group=group, snapshot_id=source['snapshot_id'],
                                         sha256=source['sha256'], publisher=source['publisher'], evidence=source['evidence'],
                                         checked_at=source['checked_at']))
            raw_series.update((source or {}).get('series', {}))
        for key, (group, label, unit, frequency) in SERIES.items():
            rows = raw_series.get(f'{key}_{method}' if group == 'full_market' else key, [])
            stats = analyze(rows, asof, positive=group in ('full_market', 'composites'),
                            stale_days=80 if frequency == 'monthly' else 14, frequency=frequency)
            if failures.get(group) and stats['status'] == 'available':
                stats['status'] = 'source_error'
            latest = next((row for row in reversed(rows) if row['date'] <= asof), {})
            items.append(dict(id=key, label=label, group=group, unit=unit, statistics=stats,
                              published_at=latest.get('published_at'), source_url=latest.get('source_url')))
            if key == 'cn_10y':
                scoring.append(dict(id=key, label=label, category='rates', inverse=True, statistics=stats))

        derived = [
            ('cn_term', '中国国债10Y−2Y期限利差', 'cn_10y', 'cn_2y', 'rates', '百分点', 1),
            ('us_term', '美国国债10Y−2Y期限利差', 'us_10y', 'us_2y', 'rates', '百分点', 1),
            ('repo_spread', 'FR007−FDR007', 'fr007', 'fdr007', 'repo', 'BP', 100),
            ('credit_spread', 'AAA 3Y中票−3Y国债信用利差', 'aaa_3y', 'gov_3y', 'credit', 'BP', 100),
            ('money_gap', 'M2−M1同比剪刀差', 'm2_yoy', 'm1_yoy', 'money', '百分点', 1),
        ]
        for key, label, left, right, group, unit, factor in derived:
            rows = difference(raw_series.get(left, []), raw_series.get(right, []), factor=factor)
            stats = analyze(rows, asof, stale_days=80 if key == 'money_gap' else 14,
                            frequency='monthly' if key == 'money_gap' else 'daily')
            if failures.get(SERIES[left][0]) or failures.get(SERIES[right][0]):
                stats['status'] = 'source_error' if rows else 'missing'
            items.append(dict(id=key, label=label, group=group, unit=unit, statistics=stats,
                              formula=f'({left} - {right}) × {factor}', same_date_only=True))
            if key == 'credit_spread':
                scoring.append(dict(id=key, label=label, category='credit', inverse=True, statistics=stats))

        indices = []
        for iid, label in INDICES:
            source = index_data.get(iid)
            valid_source = source if (source or {}).get('provider_id') == 'lixinger' else None
            series = (valid_source or {}).get('series', {})
            if valid_source:
                sources_list.append(dict(kind='index', index_id=iid, snapshot_id=source['snapshot_id'],
                                         sha256=source['sha256'], publisher='理杏仁', evidence=source['evidence'],
                                         checked_at=source['fetched_at'], error=failures.get(iid)))
            metrics = {}
            for metric in ('pe', 'pb', 'dividend_yield'):
                data = series.get(metric, {}).get(method, {})
                metrics[metric] = analyze(data.get('rows', []), asof, positive=metric != 'dividend_yield')
                if failures.get(iid) and metrics[metric]['status'] == 'available':
                    metrics[metric]['status'] = 'source_error'
                if (metric == 'pe' and iid in ('csi:000300', 'csi:000905', 'csi:000852')
                        or metric == 'pb' and iid in ('csi:000300', 'csi:000905')):
                    fixed = series.get(metric, {}).get('mcw', {})
                    fixed_stats = analyze(fixed.get('rows', []), asof, positive=True)
                    if failures.get(iid) and fixed_stats['status'] == 'available':
                        fixed_stats['status'] = 'source_error'
                    scoring.append(dict(id=iid + ':' + metric, label=label + ' ' + metric.upper(),
                                        category='equity', method='mcw', statistics=fixed_stats))
            indices.append(dict(id=iid, label=label, method=method, metrics=metrics,
                                subscribed=iid in profiles, source='理杏仁' if valid_source else None,
                                reason=None if valid_source else '尚未取得理杏仁估值，请在指数资料库添加并采集估值模块'))
        statistics = {item['id']: item['statistics'] for item in items}
        for metric in ('pe', 'pb'):
            fixed = analyze(raw_series.get(f'full_market_{metric}_median', []), asof, positive=True)
            if failures.get('full_market') and fixed['status'] == 'available':
                fixed['status'] = 'source_error'
            scoring.append(dict(id='full_market:' + metric, label='理杏仁 A股全指 ' + metric.upper(),
                                category='equity', method='median', statistics=fixed))
        pe = (index_data.get('csi:000300') or {}).get('series', {}).get('pe', {}).get('mcw', {}).get('rows', [])
        if (index_data.get('csi:000300') or {}).get('provider_id') != 'lixinger':
            pe = []
        erp = earnings_spread(pe, raw_series.get('cn_10y', []))
        erp_stats = analyze(erp, asof)
        if (failures.get('csi:000300') or failures.get('china_rates')) and erp_stats['status'] == 'available':
            erp_stats['status'] = 'source_error'
        items.append(dict(id='erp', label='沪深300简化ERP（mcw）', group='cross_asset', unit='百分点',
                          statistics=erp_stats, formula='100 / PE(mcw) − 中国10Y国债收益率(%)', same_date_only=True))
        inverse = [dict(date=row['date'], value=None if row['value'] is None or dec(row['value']) <= 0
                        else ds(100 / dec(row['value']))) for row in raw_series.get('cn_10y', [])]
        inverse_stats = analyze(inverse, asof, positive=True)
        if failures.get('china_rates') and inverse_stats['status'] == 'available':
            inverse_stats['status'] = 'source_error'
        items.append(dict(id='bond_inverse', label='10Y国债收益率倒数（参考）', group='cross_asset', unit='倍',
                          statistics=inverse_stats, formula='100 / 中国10Y国债收益率(%)；不是债券PE'))
        comparisons = {metric: comparison(statistics['full_market_' + metric]['chart'], inverse_stats['chart'],
                                         statistics['sh_composite']['chart'], statistics['sz_composite']['chart'], asof=asof)
                       for metric in ('pe', 'pb')}
        scores = score(scoring)
        names = {'equity': '股票', 'rates': '利率债', 'credit': '信用债'}
        evidence = [f'{names[key]}水位 {format(dec(entry["score"]), ".2f") if entry["score"] is not None else "缺失"}，有效指标 {entry["included"]}/{entry["expected"]}。'
                    for key, entry in scores['categories'].items()]
        analysis_id = hashlib.sha256(json_dumps([METHOD_VERSION, method, asof,
                                                [(s['kind'], s['snapshot_id']) for s in sources_list], sorted(profiles), failures]).encode()).hexdigest()
        return dict(analysis_id=analysis_id, research_revision=revision, method=method, method_version=METHOD_VERSION,
                    asof=asof, indices=indices, series=items, comparisons=comparisons,
                    scores=scores, sources=sources_list, groups=groups,
                    limitations=LIMITATIONS, commentary=dict(origin='rule', title='历史水位规则摘要', evidence=evidence,
                    overall=f'综合水位覆盖 {scores["overall"]["included"]}/3 个大类。请结合有效日期、样本和缺口阅读。',
                    counterpoints=[text for text in LIMITATIONS[:2]], watchlist=['关注下一次正式数据发布及缺失大类补齐后的覆盖变化。']))

    def freeze(self, body):
        with self.store.lock:
            value = self.dashboard(body['method'], body['asof'])
            if value['analysis_id'] != body['analysis_id']:
                raise ConflictError('市场研究输入已变化，请重新读取核对')
            digest, raw = encode(value)
            with self.research.transaction(expected=body['expected_research_revision']) as db:
                if db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] > 400 * 1024 * 1024:
                    raise DomainError('数据库接近备份容量限制，未保存新的研究报告')
                db.execute('INSERT OR IGNORE INTO market_reports VALUES (?,?,?,?)', (value['analysis_id'], now(), digest, raw))
        return self.frozen(value['analysis_id'])

    def reports(self):
        with self.store.connection() as db:
            rows = db.execute('SELECT id,created_at,sha256 FROM market_reports ORDER BY rowid DESC LIMIT 50').fetchall()
        return dict(items=[dict(id=row[0], created_at=row[1], sha256=row[2]) for row in rows], limit=50)

    def frozen(self, sid):
        with self.store.connection() as db:
            row = db.execute('SELECT created_at,sha256,data FROM market_reports WHERE id=?', (sid,)).fetchone()
        if not row:
            raise DomainError('市场研究快照不存在')
        return dict(snapshot_id=sid, created_at=row[0], sha256=row[1], data=decode(row[2], row[1]))
