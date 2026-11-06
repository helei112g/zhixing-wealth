"""Subscribed, budgeted, resumable collection using existing versioned snapshots."""
import hashlib
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import timedelta
from threading import Lock
from ..common import ConflictError, DomainError, day, json_dumps, now, today
from . import lixinger, providers
from .metrics import years_before


def subscription(profile):
    # Only explicitly saved profiles are passed here, never the built-in catalog.
    return profile.get('subscription') or dict(enabled=True, modules=list(lixinger.DEFAULT_MODULES), history_start=None)


class Collection:
    def __init__(self, research):
        self.research = research
        self.identities = OrderedDict()
        self.identity_lock = Lock()

    def identity(self, code, *, lookup=False):
        key = code.strip().upper()
        if not key:
            raise DomainError('请输入指数代码或名称')
        try:
            return providers.identity(key)
        except providers.market.ProviderError:
            pass

        with self.identity_lock:
            saved = self.research.profiles()
            matches = {p['code']: p for p in [*self.identities.values(), *saved]
                       if key in (p['code'], p['name'].upper())}
            if not matches and lookup:
                if not self.research.writes_allowed():
                    raise DomainError('系统处于只读状态，无法联网核对新指数')
                by_code = bool(re.fullmatch(r'[A-Z0-9]{4,16}', key))
                params = {'stockCodes': [key]} if by_code else {}
                rows = self.research.access.request('cn/index', params)
                candidates = [row for row in rows if isinstance(row, dict) and
                              (row.get('stockCode') == key if by_code else str(row.get('name', '')).strip().upper() == key)]
                if len(candidates) > 1:
                    raise DomainError('理杏仁返回多个同名指数，请输入指数代码')
                if candidates:
                    profile = providers.lixinger_identity(candidates[0])
                    self.identities[profile['code']] = profile
                    if len(self.identities) > 128:
                        self.identities.popitem(last=False)
                    matches[profile['code']] = profile
            if len(matches) == 1:
                return deepcopy(next(iter(matches.values())))
            if matches:
                raise DomainError('存在多个同名指数，请输入指数代码')
        if lookup:
            raise DomainError('理杏仁未找到该指数，请核对代码或准确名称')
        raise DomainError('请先联网核对该指数，再按预览结果添加')

    def preview(self, code, modules, history_start, *, lookup=False):
        p = self.identity(code, lookup=lookup)
        adding = modules is None
        if adding:
            if history_start is not None:
                raise DomainError('添加指数默认使用全历史，请在管理采集中调整起始日')
            modules = lixinger.NEW_INDEX_MODULES
        if not modules or len(modules) != len(set(modules)) or any(m not in lixinger.MODULES for m in modules):
            raise DomainError('请选择有效且不重复的采集模块')
        end = providers.cutoff()
        start = str(day(history_start)) if history_start else p['launch']
        if not p['launch'] <= start <= end: raise DomainError('历史起点须介于指数发布日期和已完成交易日之间')
        overseas=p.get('exposure')=='overseas'
        unsupported=[m for m in modules if not providers.module_capabilities(p)[m]['supported']]
        chosen = [m for m in modules if m not in unsupported] if overseas else list(dict.fromkeys(['index_info', *modules]))
        if not chosen: raise DomainError('所选模块的跨市场身份与币种尚未核验')
        budget = {}; pending = []
        for m in chosen:
            if overseas: continue
            if m in ('index_financials', 'stock_valuations', 'financials', 'historical_financials'):
                pending.append(m); continue
            for call in lixinger.request_plan(p, m, start, end):
                e = call['endpoint']; budget[e] = budget.get(e, 0)+1
        state = self.research.access.status()
        iid = p['provider'] + ':' + p['code']
        with self.research.store.connection() as db:
            revision = self.research.revision()
            existing = db.execute('SELECT id FROM research_indices WHERE id=?', (iid,)).fetchone()
        return dict(profile=p, modules=chosen, history_start=history_start, effective_start=start, end=end,
                    existing_id=existing[0] if existing else None, research_revision=revision, endpoint_budget=[dict(endpoint=e, calls=n,
                    remaining=max(0, state['limit']-state['counts'].get(e, 0))) for e, n in sorted(budget.items())],
                    dependencies_pending=pending, unsupported_modules=unsupported, configured=state['configured'], enabled=state['enabled'],
                    limitation='公司数据仍用已核验来源；指数财报端点需基本信息确认后计划。添加仅保存订阅，点击更新才请求接口。')

    def save(self, code, modules, history_start, enabled, expected):
        adding = modules is None
        if adding and not enabled:
            raise DomainError('新增指数默认启用采集，请在管理采集中暂停')
        plan = self.preview(code, modules, history_start)
        p = plan['profile']; iid = p['provider']+':'+p['code']
        with self.research.worker_lock, self.research.transaction(expected=expected, bump=True) as db:
            old = db.execute('SELECT data FROM research_indices WHERE id=?', (iid,)).fetchone()
            if adding and old:
                raise ConflictError('该指数已在指数库中，请打开已有档案或使用管理采集')
            import json
            value = json.loads(old[0]) if old else dict(p, id=iid, created_at=now())
            previous = subscription(value)
            changed = previous.get('modules') != plan['modules'] or previous.get('history_start') != history_start
            value['subscription'] = dict(enabled=enabled, modules=plan['modules'], history_start=history_start)
            # A changed range invalidates checkpoints, never observations or historical versions.
            if changed: value['collection_generation'] = value.get('collection_generation', 0)+1
            db.execute('INSERT INTO research_indices VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (iid, json_dumps(value)))
        return dict(id=iid, subscription=value['subscription'], research_revision=self.research.revision())

    def active(self, iid, module):
        p = self.research.profile(iid); sub = subscription(p)
        if not sub['enabled'] or module not in sub['modules']: raise DomainError('该指数已暂停或模块未订阅；本次未采集')
        if self.research.updates.stopping.is_set() or not self.research.writes_allowed(): raise DomainError('采集已停止')
        return p

    def status(self, iid):
        p = self.research.profile(iid); states = {}
        for m in subscription(p)['modules']:
            stage = self.research.module(iid, 'stage.'+m)
            if stage:
                states[m] = {k: stage.get(k) for k in ('phase', 'last_incremental', 'last_reconciled', 'next_reconciliation', 'range_start', 'range_end', 'mode', 'difference')}
                states[m].update(completed=len(stage.get('parts', [])), planned=len(stage.get('calls', [])))
        return dict(subscription=subscription(p), modules=states)

    def collect(self, iid, module, end, automatic=False):
        p = self.active(iid, module); sub = subscription(p)
        if module in ('stock_valuations', 'financials', 'historical_financials'):
            return self.company_data(iid, module, end)
        if p.get('exposure')=='overseas':
            if module not in ('price','total_return','memberships'): raise DomainError('该跨市场模块尚未核验')
            value=providers.constituents(p,end) if module=='memberships' else providers.prices(p,module,end)
            self.active(iid,module)
            return dict(value,snapshot_id=self.research.publish(iid,module,value))
        old = self.research.module(iid, module)
        state = self.research.module(iid, 'stage.'+module) or {}
        if automatic and state.get('phase')=='validation_failed': raise DomainError('上次完整性校验失败；保留旧数据，需手动核对后重试')
        start = sub.get('history_start') or p['launch']
        if module == 'historical_memberships':
            start = max(start, str(years_before(end, 10)))
        full = (not old or old.get('provider_id') != 'lixinger' or not state.get('last_reconciled') or
                (today()-day(state['last_reconciled'][:10])).days >= 15 or state.get('generation') != p.get('collection_generation', 0))
        needs_valuation_backfill = (module == 'valuations' and old
                                    and 'ewpvo' not in old.get('series', {}).get('pe', {}))
        full = full or bool(needs_valuation_backfill)
        if not full and old.get('effective_date'): start = max(start, str(day(old['effective_date'])-timedelta(days=15)))
        # Resume the interrupted cycle with its original fixed cutoff and exact shards.
        if (state.get('phase') == 'collecting' and state.get('generation') == p.get('collection_generation',0)
                and (not needs_valuation_backfill or (state['mode'] == 'reconcile' and
                     'pe_ttm.ewpvo' in state['calls'][0]['params']['metricsList']))):
            start, end = state['range_start'], state['range_end']
            full = state['mode'] == 'reconcile'
        info = (self.research.module(iid, 'index_info') or {}).get('info')
        if module != 'index_info' and not info:
            raise DomainError('请先更新指数基本信息并核验地区、币种及编制方，再采集其他理杏仁模块')
        calls = lixinger.request_plan(p, module, start, end, info)
        if not full and module == 'historical_memberships':
            # Revisit old baskets only in the periodic reconciliation, not every trading day.
            last_period=max((b['period'] for b in (old or {}).get('baskets',[])),default='')
            calls = [c for c in calls if c['period'] > last_period]
        identity = hashlib.sha256(json_dumps([module, sub, p.get('collection_generation', 0), start, end, full, calls]).encode()).hexdigest()
        if state.get('phase') != 'collecting' or state.get('request_id') != identity:
            state = dict(request_id=identity, phase='collecting', generation=p.get('collection_generation', 0),
                         range_start=start, range_end=end, mode='reconcile' if full else 'incremental',
                         calls=calls, parts=[], last_incremental=state.get('last_incremental'), last_reconciled=state.get('last_reconciled'))
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
        if full: state.update(last_reconciled=now(), next_reconciliation=str(today()+timedelta(days=15)))
        self.research.publish(iid, 'stage.'+module, state)
        return dict(value, snapshot_id=sid)

    def company_data(self, iid, module, end):
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
            rows = self.shared_companies('financials', list(unique.values()), end)
            annual, observations, evidence = self.historical_industry_baskets(baskets['baskets'], end)
            value = dict(
                rows,
                years=history.totals(rows['rows'], annual),
                membership_snapshot_id=baskets['snapshot_id'],
                industry_observations=observations,
                industry_evidence=evidence,
                industry_basis='对应期末前最近7天内的历史估值记录行业；当前可得版本，不声称严格PIT',
            )
        else:
            value = self.shared_companies(module, members['rows'], end)

        with self.research.store.lock:
            latest = self.active(iid, module)
            if latest.get('collection_generation', 0) != p.get('collection_generation', 0):
                raise DomainError('采集范围已变化，本次响应未发布')
            sid = self.research.publish(iid, module, value)
        return dict(value, snapshot_id=sid)

    def historical_industry_baskets(self, baskets, end):
        """Enrich annual basket copies with dated observations, preserving source lists."""
        annual = [deepcopy(b) for b in baskets if b['period'].endswith('12-31')]
        known = {}
        observations = []
        evidence = []
        for profile in self.research.profiles():
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

    def shared_companies(self, module, members, end):
        """Reuse immutable company observations for the same query cutoff across indices."""
        wanted={m['code'] for m in members}; known=set(); rows={}; evidence=[]; periods=set(); refs=[]
        names=('financials','historical_financials') if module=='financials' else ('stock_valuations',)
        for p in self.research.profiles():
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


def merge(old, new, full, requested_range=None):
    """Merge revisions only within one provider/metric identity; missing rows never delete history."""
    stats = dict(added=0, revised=0, missing=0)
    if not old or old.get('provider_id') != new.get('provider_id'): return new, stats
    out = deepcopy(new)
    def combine(a, b, key='date'):
        prev = {r[key]: r for r in a}; fresh = {r[key]: r for r in b}
        if len(fresh) != len(b): raise DomainError('分片之间出现重复业务键，未发布')
        if prev and fresh:
            lo, hi = min(fresh), max(fresh)
            if full and requested_range: lo, hi = requested_range
            missing = (set(prev) if full and not requested_range else {k for k in prev if lo <= k <= hi})-set(fresh)
            stats['missing'] += len(missing)
            if missing: raise DomainError('来源返回历史缩水或中间缺记录，保留旧快照和分片；需核对后重试')
        elif prev and not fresh: raise DomainError('来源返回空数据，保留有效历史')
        stats['added'] += len(set(fresh)-set(prev))
        stats['revised'] += sum(prev[k] != v for k, v in fresh.items() if k in prev)
        return [dict(prev, **fresh)[k] for k in sorted(set(prev)|set(fresh))]
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
