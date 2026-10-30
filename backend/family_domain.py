"""Validation and deterministic read projections for the family balance sheet."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from .common import DomainError, ZERO, dec, ds, currency, day, today, now, json_dumps
from .ledger import Ledger, Market

TABLES = ('family_items', 'family_values', 'family_links', 'family_schedules', 'family_settings',
          'family_reconciliations', 'family_closures', 'family_notification_state', 'family_income')
CATEGORIES = {'cash': '现金', 'fixed': '固定资产', 'investment': '投资理财', 'receivable': '应收款（别人欠我的钱）', 'insurance': '保单现金价值', 'debt': '负债'}
REASONS = ('opening', 'balance', 'valuation', 'income_expense', 'transfer', 'correction', 'scope')


def text(value, field, limit=120, required=False):
    value = str(value or '').strip()
    if (required and not value) or len(value) > limit:
        raise DomainError(f'{field}须为{1 if required else 0}到{limit}字')
    return value


def money(value, field='金额', nullable=False):
    return None if nullable and value in (None, '') else ds(dec(value, field, nonnegative=True))


def past(value, field='生效日期'):
    d = day(value, field)
    if d > today():
        raise DomainError(field + '不能在未来；未来缴款请录入计划')
    return str(d)


def fraction(value):
    v = dec(value, '持有或承担比例', positive=True)
    if v > 1:
        raise DomainError('持有比例须大于0且不超过1')
    return ds(v)


def read_state(db):
    return {table.removeprefix('family_'): [json.loads(r[0]) for r in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
            for table in TABLES if table != 'family_income' or db.execute("SELECT 1 FROM sqlite_master WHERE name='family_income'").fetchone()}


def at_item(item, date):
    versions = [v for v in item['versions'] if v['effective_on'] <= date]
    return max(enumerate(versions), key=lambda x: (x[1]['effective_on'], x[0]))[1] if versions else None


def active(row, date):
    return row['effective_on'] <= date and (not row.get('closed_on') or date < row['closed_on'])


def last_value(values, item_id, date):
    rows = [v for v in values if v['item_id'] == item_id and v['effective_on'] <= date]
    return max(enumerate(rows), key=lambda x: (x[1]['effective_on'], x[0]))[1] if rows else None


def event_signatures(state, pid, date):
    """Settlement changes become visible even without a new source event revision."""
    result = {}
    for e in state['events']:
        if e['portfolio_id'] != pid or e['date'] > date or e['kind'] in ('SPLIT', 'OPENING_POSITION'):
            continue
        signature = {k: v for k, v in e.items() if k not in ('created_at', 'updated_at', 'fingerprint')}
        signature['settled_as_of'] = {k: str(e[k]) <= date for k in ('settlement_date', 'pay_date', 'reinvest_date') if e.get(k)}
        result[e['id']] = hashlib.sha256(json_dumps(signature).encode()).hexdigest()
    return result


def portfolio_snapshot(state, portfolio, date, *, market=None):
    ledger = Ledger(state, portfolio, market=market)
    for op in ledger.ops:
        if op[0] > date:
            break
        ledger.apply(op)
    return ledger.snapshot(day(date))


def settings_at(family, date):
    rows = [r for r in family['settings'] if r['effective_on'] <= date]
    return max(enumerate(rows), key=lambda x: (x[1]['effective_on'], x[0]))[1] if rows else {
        'living_expense': None, 'monthly_income': None, 'reserve_months': '12', 'effective_on': date,
        'debt_plan_complete': False, 'policy_plan_complete': False}


def snapshot(state, family, date, *, market=None):
    market = market if market is not None else Market(state)
    rows, missing, warnings, pending, links = [], [], [], [], []
    def convert(value, c):
        fx = market.fx(c, date)
        if value is None:
            return None, fx
        if dec(value) == 0:
            return ZERO, fx
        return (dec(value) * dec(fx['rate']) if fx else None), fx
    for item in family['items']:
        meta = at_item(item, date)
        if not meta or not active(meta, date):
            continue
        val = last_value(family['values'], item['id'], date)
        native = dec(val['amount']) if val and val['amount'] is not None else None
        if meta['category'] == 'debt' and native is not None:
            native += dec(val['interest']) + dec(val['fees'])
        owned = native * dec(meta['share']) if native is not None else None
        value, fx = convert(owned, meta['currency'])
        free = None
        if meta['category'] == 'cash' and owned is not None:
            free, _ = convert((native - dec(val['restricted'])) * dec(meta['share']), meta['currency'])
        source_date = val['effective_on'] if val else None
        if value is None:
            missing.append(meta['name'] + ('：还没填写金额' if owned is None else '：缺少换算汇率'))
        stale = source_date is not None and (day(date) - day(source_date)).days > meta['review_days']
        if stale:
            warnings.append(meta['name'] + '：金额最后更新于 ' + source_date)
        payment_end = None
        if meta.get('payment_plan', {}).get('mode', 'none') != 'none':
            from .family_plans import shifted_date
            plan = meta['payment_plan']
            payment_end = str(shifted_date(plan['first_due'], (plan['periods']-1)*(12 if plan['mode']=='annual' else 1)))
        rows.append(dict(meta, id=item['id'], value=value, native_amount=native, household_amount=owned,
                         payment_end=payment_end,
                         household_restricted=dec(val['restricted']) * dec(meta['share']) if val else ZERO, available_cash=free,
                         source_date=source_date, fx=fx, linked=False, stale=stale, valuation=val,
                         versions=item['versions']))
    for link in family['links']:
        if not active(link, date):
            continue
        p = next((p for p in state['portfolios'] if p['id'] == link['portfolio_id']), None)
        if not p:
            missing.append('关联组合不存在'); continue
        snap = portfolio_snapshot(state, p, date, market=market)
        share = dec(link['share'])
        receivables = [r['value_base'] for r in snap['receivables']]
        invest = None if snap['securities_total'] is None or any(v is None for v in receivables) else (snap['securities_total'] + sum(receivables, ZERO)) * share
        quote_dates = [h['quote']['date'] for h in snap['holdings'] if h.get('quote') and h['quantity'] > 0]
        common = dict(id='link:' + link['id'], name=p['name'], linked=True, link_id=link['id'], portfolio_id=p['id'],
                      currency='CNY', share=link['share'], source_date=min(quote_dates) if quote_dates else date,
                      effective_on=link['effective_on'], archived=p.get('archived', False), stale=False)
        rows.append(dict(common, category='investment', value=invest, available_cash=None))
        if invest is None:
            missing.append(p['name'] + '：关联估值不完整')
        if snap['estimated']:
            warnings.append(p['name'] + '：组合估值含暂估')
        if p.get('archived'):
            warnings.append(p['name'] + '：组合已归档，仍计入资产总览')
        if p.get('funding_mode') != 'budget':
            cash = snap['cash_total'] * share if snap['cash_total'] is not None else None
            free = snap['available_cash'] * share if snap['available_cash'] is not None else None
            rows.append(dict(common, id=common['id'] + ':cash', name=p['name'] + ' · 真实现金', category='cash', value=cash, available_cash=free))
            if cash is None:
                missing.append(p['name'] + '：真实现金缺汇率')
        else:
            current = event_signatures(state, p['id'], date)
            reconciled = [r for r in family['reconciliations'] if r['link_id'] == link['id'] and r['effective_on'] <= date]
            baseline = max(enumerate(reconciled), key=lambda x: (x[1]['effective_on'], x[0]))[1]['signatures'] if reconciled else link['baseline']
            changed = sum(1 for k in set(current) | set(baseline) if current.get(k) != baseline.get(k))
            if changed:
                pending.append({'link_id': link['id'], 'portfolio_id': p['id'], 'name': p['name'], 'changed_events': changed})
        links.append(dict(link, name=p['name'], funding_mode=p.get('funding_mode'), investment_value=invest,
                          excluded_budget=snap['cash_total'] if p.get('funding_mode') == 'budget' else ZERO))
    categories = []
    for key, label in CATEGORIES.items():
        group = [r for r in rows if r['category'] == key]
        known = sum((r['value'] for r in group if r['value'] is not None), ZERO)
        categories.append({'key': key, 'label': label, 'known_value': known,
                           'value': None if any(r['value'] is None for r in group) else known, 'items': group})
    asset_groups = [c for c in categories if c['key'] != 'debt']
    known_assets = sum((c['known_value'] for c in asset_groups), ZERO)
    provisional_assets = None if any(c['value'] is None for c in asset_groups) else known_assets
    debts = next(c['value'] for c in categories if c['key'] == 'debt')
    provisional_net = provisional_assets - debts if provisional_assets is not None and debts is not None else None
    confirmed = not missing
    cash_rows = [r for r in rows if r['category'] == 'cash']
    available = None if any(r['available_cash'] is None for r in cash_rows) else sum((r['available_cash'] for r in cash_rows), ZERO)
    for c in categories:
        c['weight'] = c['value'] / provisional_assets if c['key'] != 'debt' and provisional_assets and provisional_assets > 0 and c['value'] is not None else None
    settings = settings_at(family, date)
    due = schedule_projection(family, date, 365)
    def scheduled(kind, days):
        selected = [r for r in due if r['kind'] == kind and date <= r['due_date'] < str(day(date) + timedelta(days=days)) and r['status'] != 'cancelled']
        numbers = [convert(dec(r['amount']) * dec(r['share']), r['currency'])[0] for r in selected]
        return None if any(v is None for v in numbers) else sum(numbers, ZERO)
    debt_month = scheduled('debt', 30) if settings['debt_plan_complete'] else None
    premiums = scheduled('premium', 365) if settings['policy_plan_complete'] else None
    living = dec(settings['living_expense']) if settings['living_expense'] is not None else None
    monthly = living + debt_month + premiums / 12 if living is not None and debt_month is not None and premiums is not None else None
    coverage = available / monthly if available is not None and monthly and monthly > 0 and confirmed else None
    income = dec(settings['monthly_income']) if settings['monthly_income'] is not None else None
    return dict(as_of=date, generated_at=now(), source_revision=state['revision'], items=rows, categories=categories,
                known_assets=known_assets, total_assets=provisional_assets if confirmed else None,
                provisional_assets=provisional_assets, total_debt=debts, net_assets=provisional_net if confirmed else None,
                provisional_net=provisional_net, debt_ratio=debts / provisional_assets if confirmed and debts is not None and provisional_assets and provisional_assets > 0 else None,
                incomplete=bool(missing), provisional=not confirmed, missing_items=missing, warnings=warnings,
                reconciliation_todos=pending, links=links, settings=settings, schedules=[r for r in due if r['days'] <= 35],
                safety={'available_cash': available, 'living_expense': living, 'debt_month': debt_month,
                        'annual_premiums': premiums, 'monthly_total': monthly, 'coverage_months': coverage,
                        'target': monthly * dec(settings['reserve_months']) if monthly is not None else None,
                        'debt_income_ratio': debt_month / income if debt_month is not None and income and income > 0 else None},
                oldest_source_date=min((r['source_date'] for r in rows if r.get('source_date')), default=None))


def schedule_projection(family, date, horizon=35):
    from .family_plans import installments
    end = str(day(date) + timedelta(days=horizon))
    rows = []
    for item in family['items']:
        meta = at_item(item, date)
        if not meta or not active(meta, date) or meta['category'] not in ('debt','insurance'): continue
        value = last_value(family['values'], item['id'], date)
        if meta['category']=='debt' and value and value['amount'] is not None and dec(value['amount'])+dec(value['interest'])+dec(value['fees'])==0: continue
        plan = meta.get('payment_plan')
        generated = installments(plan, item['id'], meta['name'], meta['currency'], date, end)
        rows.extend(r for r in generated if active(meta,r['due_date']))
        for row in family['schedules']:
            if row['item_id'] != item['id'] or row['status'] != 'pending' or not date <= row['due_date'] <= end: continue
            if row['kind'] in ('receivable','debt_end'): continue
            if plan and plan.get('mode') != 'none' and row['kind'] in ('debt','premium'): continue
            if not active(meta,row['due_date']): continue
            rows.append(dict(row, name=meta['name'], currency=meta['currency'], share=meta['share'],
                             household_amount=dec(row['amount'])*dec(meta['share']),
                             days=(day(row['due_date'])-day(date)).days))
    return sorted(rows,key=lambda r:(r['due_date'],r['id']))
