"""Budget allocation projections and atomic, non-trading purchase intentions."""
import json

from .common import ConflictError, DomainError, ZERO, dec, ds, json_dumps, ratio, wire
from .holding_analysis import label_map, normalize_labels, resolve_labels
from .ledger import Ledger
from .planning import plan_funding, plan_percentage


INVESTMENT_KINDS = ('BUY', 'REINVEST', 'OPENING_POSITION', 'SUBSCRIBE_PENDING')


def purchase_ids(state, pid, iid):
    return sorted(e['id'] for e in state['events'] if e['portfolio_id'] == pid
                  and e.get('instrument_id') == iid and e['kind'] in INVESTMENT_KINDS)


def holding_status(state, pid, holding):
    if holding.get('unconfirmed_split'):
        return '待核对'
    if dec(holding['quantity']) > ZERO:
        return '持有中'
    allocation = holding.get('allocation', {})
    purchases = purchase_ids(state, pid, holding['instrument_id'])
    if allocation.get('planned_buy') and not (set(purchases) - set(allocation.get('planned_buy_after', []))):
        return '计划买入'
    return '已清仓' if purchases else '尚未买入'


def prepare_purchase(service, state, pid, data):
    portfolio = service.portfolio(state, pid)
    if portfolio.get('archived'):
        raise DomainError('组合已归档，请先恢复后再设置计划')
    if type(data.get('expected_revision')) is not int:
        raise DomainError('计划买入须携带读取时的 expected_revision')
    if data['expected_revision'] != state['revision']:
        raise ConflictError('组合已变化，请重新预览并核对；本次未写入')
    if bool(data.get('instrument')) == bool(data.get('instrument_id')):
        raise DomainError('请选择一个已有标的或填写新标的，不能同时提供')
    info = None
    if data.get('instrument'):
        candidate, _, info = service.inline_instrument(state, dict(kind='BUY', portfolio_id=pid, instrument=data['instrument']))
        instrument = info['instrument']
        # A plan never renames a globally shared instrument.
        if info['before_instrument']:
            instrument = info['before_instrument']
            info['instrument'] = instrument
    else:
        candidate = state
        instrument = next((i for i in state['instruments'] if i['id'] == data['instrument_id'] and not i.get('builtin')), None)
        if not instrument:
            raise DomainError('标的不存在')
    if instrument['type'] == 'index':
        raise DomainError('指数用于研究，请选择实际买入的基金或证券')
    iid = instrument['id']
    old = next((a for a in state['allocations'] if a['portfolio_id'] == pid and a['instrument_id'] == iid), None)
    pct = plan_percentage(data['target_pct'])
    if pct <= ZERO:
        raise DomainError('计划买入比例须大于0；取消已有计划请设置计划为0')
    from .common import classification
    allocation = dict(old or {}, portfolio_id=pid, instrument_id=iid, target_pct=ds(pct),
                      **classification(dict(old or {}, asset_class=data.get('asset_class', (old or {}).get('asset_class', '未分类')))),
                      planned_buy=True, planned_buy_after=purchase_ids(state, pid, iid))
    labels = normalize_labels(data.get('holding_labels') or label_map(state, pid).get(iid, {}), instrument)
    candidate = dict(candidate,
                     instruments=[i for i in candidate['instruments'] if i['id'] != iid] + [instrument],
                     allocations=[a for a in candidate['allocations'] if (a['portfolio_id'], a['instrument_id']) != (pid, iid)] + [allocation])
    snap = Ledger(candidate, portfolio).run(include_history=False)
    current = next(h for h in snap['holdings'] if h['instrument_id'] == iid)
    if dec(current['quantity']) > ZERO or current.get('unconfirmed_split'):
        allocation['planned_buy'] = False  # Updating an existing holding must not re-arm after a later sale.
    return dict(revision=state['revision'], instrument=instrument, allocation=allocation,
                holding_labels=resolve_labels(instrument, labels), target_amount=dec(portfolio['planned_amount']) * pct / 100,
                plan_funding=plan_funding(snap), existing=old is not None), info, old, labels, candidate


def purchase_plan(service, pid, data, preview=False):
    if preview:
        value, *_ = prepare_purchase(service, service.store.state(), pid, data)
        return wire(dict(value, preview=True))
    with service.store.transaction(expected_revision=data.get('expected_revision')) as db:
        state = service.store.state(db)
        value, info, old, labels, candidate = prepare_purchase(service, state, pid, data)
        inst, allocation = value['instrument'], value['allocation']
        if info and not info['before_instrument']:
            db.execute('INSERT INTO instruments VALUES (?,?,?)', (inst['id'], info['identity'], json_dumps(inst)))
            service.store.audit(db, 'instrument', inst['id'], None, inst, '计划买入建档；未产生交易')
        db.execute('INSERT OR REPLACE INTO allocations VALUES (?,?,?)', (pid, inst['id'], json_dumps(allocation)))
        service.store.audit(db, 'allocation', pid + ':' + inst['id'], old, allocation, '设置计划买入；不扣减现金')
        service.holding_analysis.write(db, candidate, pid, inst, labels)
    return wire(dict(value, revision=value['revision'] + 1, preview=False))


