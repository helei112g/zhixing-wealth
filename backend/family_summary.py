"""Shared previous-period asset comparisons and income records."""
import calendar
from datetime import timedelta
from .common import DomainError, ZERO, day, dec, today, wire
from .family_domain import snapshot
from .family_ledger import debt_summary
from .ledger import Market


def income_records(state, family, *, market=None):
    market = market if market is not None else Market(state)
    rows = []
    for entry in family.get('income', []):
        value = entry['versions'][-1]
        fx = market.fx_series[value['currency']].at(value['date']) if value['currency'] != 'CNY' else {'rate': '1', 'date': value['date']}
        rows.append(dict(value, id=entry['id'], value=dec(value['amount']) * dec(fx['rate']) if fx else None, fx=fx))
    return rows


def period_summary(state, family, grain='month', period=None, start=None, end=None):
    current = today()
    if grain == 'year':
        try: year = int(period or current.year)
        except (TypeError, ValueError): raise DomainError('请选择统计年份') from None
        if not 1901 <= year <= current.year: raise DomainError('统计年份须在1901年至今年之间')
        first, last = day(f'{year}-01-01'), day(f'{year}-12-31')
    elif grain == 'month':
        first = day(str(period or current.isoformat()[:7]) + '-01', '统计月份')
        if first.year < 1901 or first > current: raise DomainError('请选择已开始的统计月份')
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    elif grain == 'custom':
        first, last = day(start, '起始日期'), day(end, '结束日期')
        if first.year < 1901 or last > current: raise DomainError('自定义日期须在1901年至今天之间')
    else: raise DomainError('统计范围须为year、month或custom')
    partial = last > current
    last = min(last, current)
    if first > last or (last-first).days > 366*30: raise DomainError('起止日期须按时间顺序且不超过30年')
    before = first-timedelta(days=1)
    previous_last = before
    previous_first = (before.replace(month=1, day=1) if grain == 'year' else
                      before.replace(day=1) if grain == 'month' else first-(last-first+timedelta(days=1)))
    comparison_label = {'month':'较上月末', 'year':'较上年末', 'custom':'较起始日前一天'}[grain]
    dates = [v['effective_on'] for i in family['items'] for v in i['versions']] + [l['effective_on'] for l in family['links']]
    market = Market(state)
    def projected(d): return snapshot(state, family, str(d), market=market) if dates and str(d) >= min(dates) else None
    ending, opening = snapshot(state, family, str(last), market=market), projected(before)
    previous = opening  # The comparison balance and opening balance share the same date.
    records = income_records(state, family, market=market)
    def flow(a, b):
        rows = [r for r in records if str(a) <= r['date'] <= str(b)]
        total = sum((r['value'] for r in rows), ZERO) if rows and all(r['value'] is not None for r in rows) else None
        return dict(value=total, count=len(rows), months=sorted({r['month'] for r in rows}), missing_fx=any(r['value'] is None for r in rows))
    income, previous_income = flow(first, last), flow(previous_first, previous_last)
    def scope(snap, key):
        if snap is None: return None
        category = 'cash' if key == 'liquid_cash' else key
        if category in ('cash','fixed','investment','receivable'):
            return sorted(r['id']+':'+r['share'] for r in snap['items'] if r['category'] == category)
        return sorted(r['id']+':'+r['share'] for r in snap['items'] if (r['category']=='debt') == (key=='total_debt')) if key in ('total_assets','total_debt') else sorted(r['id']+':'+r['share'] for r in snap['items'])
    def comparison(value, old, changed=False):
        amount = value-old if value is not None and old is not None else None
        reason = '缺少上期记录或金额' if amount is None else '上期金额不大于零' if old <= 0 else '含新增、移除或份额调整' if changed else ''
        return dict(previous=old, amount=amount, rate=amount/old if amount is not None and old>0 else None, reason=reason)
    def value_of(snap, key):
        if snap is None: return None
        if key == 'liquid_cash': return snap['safety']['available_cash']
        if key in ('fixed','investment','receivable'):
            return next(c['value'] for c in snap['categories'] if c['key'] == key)
        return snap[key]
    metrics = {}
    for key in ('total_assets','net_assets','total_debt','debt_ratio','liquid_cash','fixed','investment','receivable'):
        value = value_of(ending,key)
        changed = previous is not None and scope(previous,key) != scope(ending,key)
        metric = comparison(value, value_of(previous,key), changed)
        old = value_of(opening,key)
        metric.update(value=value, period_change=value-old if value is not None and old is not None else None)
        metrics[key] = metric
    metrics['income'] = dict(value=income['value'], **comparison(income['value'], previous_income['value']))
    repayment = debt_summary(state, family, ending, market=market)
    cash_cover = dict(month=repayment['month'], monthly_repayment=repayment['month_due'], months=repayment['cash_coverage'],
                      unconfigured=repayment['unconfigured'], missing_fx=repayment['missing_fx'],
                      basis='流动现金除以所选结束月份的计划还款额，仅作月数折算，不包含生活开支和保费')
    return wire(dict(period=dict(grain=grain,start=str(first),end=str(last),opening_date=str(before),comparison_start=str(previous_first),comparison_end=str(previous_last),partial=partial,comparison_label=comparison_label),
                     snapshot=ending,metrics=metrics,cash_cover=cash_cover,income=income,previous_income=previous_income,revision=state['revision']))
