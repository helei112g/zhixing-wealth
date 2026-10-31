"""Optional portfolio-scoped labels and a non-overlapping current holding tree."""
from .common import DomainError, ZERO, dec, json_dumps, now, ratio, wire

SCOPES = ('A股', '港股', '美股', '海外权益', '国内债券', '海外债券', '大宗商品', '货币', '混合 / 其他')
EQUITY_DIRECTIONS = ('红利', '宽基', '行业', '价值', '成长', '其他')
DIRECTIONS = {scope: EQUITY_DIRECTIONS for scope in SCOPES}
DIRECTIONS.update({'国内债券': ('纯债', '可转债', '二级债', '其他'),
                   '海外债券': ('国债', '投资级债', '高收益债', '综合债券', '其他'),
                   '大宗商品': ('贵金属', '能源', '工业金属', '农产品', '综合商品', '其他'),
                   '货币': ('货币基金', '其他'), '混合 / 其他': ('偏股', '偏债', '均衡', '其他')})


def inferred_scope(instrument):
    # A fund's trading venue/currency is not evidence of its underlying exposure.
    if instrument.get('type') == 'stock':
        return {'SH': 'A股', 'SZ': 'A股', 'HK': '港股', 'US': '美股'}.get(instrument.get('market'), '待确认')
    return '待确认'


def resolve_labels(instrument, saved=None):
    saved = saved or {}
    automatic = inferred_scope(instrument)
    scope = saved.get('scope') or automatic
    direction, detail = saved.get('direction', ''), saved.get('detail', '')
    return dict(scope=saved.get('scope', ''), direction=direction, detail=detail,
                resolved_scope=scope, automatic_scope=automatic,
                scope_source='manual' if saved.get('scope') else 'unconfirmed' if automatic == '待确认' else 'automatic',
                path=[v for v in (scope, direction, detail) if v])


def normalize_labels(data, instrument):
    result = {}
    for key, limit in (('scope', 30), ('direction', 30), ('detail', 40)):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value.strip()) > limit or any(ord(c) < 32 for c in value):
            raise DomainError('标签须为长度限制内的单行文字')
        result[key] = value.strip()
    if result['scope'] and result['scope'] not in SCOPES:
        raise DomainError('投资范围无效')
    scope = result['scope'] or inferred_scope(instrument)
    if result['direction'] and result['direction'] not in DIRECTIONS.get(scope, ()):
        raise DomainError('请先确认投资范围，并选择该范围下的配置方向')
    if result['detail'] and not result['direction']:
        raise DomainError('填写细分标签前请选择配置方向')
    return result


def label_map(state, portfolio_id):
    return {r['instrument_id']: r for r in state.get('holding_labels', []) if r['portfolio_id'] == portfolio_id}


def validate_labels_backup(state):
    for row in state.get('holding_labels', []):
        instrument = next((i for i in state['instruments'] if i['id'] == row['instrument_id']), None)
        if not instrument or not any(a['portfolio_id'] == row['portfolio_id'] and a['instrument_id'] == row['instrument_id'] for a in state['allocations']):
            raise DomainError('持仓标签不属于有效组合同标的')
        normalize_labels(row, instrument)