def purchase_plan_deletion_context(db, pid, iid):
    row = db.execute('''
        SELECT a.data AS allocation, p.data AS portfolio, i.data AS instrument, l.data AS labels,
               (SELECT value FROM meta WHERE key='revision') AS revision
        FROM allocations a
        JOIN portfolios p ON p.id=a.portfolio_id
        JOIN instruments i ON i.id=a.instrument_id
        LEFT JOIN holding_labels l ON l.portfolio_id=a.portfolio_id AND l.instrument_id=a.instrument_id
        WHERE a.portfolio_id=? AND a.instrument_id=?
    ''', (pid, iid)).fetchone()
    if not row:
        raise DomainError('当前组合没有此标的的买入计划')
    allocation = json.loads(row['allocation'])
    portfolio = json.loads(row['portfolio'])
    instrument = json.loads(row['instrument'])
    labels = json.loads(row['labels']) if row['labels'] is not None else None
    if portfolio.get('archived'):
        raise DomainError('组合已归档，请先恢复后再删除计划')
    # Setting the target to zero clears planned_buy but retains the original plan marker.
    if not (allocation.get('planned_buy') or isinstance(allocation.get('planned_buy_after'), list)):
        raise DomainError('只有通过计划买入添加或标记的标的可以删除计划')

    scope = (pid, iid)
    if db.execute("SELECT 1 FROM events WHERE portfolio_id=? AND json_extract(data,'$.instrument_id')=? LIMIT 1", scope).fetchone():
        raise DomainError('此标的有交易流水或删除历史，只能调整投入目标')
    if db.execute('SELECT 1 FROM investment_notes WHERE portfolio_id=? AND instrument_id=? LIMIT 1', scope).fetchone():
        raise DomainError('此标的有投资笔记或删除历史，只能调整投入目标')
    if db.execute("SELECT 1 FROM reservations WHERE portfolio_id=? AND json_extract(data,'$.instrument_id')=? LIMIT 1", scope).fetchone():
        raise DomainError('此标的有历史资金预留，只能调整投入目标')
    if db.execute("SELECT 1 FROM corporate_actions WHERE instrument_id=? AND json_extract(data,'$.portfolio_id')=? LIMIT 1", (iid, pid)).fetchone():
        raise DomainError('此标的有分红或企业行动核对记录，只能调整投入目标')

    pct = dec(allocation['target_pct'])
    label_path = resolve_labels(instrument, labels)['path'] if labels and any(labels.get(k) for k in ('scope', 'direction', 'detail')) else None
    preview = wire(dict(portfolio_id=pid, instrument_id=iid, portfolio_name=portfolio['name'],
                        instrument_name=instrument['name'], instrument_code=instrument['code'],
                        instrument_market=instrument['market'], revision=int(row['revision']), target_pct=pct,
                        target_amount=dec(portfolio['planned_amount']) * pct / 100,
                        holding_label_path=label_path))
    return preview, allocation, labels


def preview_purchase_plan_deletion(service, pid, iid):
    with service.store.connection() as db:
        db.execute('BEGIN')  # Read the revision and all dependencies from the same snapshot.
        preview, _, _ = purchase_plan_deletion_context(db, pid, iid)
    return preview


