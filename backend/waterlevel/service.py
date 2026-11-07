"""Local-only reads; source collection shares the application's durable update worker."""
import hashlib
import json
import secrets
from copy import deepcopy

from ..common import ConflictError, DomainError, day, dec, ds, json_dumps, now, today
from ..research.collection import subscription
from ..research.snapshots import encode, decode
from . import sources, validation
from .catalog import FULL_MARKET_FIELDS, FULL_MARKET_ID, GROUPS, INDICES, LIMITATIONS, SERIES, SPLIT_GROUPS
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

    def collect(self, group, asof, *, full=False, series=None):
        previous = self.source(group)
        options = {'access': self.research.access} if group == 'full_market' else {}
        if full:
            options['full'] = True
        if series:
            options['series'] = series
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
        updated = [value['series'][series]] if series else value['series'].values()
        latest = max((row['date'] for rows in updated for row in rows if row['value'] is not None), default=None)
        return [dict(scope='market', resource=dict(scope='market', group=group), name=SERIES[series][1] if series else GROUPS[group]['label'],
                     status='complete', snapshot_id=sid, date=latest, difference=value.get('difference'))]

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

    def update(self, groups, request_id, *, full=False, indices=()):
        if any(iid not in dict(INDICES) for iid in indices):
            raise DomainError('市场水位指数范围无效')
        resources = [dict(scope='market', group=group) for group in groups]
        resources.extend(dict(scope='valuations', target=iid, module='valuations') for iid in indices)
        if full:
            resources = [dict(resource, full=True) for resource in resources]
        return self.research.run(dict(scopes=list(dict.fromkeys(r['scope'] for r in resources)),
                                      request_id=request_id, resources=resources))

    @staticmethod
    def _failures(jobs):
        failures = {}
        for job in jobs:
            for item in reversed(job.get('items', [])):
                resource = item.get('resource', {})
                key = resource.get('group')
                if resource.get('module', item.get('module')) == 'valuations':
                    key = resource.get('target', item.get('target'))
                keys = (resource['series'],) if resource.get('series') else SPLIT_GROUPS.get(key, (key,))
                for entry in keys:
                    if entry and entry not in failures:
                        failures[entry] = item.get('error') if item.get('status') == 'error' else None
        return failures

    def data_sources(self):
        """Read update targets and observed dates without computing charts."""
        with self.store.lock:
            failures = self._failures(self.research.updates.jobs())
            profile = next((p for p in self.research.profiles() if p['id'] == FULL_MARKET_ID), None)
            shared = self.research.module(FULL_MARKET_ID, 'valuations') if profile else None
            items = []
            for group, info in GROUPS.items():
                saved = self.source(group) or {}
                if group == 'full_market' and (shared or {}).get('provider_id') == 'lixinger':
                    rows = full_market_series(shared)
                    if all(rows.values()):
                        saved = dict(shared, series=rows)
                for key in SPLIT_GROUPS.get(group, (None,)):
                    error_key = FULL_MARKET_ID if group == 'full_market' and profile else key or group
                    updatable = group != 'full_market' or not profile or valuation_enabled(profile)
                    items.append(source_entry(group, key, info, saved, failures.get(error_key), updatable))
            return dict(items=items, research_revision=self.research.revision())

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
            observed = {FULL_MARKET_ID, *(iid for iid, _ in INDICES)}
            index_data = {iid: self.research.module(iid, 'valuations') for iid in observed & profiles.keys()}
        value = self._assemble(method, asof, revision, source_data, index_data, profiles, failures)
        with self.store.lock:
            if self.research.revision() == revision:
                self.cache[key] = deepcopy(value)
                while len(self.cache) > 8:
                    self.cache.pop(next(iter(self.cache)))
        return value

    def _assemble(self, method, asof, revision, source_data, index_data, profiles, failures):
        sources_list, groups, items, raw_series, scoring = [], [], [], {}, []
        all_index = index_data.get(FULL_MARKET_ID) or {}
        all_series = full_market_series(all_index)
        shared = all_index.get('provider_id') == 'lixinger' and all(all_series.values())
        if FULL_MARKET_ID in failures:
            failures = dict(failures, full_market=failures[FULL_MARKET_ID])

        for group, definition in GROUPS.items():
            use_shared = group == 'full_market' and shared
            source = all_index if use_shared else source_data[group]
            error = next((failures[key] for key in SPLIT_GROUPS.get(group, (group,)) if failures.get(key)), None)
            status = 'error' if error else 'available' if source else 'missing'
            checked_at = source.get('fetched_at' if use_shared else 'checked_at') if source else None
            updatable = group != 'full_market' or FULL_MARKET_ID not in profiles or valuation_enabled(profiles[FULL_MARKET_ID])
            groups.append(dict(group=group, **definition, status=status, error=error, updatable=updatable,
                               notes=(source or {}).get('notes', []), checked_at=checked_at))
            if source:
                identity = dict(kind='index', index_id=FULL_MARKET_ID) if use_shared else dict(kind='market', group=group)
                sources_list.append(dict(identity, snapshot_id=source['snapshot_id'], sha256=source['sha256'],
                                         publisher=definition['publisher'], evidence=source['evidence'], checked_at=checked_at))
            raw_series.update(all_series if use_shared else (source or {}).get('series', {}))
        for key, (group, label, unit, frequency) in SERIES.items():
            rows = raw_series.get(f'{key}_{method}' if group == 'full_market' else key, [])
            stats = analyze(rows, asof, positive=group in ('full_market', 'composites'),
                            stale_days=80 if frequency == 'monthly' else 14, frequency=frequency)
            if failures.get(key if group in SPLIT_GROUPS else group) and stats['status'] == 'available':
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
            if any(failures.get(name if SERIES[name][0] in SPLIT_GROUPS else SERIES[name][0]) for name in (left, right)):
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
                                subscribed=iid in profiles, updatable=valuation_enabled(profiles.get(iid)),
                                source='理杏仁' if valid_source else None,
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


def source_entry(group, key, info, saved, error, updatable):
    resource = dict(scope='market', group=group)
    if key:
        resource['series'] = key
    dates = []
    for name, rows in saved.get('series', {}).items():
        if key and name != key:
            continue
        date = next((r['date'] for r in reversed(rows) if r['value'] is not None), None)
        if date:
            if name in SERIES:
                _, label, _, frequency = SERIES[name]
            else:
                field, method = FULL_MARKET_FIELDS[name].split('.')
                label = f'{"PE TTM" if field == "pe_ttm" else "PB"} · {method}'
                frequency = 'daily'
            dates.append(dict(label=label, date=date, frequency=frequency))
    if saved.get('calendar'):
        dates = [dict(label='日历年度', date=str(saved['calendar']['year']), frequency='annual')]
    return dict(id=key or group, label=SERIES[key][1] if key else info['label'],
                publisher=info['publisher'], resource=resource, dates=dates, error=error, updatable=updatable,
                snapshot_id=saved.get('snapshot_id') if dates else None,
                evidence=saved.get('evidence', [])[-5:] if dates else [])


def full_market_series(snapshot):
    return {f'full_market_{metric}_{method}': snapshot.get('series', {}).get(metric, {}).get(method, {}).get('rows', [])
            for metric in ('pe', 'pb') for method in METHODS}


def valuation_enabled(profile):
    return bool(profile and subscription(profile)['enabled'] and 'valuations' in subscription(profile)['modules'])