class HoldingAnalysis:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def context(self, state, portfolio_id, instrument_id):
        portfolio = self.service.portfolio(state, portfolio_id)
        if not any(a['portfolio_id'] == portfolio_id and a['instrument_id'] == instrument_id for a in state['allocations']):
            raise DomainError('标的不属于当前组合')
        instrument = next(i for i in state['instruments'] if i['id'] == instrument_id)
        return portfolio, instrument

    def options(self, state, portfolio_id):
        suggestions = sorted({(r.get('scope') or inferred_scope(next(i for i in state['instruments'] if i['id'] == r['instrument_id'])),
                               r.get('direction', ''), r['detail']) for r in state.get('holding_labels', [])
                              if r['portfolio_id'] == portfolio_id and r.get('detail')})
        return dict(scopes=SCOPES, directions=DIRECTIONS,
                    suggestions=[dict(scope=s, direction=d, detail=t) for s, d, t in suggestions])

    def read(self, portfolio_id, instrument_id):
        state = self.store.state()
        _, instrument = self.context(state, portfolio_id, instrument_id)
        return wire(dict(portfolio_id=portfolio_id, instrument_id=instrument_id, revision=state['revision'],
                         allocation=next(a for a in state['allocations'] if a['portfolio_id']==portfolio_id and a['instrument_id']==instrument_id),
                         labels=resolve_labels(instrument, label_map(state, portfolio_id).get(instrument_id)),
                         options=self.options(state, portfolio_id)))

    def put(self, portfolio_id, instrument_id, data):
        revision = data.get('expected_revision')
        if type(revision) is not int or revision < 0:
            raise DomainError('设置标签须携带读取时的 expected_revision')
        with self.store.transaction(expected_revision=revision) as db:
            state = self.store.state(db)
            portfolio, instrument = self.context(state, portfolio_id, instrument_id)
            if portfolio.get('archived'):
                raise DomainError('组合已归档，请先恢复后再设置标签')
            self.write(db, state, portfolio_id, instrument, data)
        return self.read(portfolio_id, instrument_id)

    def write(self, db, state, portfolio_id, instrument, data):
        labels = normalize_labels(data, instrument)
        old = label_map(state, portfolio_id).get(instrument['id'])
        item = dict(labels, portfolio_id=portfolio_id, instrument_id=instrument['id'], updated_at=now())
        db.execute('INSERT OR REPLACE INTO holding_labels VALUES (?,?,?)',
                   (portfolio_id, instrument['id'], json_dumps(item)))
        self.store.audit(db, 'holding_labels', portfolio_id + ':' + instrument['id'], old, item, '设置当前持仓标签；不修改历史流水或投入计划')
        return item

    def report(self, portfolio_id, basis='holdings'):
        if basis not in ('holdings', 'assets'):
            raise DomainError('统计口径须为holdings或assets')
        state = self.store.state()
        portfolio = self.service.portfolio(state, portfolio_id)
        snap = self.service.plan_snapshot(state, portfolio)
        saved = label_map(state, portfolio_id)
        holdings, nodes = [], []
        total = snap['securities_total'] if basis == 'holdings' else snap['total_assets']
        total = dec(total) if total is not None else None
        for h in snap['holdings']:
            if dec(h['quantity']) <= ZERO:
                continue
            labels = resolve_labels(h['instrument'], saved.get(h['instrument_id']))
            value = dec(h['value_base']) if h['value_base'] is not None else None
            row = dict(instrument_id=h['instrument_id'], instrument=h['instrument'], value=value, labels=labels)
            holdings.append(row)
            children, path = nodes, []
            for name in labels['path']:
                path = path + [name]
                node = next((n for n in children if n['name'] == name), None)
                if node is None:
                    node = dict(id='group:' + json_dumps(path), name=name, path=path, value=ZERO,
                                known_value=ZERO, holding_ids=[], children=[], kind='group', incomplete=False)
                    children.append(node)
                node['holding_ids'].append(h['instrument_id'])
                if value is None:
                    node['incomplete'] = True
                else:
                    node['known_value'] += dec(value)
                children = node['children']
        if basis == 'assets':
            receivables = snap['receivables']
            receivable = None if any(r['value_base'] is None for r in receivables) else sum((dec(r['value_base']) for r in receivables), ZERO)
            for key, name, value in (('cash', '剩余可投金额', snap['cash_total']), ('receivables', '应收及在途', receivable)):
                value = dec(value) if value is not None else None
                if value != ZERO:
                    nodes.append(dict(id='system:' + key, name=name, path=[name], value=value, known_value=value or ZERO,
                                      holding_ids=[], children=[], kind='system', incomplete=value is None))
        by_id = {h['instrument_id']: h for h in holdings}
        def include_direct(group):
            for n in group:
                if n['children']:
                    direct = set(n['holding_ids']) - {i for child in n['children'] for i in child['holding_ids']}
                    if direct:
                        values = [by_id[i]['value'] for i in direct]
                        n['children'].append(dict(id=n['id'] + ':direct', name='本级持仓', path=n['path'] + ['本级持仓'],
                                                  value=ZERO, known_value=sum((dec(v) for v in values if v is not None), ZERO),
                                                  incomplete=any(v is None for v in values), holding_ids=sorted(direct), children=[], kind='group'))
                    include_direct(n['children'])
        include_direct(nodes)
        incomplete = total is None or any(n['incomplete'] for n in nodes)
        def finish(group):
            for n in group:
                n['value'] = None if n['incomplete'] else n['known_value']
                n['weight'] = ratio(n['value'], total) if not incomplete and total and total > ZERO else None
                n['count'] = len(n['holding_ids'])
                finish(n['children'])
        finish(nodes)
        for row in holdings:
            row['weight'] = ratio(row['value'], total) if row['value'] is not None and not incomplete and total and total > ZERO else None
        eligible = not incomplete and total > ZERO and all(n['value'] >= ZERO for n in nodes)
        reason = '部分估值或汇率缺失，占比暂不计算' if incomplete else '暂无正值资产可展示' if total <= ZERO else '存在负值，保留实际金额明细' if not eligible else ''
        return wire(dict(portfolio_id=portfolio_id, revision=state['revision'], date=snap['date'], basis=basis, currency='CNY',
                         total=total, nodes=nodes, holdings=holdings, eligible=eligible, incomplete=incomplete,
                         estimated=snap.get('estimated', False), reason=reason,
                         denominator='全部当前持仓市值（包含待确认标签）' if basis == 'holdings' else '组合总资产（持仓、剩余可投金额、应收及在途）'))
