"""Shared date bounds and debt summary for the household ledger."""
import calendar
from .common import ZERO, day, dec
from .family_plans import installments
from .ledger import Market


def recording_bounds(family, date):
    asset_dates = [v['effective_on'] for i in family['items'] for v in i['versions']]
    asset_dates += [l['effective_on'] for l in family['links']]
    income_dates = [i['versions'][-1]['date'] for i in family.get('income', [])]
    dates = asset_dates + income_dates
    first = min(dates, default=None)
    return dict(first_date=first, asset_first_date=min(asset_dates, default=None),
                income_first_date=min(income_dates, default=None), last_date=date,
                years=list(range(int(date[:4]), int(first[:4])-1, -1)) if first else [],
                income_years=sorted({int(d[:4]) for d in income_dates}, reverse=True))


def debt_summary(state, family, snap, *, market=None):
    date = snap['as_of']; d = day(date)
    start, end = str(d.replace(day=1)), str(d.replace(day=calendar.monthrange(d.year, d.month)[1]))
    market = market if market is not None else Market(state)
    payments = []; unconfigured = []
    for row in snap['items']:
        if row['category'] != 'debt': continue
        plan = row.get('payment_plan')
        if plan and plan.get('mode') != 'none':
            generated = installments(plan, row['id'], row['name'], row['currency'], start, end)
            payments.extend(generated)
        else:
            legacy = [s for s in family['schedules'] if s['item_id'] == row['id'] and s['kind'] == 'debt'
                      and s['status'] != 'cancelled' and start <= s['due_date'] <= end]
            payments.extend(dict(s, currency=row['currency'], share=row['share']) for s in legacy)
            if not legacy and (row['value'] is None or row['value'] > 0): unconfigured.append(row['name'])
    amounts = []
    for p in payments:
        fx = market.fx(p['currency'], date)
        amount = dec(p['amount']) * dec(p['share'])
        amounts.append(amount * dec(fx['rate']) if fx else ZERO if amount == 0 else None)
    known = sum((a for a in amounts if a is not None), ZERO)
    missing_fx = any(a is None for a in amounts)
    due = None if unconfigured or missing_fx else known
    cash = snap['safety']['available_cash']
    return dict(total_debt=snap['total_debt'], month=start[:7], month_due=due, known_month_due=known,
                available_cash=cash, cash_coverage=cash/due if cash is not None and due is not None and due > 0 else None,
                unconfigured=unconfigured, missing_fx=missing_fx)
