"""Subscribed, resumable collection using existing versioned snapshots."""
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from ..common import ConflictError, DomainError, day, json_dumps, now
from . import lixinger, providers
from .metrics import years_before


def subscription(profile):
    # Only explicitly saved profiles are passed here, never the built-in catalog.
    saved = profile.get('subscription')
    if not saved:
        return dict(enabled=True, modules=list(lixinger.DEFAULT_MODULES), history_start=None)
    return dict(saved, modules=[module for module in saved['modules'] if module != 'tracking_funds'])


def collection_modules(profile, modules=None):
    selected = subscription(profile)['modules'] if modules is None else modules
    supported = providers.module_capabilities(profile)
    enabled = [module for module in selected if supported.get(module, {}).get('supported')]
    if 'price' in enabled and supported['volatility']['supported']:
        enabled.append('volatility')
    return enabled


class Collection:
    def __init__(self, research):
        self.research = research

    def preview(self, code, modules, history_start):
        if not modules or len(modules) != len(set(modules)) or any(m not in lixinger.MODULES for m in modules):
            raise DomainError('请选择有效且不重复的采集模块')
        known = providers.catalog_identity(code)
        canonical = known['code'] if known else code.strip().upper()
        if not providers.INDEX_CODE.fullmatch(canonical):
            raise DomainError('请输入理杏仁的指数代码（字母和数字）')
        revision = self.research.revision()
        existing = next((p for p in self.research.profiles() if p['code'] == canonical), None)
        end = providers.cutoff()
        price_date = None
        if existing:
            p = existing
        else:
            p, price_date = lixinger.verify_index(self.research.access, canonical, known, history_start, end)
            if not known:
                p['registration'] = dict(source='lixinger', price_date=price_date, checked_at=now())
        start = str(day(history_start)) if history_start else p['launch']
        if not p['launch'] <= start <= end: raise DomainError('历史起点须介于指数发布日期和已完成交易日之间')
        overseas=p.get('exposure')=='overseas'
        unsupported=[m for m in modules if not providers.module_capabilities(p)[m]['supported']]
        chosen = [m for m in modules if m not in unsupported] if overseas else list(dict.fromkeys(['index_info', *modules]))
        if not chosen: raise DomainError('所选模块的跨市场身份与币种尚未核验')
        calls_by_endpoint = {}; pending = []
        for m in collection_modules(p, chosen):
            if overseas: continue
            if m in ('index_financials', 'stock_valuations', 'financials', 'historical_financials'):
                pending.append(m); continue
            for call in lixinger.request_plan(p, m, start, end):
                e = call['endpoint']; calls_by_endpoint[e] = calls_by_endpoint.get(e, 0)+1
        state = self.research.access.status()
        return dict(profile=p, modules=chosen, history_start=history_start, effective_start=start, end=end,
                    verified_price_date=price_date, initial_sync=existing is None,
                    research_revision=revision, endpoint_budget=[dict(endpoint=e, calls=n,
                    remaining=None) for e, n in sorted(calls_by_endpoint.items())],
                    dependencies_pending=pending, unsupported_modules=unsupported, configured=state['configured'], enabled=state['enabled'],
                    limitation='公司数据仍用已核验来源；指数财报端点需基本信息确认后计划。新订阅保存后立即启动一次全量同步。')

    def save(self, code, modules, history_start, enabled, expected):
        from .updates import index_resources

        if self.research.revision() != expected:
            raise ConflictError('资料或设置已变化，请重新读取并核对；本次未写入')
        if not self.research.writes_allowed():
            raise DomainError('系统处于只读验收或停写状态')
        plan = self.preview(code, modules, history_start)
        p = plan['profile']
        iid = p['provider']+':'+p['code']
        job_id = None
        with self.research.worker_lock, self.research.transaction(expected=expected, bump=True) as db:
            old = db.execute('SELECT data FROM research_indices WHERE id=?', (iid,)).fetchone()
            if not old and not enabled:
                raise DomainError('新增指数须启用并执行首次同步，保存后可暂停')
            value = json.loads(old[0]) if old else dict(p, id=iid, created_at=now())
            previous = subscription(value)
            changed = previous.get('modules') != plan['modules'] or previous.get('history_start') != history_start
            value['subscription'] = dict(enabled=enabled, modules=plan['modules'], history_start=history_start)
            # A changed range invalidates checkpoints, never observations or historical versions.
            if changed: value['collection_generation'] = value.get('collection_generation', 0)+1
            db.execute('INSERT INTO research_indices VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (iid, json_dumps(value)))
            if not old:
                resources = [dict(resource, full=True) for resource in index_resources(iid, collection_modules(value))]
                job_id = self.research.updates.enqueue(db, resources, trigger='index_create')

        if job_id:
            self.research.updates.start()
        return dict(id=iid, subscription=value['subscription'], research_revision=self.research.revision(), job_id=job_id)

    def active(self, iid, module):
        p = self.research.profile(iid); sub = subscription(p)
        if not sub['enabled'] or module not in collection_modules(p): raise DomainError('该指数已暂停或模块未订阅；本次未采集')
        if self.research.updates.stopping.is_set() or not self.research.writes_allowed(): raise DomainError('采集已停止')
        return p

    def status(self, iid):
        from .updates import index_resources

        p = self.research.profile(iid)
        enabled = collection_modules(p)
        resources = index_resources(iid, enabled)
        states = {}
        for m in enabled:
            stage = self.research.module(iid, 'stage.'+m)
            if stage:
                states[m] = {k: stage.get(k) for k in ('phase', 'last_incremental', 'last_reconciled', 'range_start', 'range_end', 'mode', 'difference')}
                states[m].update(completed=len(stage.get('parts', [])), planned=len(stage.get('calls', [])))
        return dict(subscription=subscription(p), modules=states, resources=resources)

    def collect(self, iid, module, end, automatic=False, *, full=False):
        if automatic:
            raise DomainError('指数资料库仅支持手动更新')
        p = self.active(iid, module)
        sub = subscription(p)
        if module in ('stock_valuations', 'financials', 'historical_financials'):
            return self.company_data(iid, module, end, full=full)

        old = self.research.module(iid, module)
        start = sub.get('history_start') or p['launch']
        if p.get('exposure') == 'overseas':
            return self.collect_official(p, module, start, end, old, full)

        state = self.research.module(iid, 'stage.'+module) or {}
        if module == 'historical_memberships':
            start = max(start, str(years_before(end, 10)))
        needs_valuation_backfill = (module == 'valuations' and old
                                    and 'ewpvo' not in old.get('series', {}).get('pe', {}))
        needs_finance_backfill = (module == 'index_financials' and old
                                  and not set(lixinger.FINANCIAL_METRICS).issubset(old.get('metrics', ())))
        needs_margin_backfill = (module == 'market_activity' and old
                                 and not set(lixinger.MARGIN_RATIO_FIELDS).issubset(old.get('margin_ratio_fields', ())))
        full = (full or needs_finance_backfill or needs_margin_backfill or not old or old.get('provider_id') != 'lixinger' or not state.get('last_reconciled')
                or bool(needs_valuation_backfill) or state.get('generation') != p.get('collection_generation', 0))
        if not full and old.get('effective_date'):
            start = max(start, str(day(old['effective_date']) - timedelta(days=15)))
        # Only resume checkpoints for the same mode and configured range.
        if (state.get('phase') == 'collecting' and state.get('generation') == p.get('collection_generation', 0)
                and state['mode'] == ('reconcile' if full else 'incremental')
                and (not needs_finance_backfill or
                     set(lixinger.FINANCIAL_METRICS).issubset(state['calls'][0]['params'].get('metricsList', ())))
                and (not needs_margin_backfill or state.get('margin_ratios'))
                and (not needs_valuation_backfill or
                     'pe_ttm.ewpvo' in state['calls'][0]['params']['metricsList'])):
            start, end = state['range_start'], state['range_end']
        info = (self.research.module(iid, 'index_info') or {}).get('info')
        if module != 'index_info' and not info:
            raise DomainError('请先更新指数基本信息并核验地区、币种及编制方，再采集其他理杏仁模块')
        calls = lixinger.request_plan(p, module, start, end, info)
        if not full and module == 'historical_memberships':
            # Revisit old baskets only during explicit reconciliation.
            last_period=max((b['period'] for b in (old or {}).get('baskets',[])),default='')
            calls = [c for c in calls if c['period'] > last_period]
        identity = hashlib.sha256(json_dumps([module, sub, p.get('collection_generation', 0), start, end, full, calls]).encode()).hexdigest()
        if state.get('phase') != 'collecting' or state.get('request_id') != identity:
            state = dict(request_id=identity, phase='collecting', generation=p.get('collection_generation', 0),
                         range_start=start, range_end=end, mode='reconcile' if full else 'incremental',
                         calls=calls, parts=[], last_incremental=state.get('last_incremental'), last_reconciled=state.get('last_reconciled'))
            if module == 'market_activity':
                state['margin_ratios'] = True
        for original in calls[len(state['parts']):]:
            self.active(iid, module)
            call = deepcopy(original)
            if call['kind'] == 'members':
                weights = lixinger.full_weights(state['parts'][-1]['rows'], call['period'], module=='memberships')
                if weights: call['params']['date'] = weights[0]['date']
            raw = self.research.access.request(call['endpoint'], call['params'], automatic=automatic)
            rows = lixinger.normalize(call, raw, p)
            self.active(iid, module)
            if self.research.profile(iid).get('collection_generation', 0) != state['generation']:
                raise DomainError('采集范围已变化，本次响应未发布')
            state['parts'].append(dict(call=call, rows=rows))
            # Checkpoints survive errors; analysis only reads the final module head.
            self.research.publish(iid, 'stage.'+module, state)
        if not calls:
            if old:return old
            raise DomainError('没有已完成且可查询的历史期间')
        try:
            value = lixinger.assemble(p, module, state['parts'], end)
            value['coverage'] = dict(configured_start=sub.get('history_start'), requested_start=state['range_start'],
                                     requested_end=end, range_limited=bool(sub.get('history_start')), status='collected',
                                     supplier_full_history_verified=False)
            if module == 'memberships':
                check_current_membership(old, value)
            if module == 'valuations':
                from ..waterlevel.catalog import FULL_MARKET_ID
                if iid == FULL_MARKET_ID:
                    validate_full_market_valuation(value, self.research.waterlevel.source('full_market') if not old else None, start)
            value, difference = merge(old, value, full, (start, end))
        except DomainError:
            state['phase']='validation_failed'
            self.research.publish(iid,'stage.'+module,state)
            raise
        if module in ('price','total_return'):
            from ..trading_calendar import no_expected_session
            dates={r['date'] for r in value['rows']};missing=set()
            cursor=day(min(dates));last=day(max(dates))
            while cursor<=last:
                if cursor.year==2026 and cursor.weekday()<5 and str(cursor) not in dates and not no_expected_session('CN',str(cursor-timedelta(days=1)),str(cursor)):
                    missing.add(str(cursor))
                cursor+=timedelta(days=1)
            value['missing_dates']=sorted(missing)
        self.active(iid, module)
        # No await/network between the final subscription check and transactional publication.
        with self.research.store.lock:
            self.active(iid,module)
            if self.research.profile(iid).get('collection_generation', 0) != state['generation']:
                raise DomainError('采集范围已变化，本次响应未发布')
            sid = self.research.publish(iid, module, value)
        state.update(phase='complete', difference=difference, last_incremental=now())
        if full:
            state['last_reconciled'] = now()
        self.research.publish(iid, 'stage.'+module, state)
        return dict(value, snapshot_id=sid, update_difference=difference)

    def collect_official(self, profile, module, start, end, old, full):
        iid = profile['id']
        if module == 'memberships':
            value = providers.constituents(profile, end)
            check_current_membership(old, value)
            difference = None
        elif module in ('price', 'total_return'):
            if not full and old and old.get('effective_date'):
                start = max(start, str(day(old['effective_date']) - timedelta(days=15)))
            # The CSI endpoint includes an artificial start row, so request one extra day.
            request_start = str(day(start) - timedelta(days=1)) if profile['provider'] == 'csi' else start
            value = providers.prices(profile, module, end, start_date=request_start)
            value['rows'] = [row for row in value['rows'] if row['date'] >= start]
            if not value['rows']:
                raise DomainError('官方来源未返回请求范围内的有效日线，保留旧数据')
            value['effective_date'] = value['rows'][-1]['date']
            if old and old['effective_date'] > value['effective_date']:
                raise DomainError('官方来源返回旧日期，保留有效历史')
            previous = old if (old or {}).get('source') == value.get('source') else None
            value, difference = merge(previous, value, full, (start, end))
        else:
            raise DomainError('该跨市场模块尚未核验')

        self.active(iid, module)
        return dict(value, snapshot_id=self.research.publish(iid, module, value), update_difference=difference)

    def company_data(self, iid, module, end, *, full=False):
        """Retain the company-level exception; never substitute index totals for companies."""
        from . import history

        p = self.active(iid, module)
        if p.get('exposure') == 'overseas':
            raise DomainError('跨市场公司财务币种与准则尚未核验')
        members = self.research.module(iid, 'memberships')
        if not members:
            raise DomainError('请先采集有效成分名单')

        if module == 'historical_financials':
            baskets = self.research.module(iid, 'historical_memberships')
            if not baskets:
                raise DomainError('请先采集历史成分名单')
            unique = {m['code']: m for b in baskets['baskets'] for m in b['members']}
            rows = self.shared_companies('financials', list(unique.values()), end, full=full)
            annual, observations, evidence = self.historical_industry_baskets(baskets['baskets'], end, full=full)
            value = dict(
                rows,
                years=history.totals(rows['rows'], annual),
                membership_snapshot_id=baskets['snapshot_id'],
                industry_observations=observations,
                industry_evidence=evidence,
                industry_basis='对应期末前最近7天内的历史估值记录行业；当前可得版本，不声称严格PIT',
            )
        else:
            value = self.shared_companies(module, members['rows'], end, full=full)

        old = self.research.module(iid, module)
        if old and old.get('rows') and not value['rows']:
            raise DomainError('公司来源返回空数据，保留有效历史')
        if old and old.get('collection_cutoff') == end:
            row_key = lambda row: (row['code'], row.get('period', row.get('date')))
            known = {row_key(row) for row in old['rows']}
            fresh = {row_key(row) for row in value['rows']}
            if known - fresh and old.get('collected_codes') == value['collected_codes']:
                raise DomainError('同一截止日的公司数据缺失记录，保留旧快照')

        with self.research.store.lock:
            latest = self.active(iid, module)
            if latest.get('collection_generation', 0) != p.get('collection_generation', 0):
                raise DomainError('采集范围已变化，本次响应未发布')
            sid = self.research.publish(iid, module, value)
        return dict(value, snapshot_id=sid)

    def historical_industry_baskets(self, baskets, end, *, full=False):
        """Enrich annual basket copies with dated observations, preserving source lists."""
        annual = [deepcopy(b) for b in baskets if b['period'].endswith('12-31')]
        known = {}
        observations = []
        evidence = []
        for profile in (() if full else self.research.profiles()):
            cached = self.research.module(profile['id'], 'historical_financials') or {}
            if cached.get('collection_cutoff') == end:
                known.update({(r['period'], r['code']): r for r in cached.get('industry_observations', [])})
                evidence.extend(cached.get('industry_evidence', []))

        for basket in annual:
            period = basket['period']
            missing = [m for m in basket['members'] if (period, m['code']) not in known]
            if missing:
                fresh = providers.historical_industries(missing, period)
                evidence.extend(fresh['evidence'])
                known.update({
                    (period, m['code']): dict(code=m['code'], period=period, date=None, industry=None)
                    for m in missing
                })
                known.update({(period, r['code']): dict(r, period=period) for r in fresh['rows']})
            for member in basket['members']:
                record = known.get((period, member['code']))
                member['industry'] = record.get('industry') if record else ''
                if record:
                    observations.append(record)

        return annual, observations, evidence

    def shared_companies(self, module, members, end, *, full=False):
        """Reuse immutable company observations for the same query cutoff across indices."""
        wanted={m['code'] for m in members}; known=set(); rows={}; evidence=[]; periods=set(); refs=[]
        names=('financials','historical_financials') if module=='financials' else ('stock_valuations',)
        for p in (() if full else self.research.profiles()):
            for name in names:
                cached=self.research.module(p['id'],name)
                if not cached or cached.get('collection_cutoff')!=end: continue
                reusable=wanted & set(cached.get('collected_codes',[])) - known
                if not reusable: continue
                known.update(reusable); refs.append(cached['snapshot_id']); evidence.extend(cached.get('evidence',[])); periods.update(cached.get('periods',[]))
                for row in cached['rows']:
                    if row['code'] in reusable: rows[(row['code'],row.get('period',row.get('date')))]=row
        missing=[m for m in members if m['code'] not in known]
        fresh=(providers.stock_valuations if module=='stock_valuations' else providers.financials)(missing,end) if missing else {}
        for row in fresh.get('rows',[]): rows[(row['code'],row.get('period',row.get('date')))]=row
        periods.update(fresh.get('periods',[])); evidence.extend(fresh.get('evidence',[]))
        dates=[r[k] for r in rows.values() for k in r if k=='date' or k.endswith('_published_at') if r[k]]
        return dict(fresh,rows=list(rows.values()),periods=sorted(periods),evidence=evidence,
                    effective_date=max(dates,default=None),collection_cutoff=end,collected_codes=sorted({r['code'] for r in rows.values()} if module=='stock_valuations' else wanted),source_snapshot_ids=refs)


def check_current_membership(old, new):
    if not old or not old.get('rows'):
        return
    if not new.get('rows') or (old.get('effective_date') and
                               (new.get('effective_date') or '') < old['effective_date']):
        raise DomainError('成分来源返回空数据或旧日期，保留旧快照')
    if old.get('weights_complete') and not new.get('weights_complete'):
        raise DomainError('成分来源缺少已核验的完整权重，保留旧快照')


def validate_full_market_valuation(value, previous, start):
    for metric in ('pe', 'pb'):
        for method in lixinger.METHODS:
            series = value['series'][metric][method]
            rows = series['rows']
            if not rows:
                raise DomainError('A股全指PE/PB口径未完整取得，保留旧快照')
            known = (previous or {}).get('series', {}).get(f'full_market_{metric}_{method}', [])
            missing = {row['date'] for row in known if row['date'] >= start} - {row['date'] for row in rows}
            if missing:
                raise DomainError('A股全指历史较已发布快照缩水，保留旧数据')
            series['rows'] = [dict(date=row['date'], value=row['value']) for row in known if row['date'] < start] + rows


def merge(old, new, full, requested_range=None):
    """Merge revisions only within one provider/metric identity; missing rows never delete history."""
    stats = dict(added=0, revised=0, missing=0)
    old = old or {}
    if old.get('provider_id') != new.get('provider_id'):
        old = {}
    out = deepcopy(new)
    def combine(a, b, key='date'):
        prev = {r[key]: r for r in a}; fresh = {r[key]: r for r in b}
        if len(fresh) != len(b): raise DomainError('分片之间出现重复业务键，未发布')
        if prev and fresh:
            lo, hi = min(fresh), max(fresh)
            if max(prev) > hi and key == 'date':
                raise DomainError('来源返回旧日期，保留有效历史')
            if full and requested_range: lo, hi = requested_range
            missing = (set(prev) if full and not requested_range else {k for k in prev if lo <= k <= hi})-set(fresh)
            stats['missing'] += len(missing)
            if missing: raise DomainError('来源返回历史缩水或中间缺记录，保留旧快照和分片；需核对后重试')
        elif prev and not fresh: raise DomainError('来源返回空数据，保留有效历史')
        stats['added'] += len(set(fresh)-set(prev))
        stats['revised'] += sum(prev[k] != v for k, v in fresh.items() if k in prev)
        prev.update(fresh)
        return [prev[k] for k in sorted(prev)]
    dated_rows=next(iter(new.get('rows',[]) or old.get('rows',[])),{})
    if 'rows' in new and 'date' in dated_rows: out['rows'] = combine(old.get('rows', []), new['rows'])
    if 'activity_rows' in new: out['activity_rows'] = combine(old.get('activity_rows', []), new['activity_rows'])
    if 'series' in new:
        for metric, methods in new['series'].items():
            if isinstance(methods, list): out['series'][metric] = combine(old.get('series', {}).get(metric, []), methods)
            else:
                for method, series in methods.items():
                    out['series'][metric][method]['rows'] = combine(old.get('series', {}).get(metric, {}).get(method, {}).get('rows', []), series['rows'])
    if 'baskets' in new:
        known={b['period'] for b in old.get('baskets',[]) if b.get('members')}
        if any(b['period'] in known and not b.get('members') for b in new['baskets']):raise DomainError('已知历史名单变为缺失或冲突；保留旧快照，需核对后重试')
        out['baskets'] = combine(old.get('baskets', []), new['baskets'], 'period')
    return out, stats
