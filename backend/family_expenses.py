"""Monthly cash surplus: recurring budgets and confirmed totals, never cash movements."""
import calendar
import hashlib
import re
from .common import DomainError, ZERO, day, dec, ds, json_dumps, now, today, wire
from .family_domain import active, at_item, money, read_state, schedule_projection, text
from .family_summary import income_records
from .ledger import Market


def month(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise DomainError('月份须为YYYY-MM')
    parsed = day(value + '-01')
    if not 1900 <= parsed.year <= today().year + 10:
        raise DomainError('月份超出支持范围')
    return value


def at_plan(entry, at):
    candidates = [v for v in entry['versions'] if v['effective_month'] <= at]
    return max(enumerate(candidates), key=lambda x: (x[1]['effective_month'], x[0]))[1] if candidates else None


def income_signature(records):
    return hashlib.sha256(json_dumps(wire(sorted(records, key=lambda r:r['id']))).encode()).hexdigest()


def planned_month(family, state, at, market):
    first = day(at + '-01'); end = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    rows = []
    for entry in family.get('expense_plans', []):
        v = at_plan(entry, at)
        if not v or v.get('end_month') and at > v['end_month']: continue
        value, sources = dec(v['amount']) if not v.get('source_item_id') else ZERO, []
        if v.get('source_item_id'):
            item = next((i for i in family['items'] if i['id'] == v['source_item_id']), None)
            if not item: value = None
            else:
                # Rules can change within a month. Resolve each due day using its effective metadata.
                for d in range(1, end.day + 1):
                    date = str(first.replace(day=d)); meta = at_item(item, date)
                    if not meta or not active(meta, date): continue
                    if not meta.get('payment_plan') or meta['payment_plan']['mode'] == 'none':
                        if not any(s['item_id'] == item['id'] and s['due_date'][:7] == at for s in family['schedules']): value = None
                    for payment in schedule_projection(family, date, 0):
                        if payment['item_id'] != item['id']: continue
                        fx_date = min(date, str(today()))
                        fx = market.fx(payment['currency'], fx_date)
                        amount = dec(payment['household_amount']) * dec(fx['rate']) if fx else None
                        if amount is None: value = None
                        elif value is not None: value += amount
                        sources.append(dict(date=date, currency=payment['currency'], amount=payment['household_amount'], fx=fx))
        rows.append(dict(id=entry['id'], name=v['name'], amount=value, source_item_id=v.get('source_item_id'), sources=sources))
    total = sum((r['amount'] for r in rows), ZERO) if all(r['amount'] is not None for r in rows) else None
    return dict(rows=rows, total=total if rows or family.get('expense_plans') else None)


def apply_expense(service, db, family, state, operation, data, record_id, reason):
    from .family_service import check_fields
    if operation == 'expense_plan':
        check_fields(data, 'name amount effective_month end_month source_item_id reason')
        import uuid
        old = next((r for r in family.get('expense_plans', []) if r['id'] == record_id), None)
        if record_id and not old: raise DomainError('固定开支计划不存在')
        effective = month(data.get('effective_month'))
        end = month(data['end_month']) if data.get('end_month') else None
        if end and end < effective: raise DomainError('结束月份不能早于生效月份')
        source = data.get('source_item_id') or None
        name = text(data.get('name'), '开支名称', 80, True)
        if source:
            item = next((r for r in family['items'] if r['id'] == source), None)
            if not item or item['versions'][-1]['category'] not in ('debt', 'insurance'):
                raise DomainError('只能引用已建档的贷款或保费计划')
            if any(e['id'] != record_id and any(v.get('source_item_id') == source for v in e['versions']) for e in family.get('expense_plans', [])):
                raise DomainError('该贷款或保费已关联开支，请编辑原计划，避免重复计算')
            amount = None
        else: amount = money(data.get('amount'), '每月计划开支')
        value = dict(name=name, amount=amount, effective_month=effective, end_month=end, source_item_id=source, recorded_at=now())
        result = dict(id=record_id or uuid.uuid4().hex, versions=(old['versions'] if old else []) + [value])
        service._put(db, 'family_expense_plans', result, reason)
        return result
    check_fields(data, 'month extra_plan actual_amount confirmed note reason')
    at = month(data.get('month'))
    if record_id and record_id != at: raise DomainError('月度开支ID必须与月份一致')
    actual = money(data.get('actual_amount'), '当月实际开支总额', True)
    confirmed = data.get('confirmed', False)
    if type(confirmed) is not bool: raise DomainError('月度核对状态须为布尔值')
    if at > str(today())[:7] and (actual is not None or confirmed): raise DomainError('未来月份只能设置预算，不能记录实际开支')
    if confirmed and actual is None: raise DomainError('确认月度收支前须填写实际开支总额，零开支请填0')
    extra = money(data.get('extra_plan', '0'), '当月额外预算')
    old = next((r for r in family.get('expense_months', []) if r['id'] == at), None)
    previous = old['versions'][-1] if old else None
    market = Market(state)
    records = [r for r in income_records(state, family, market=market) if r['month'] == at]
    if confirmed and any(r['value'] is None for r in records): raise DomainError('收入缺少汇率，补齐后才能确认月度收支')
    fixed = previous.get('plan_snapshot') if previous and previous.get('plan_snapshot') is not None else wire(planned_month(family, state, at, market))
    value = dict(month=at, extra_plan=extra, actual_amount=actual, confirmed=confirmed,
                 note=text(data.get('note'), '月度备注', 300), recorded_at=now(),
                 income_signature=income_signature(records) if confirmed else None,
                 plan_snapshot=fixed if confirmed or previous and previous.get('plan_snapshot') is not None else None)
    result = dict(id=at, versions=(old['versions'] if old else []) + [value])
    service._put(db, 'family_expense_months', result, reason)
    return result


def expense_summary(store, year=None, include_fund=True):
    year = year or today().year
    if not 1900 <= year <= today().year + 10: raise DomainError('收支年份无效')
    with store.connection() as db:
        state, family = store.state(db), read_state(db)
    market = Market(state); records = income_records(state, family, market=market)
    saved = {r['id']: r['versions'][-1] for r in family.get('expense_months', [])}
    rows = []
    for m in range(1, 13):
        at = f'{year}-{m:02d}'; record = saved.get(at, {})
        all_income = [r for r in records if r['month'] == at]
        selected = [r for r in all_income if include_fund or r['kind'] != 'fund']
        needs_review = bool(record.get('confirmed') and record.get('income_signature') != income_signature(all_income))
        confirmed = bool(record.get('confirmed') and not needs_review)
        income = sum((r['value'] for r in selected), ZERO) if (selected or confirmed) and all(r['value'] is not None for r in selected) else None
        fixed = record.get('plan_snapshot') or wire(planned_month(family, state, at, market))
        extra = dec(record.get('extra_plan', '0'))
        planned = dec(fixed['total']) + extra if fixed['total'] is not None else extra if record and not fixed['rows'] else None
        actual = dec(record['actual_amount']) if record.get('actual_amount') is not None else None
        surplus = income - actual if confirmed and income is not None and actual is not None else None
        estimate = income - planned if income is not None and planned is not None else None
        rows.append(dict(month=at, income=income, income_records=all_income, planned=planned, fixed_plan=fixed, extra_plan=extra,
                         actual=actual, confirmed=confirmed, needs_review=needs_review, note=record.get('note', ''),
                         surplus=surplus, savings_rate=surplus / income if surplus is not None and income > ZERO else None,
                         estimated_surplus=estimate, estimated_savings_rate=estimate / income if estimate is not None and income > ZERO else None,
                         status='需重新核对收入' if needs_review else '已核对' if confirmed else '预算估算' if planned is not None else '未记录'))
    annual_records = [r for r in records if r['month'].startswith(str(year)+'-') and (include_fund or r['kind'] != 'fund')]
    known_income = sum((r['value'] for r in annual_records if r['value'] is not None), ZERO) if annual_records else None
    missing_income_months = sorted({r['month'] for r in annual_records if r['value'] is None})
    income_summary = dict(total=known_income if not missing_income_months else None, known_total=known_income,
                          months=sorted({r['month'] for r in annual_records}), missing_fx_months=missing_income_months)
    included = [r for r in rows if r['confirmed'] and r['income'] is not None and r['actual'] is not None]
    income = sum((r['income'] for r in included), ZERO) if included else None
    expense = sum((r['actual'] for r in included), ZERO) if included else None
    summary = dict(income=income, expense=expense, surplus=income-expense if included else None,
                   savings_rate=(income-expense)/income if included and income > ZERO else None,
                   months=[r['month'] for r in included], confirmed_months=len(included))
    years = {today().year, year} | {int(r['month'][:4]) for r in records} | {int(m[:4]) for m in saved}
    years |= {int(v['effective_month'][:4]) for r in family.get('expense_plans', []) for v in r['versions']}
    source_items = [dict(id=r['id'], name=r['versions'][-1]['name']) for r in family['items'] if r['versions'][-1]['category'] in ('debt', 'insurance')]
    return wire(dict(year=year, years=sorted(years, reverse=True), revision=state['revision'], rows=rows, summary=summary, income_summary=income_summary,
                     include_fund=include_fund, plans=family.get('expense_plans', []), source_items=source_items,
                     basis=('全部收入（含公积金）' if include_fund else '不含公积金收入')+'口径；实际总额替代预算，不与固定开支相加；年储蓄率按同一组已核对月份的收入和开支合计计算；未记录不等于零'))


def validate_expenses(family):
    item_ids = {r['id'] for r in family['items']}
    linked = {}
    for entry in family.get('expense_plans', []):
        if not entry.get('versions'): raise DomainError('固定开支历史无效')
        for v in entry['versions']:
            month(v['effective_month']); text(v['name'], '开支名称', 80, True)
            if v.get('end_month') and month(v['end_month']) < v['effective_month']: raise DomainError('开支计划期间无效')
            source = v.get('source_item_id')
            if source:
                if source not in item_ids or source in linked and linked[source] != entry['id']: raise DomainError('开支来源不存在或重复')
                linked[source] = entry['id']
            else: money(v['amount'])
    for entry in family.get('expense_months', []):
        if not entry.get('versions'): raise DomainError('月度开支历史无效')
        for v in entry['versions']:
            if month(v['month']) != entry['id']: raise DomainError('月度开支索引无效')
            money(v['extra_plan']); money(v['actual_amount'], nullable=True)
            if type(v.get('confirmed')) is not bool: raise DomainError('月度核对状态无效')
            if v['confirmed'] and (v['actual_amount'] is None or not re.fullmatch('[a-f0-9]{64}', v.get('income_signature') or '')): raise DomainError('月度核对记录不完整')
            snap = v.get('plan_snapshot')
            if snap is not None:
                money(snap['total'], nullable=True)
                for row in snap['rows']: money(row['amount'], nullable=True)