def delete_purchase_plan(service, pid, iid, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise DomainError('删除计划须携带预览时的 expected_revision')
    with service.store.transaction(expected_revision=expected_revision) as db:
        _, allocation, labels = purchase_plan_deletion_context(db, pid, iid)
        scope = (pid, iid)
        key = pid + ':' + iid
        reason = '删除未买入计划并从当前组合移除标的'
        if labels is not None:
            db.execute('DELETE FROM holding_labels WHERE portfolio_id=? AND instrument_id=?', scope)
            service.store.audit(db, 'holding_labels', key, labels, None, reason)
        db.execute('DELETE FROM allocations WHERE portfolio_id=? AND instrument_id=?', scope)
        service.store.audit(db, 'allocation', key, allocation, None, reason)
    return dict(portfolio_id=pid, instrument_id=iid, revision=expected_revision + 1)


def allocation_report(service, pid, level='scope', scope='', direction='', detail='', asset_class='', status='all', sort='gap', *, instrument_id='', _state=None, _snapshot=None):
    levels = {'scope': 1, 'direction': 2, 'detail': 3, 'instrument': 4}
    if level not in levels or status not in ('all', 'incomplete', 'over', 'unplanned', 'planned') or sort not in ('gap', 'name'):
        raise DomainError('配置视角、筛选或排序无效')
    state = _state if _state is not None else service.store.state(); portfolio = service.portfolio(state, pid)
    snap = _snapshot if _snapshot is not None else service.plan_snapshot(state, portfolio); saved = label_map(state, pid)
    budget, total = dec(portfolio['planned_amount']), snap['total_assets']
    all_rows = []
    for h in snap['holdings']:
        labels = resolve_labels(h['instrument'], saved.get(h['instrument_id']))
        path = [labels['resolved_scope'], labels['direction'] or '未细分', labels['detail'] or '未细分']
        net, target, pct = h['net_invested_base'], h['target_amount'], h['target_pct']
        gap = target - net if pct > ZERO and net is not None else None
        all_rows.append(dict(instrument_id=h['instrument_id'], instrument=h['instrument'], path=path,
                             asset_class=h['allocation'].get('asset_class', '未分类'), status=holding_status(state, pid, h),
                             target_pct=pct, target_amount=target, net_invested=net, value=h['value_base'],
                             gap=gap, required_amount=max(ZERO, gap) if gap is not None else ZERO if pct == ZERO else None,
                             over_amount=max(ZERO, -gap) if gap is not None else ZERO if pct == ZERO else None))
    filtered = [r for r in all_rows if (not scope or r['path'][0] == scope) and
                (not direction or r['path'][1] == direction) and (not detail or r['path'][2] == detail) and
                (not asset_class or r['asset_class'] == asset_class) and (not instrument_id or r['instrument_id'] == instrument_id)]
    groups = {}
    for row in filtered:
        path = row['path'][:levels[level]] if level != 'instrument' else row['path'] + [row['instrument']['name']]
        key = tuple(path) if level != 'instrument' else row['instrument_id']
        groups.setdefault(key, dict(path=path, members=[]))['members'].append(row)
    result = []
    for key, group in groups.items():
        members = group['members']
        def total_of(field):
            return sum((r[field] for r in members), ZERO) if all(r[field] is not None for r in members) else None
        pct, target, net, value = (total_of(k) for k in ('target_pct', 'target_amount', 'net_invested', 'value'))
        required, over = total_of('required_amount'), total_of('over_amount')
        unplanned = sum(r['target_pct'] == ZERO for r in members)
        # A group with unplanned members has no complete target for net comparison.
        gap = target - net if not unplanned and net is not None else None
        item = dict(id=json_dumps(key), name=group['path'][-1], path=group['path'], count=len(members),
                    instrument_id=members[0]['instrument_id'] if level == 'instrument' else None,
                    status=members[0]['status'] if level == 'instrument' else None,
                    target_pct=pct, target_amount=target, net_invested=net, value=value,
                    input_weight=ratio(net, budget) if net is not None else None,
                    market_weight=ratio(value, total) if value is not None and total is not None and total > ZERO else None,
                    gap=gap, gap_weight=ratio(gap, budget) if gap is not None else None,
                    required_amount=required, over_amount=over, unplanned_count=unplanned,
                    incomplete_count=sum(r['required_amount'] is not None and r['required_amount'] > ZERO for r in members),
                    unknown_count=sum(r['required_amount'] is None for r in members),
                    planned_count=sum(r['status'] == '计划买入' for r in members))
        if status == 'incomplete' and not (item['incomplete_count'] or item['unknown_count']): continue
        if status == 'over' and not any(r['over_amount'] is not None and r['over_amount'] > ZERO for r in members): continue
        if status == 'unplanned' and not unplanned: continue
        if status == 'planned' and not item['planned_count']: continue
        result.append(item)
    result.sort(key=lambda r: (r['required_amount'] is None, -(r['required_amount'] or ZERO), r['id']) if sort == 'gap' else (r['path'], r['id']))
    return wire(dict(portfolio_id=pid, revision=state['revision'], date=snap['date'], level=level, rows=result,
                     budget=budget, total_assets=total, plan_funding=plan_funding(snap),
                     options=dict(instruments=[dict(id=r['instrument_id'],name=r['instrument']['name'],code=r['instrument']['code']) for r in all_rows], paths=sorted({tuple(r['path']) for r in all_rows}), asset_classes=sorted({r['asset_class'] for r in all_rows})),
                     basis='计划与已投比例以整个组合总预算为分母；市值占比以整个组合总资产为分母；待投入按标的正缺口相加，超投不抵消缺口'))
