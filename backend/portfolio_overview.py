"""Read-only rollup of the same investment projections used by portfolio detail."""

from .common import ZERO, day, ratio
from .trade_analytics import XIRR_BASIS, xirr


def decimal_text(value):
    return format(value.normalize(), 'f') if value is not None else None


def complete_sum(values):
    total = ZERO
    for value in values:
        if value is None:
            return None
        total += value
    return total


def return_metric(profit, rate, **details):
    return {'amount': decimal_text(profit), 'rate': decimal_text(rate), **details}


def period_summary(snapshots, start, end, *, check_daily_close=False):
    if end is None:
        return return_metric(None, None, start_date=None, end_date=None, estimated=False)

    profit, cost = ZERO, ZERO
    incomplete, estimated = False, False
    for snapshot in snapshots:
        history = snapshot['history']
        origin = day(history[0]['date'])
        first = max(0, (start - origin).days)
        last = (end - origin).days
        if last < first:
            continue
        cost += history[last]['investment_cost']
        for row in history[first:last + 1]:
            estimated |= row['estimated'] or (check_daily_close and row['pending_close'])
            if row['incomplete'] or row['day_profit'] is None:
                incomplete = True
            else:
                profit += row['day_profit']

    return return_metric(None if incomplete else profit, None if incomplete else ratio(profit, cost),
                         start_date=str(start), end_date=str(end), estimated=estimated)


def portfolio_overview(projections, as_of, revision):
    snapshots = [snapshot for _, snapshot, _ in projections]
    summaries = [summary for _, _, summary in projections]
    assets = complete_sum(snapshot['total_assets'] for snapshot in snapshots)
    profit = complete_sum(summary['profit'] for summary in summaries)
    cost = sum((summary['cost'] for summary in summaries), ZERO)
    first = min((day(portfolio['start_date']) for portfolio, _, _ in projections), default=None)

    if profit is None:
        annual = {'rate': None, 'status': '有未确认流水或缺少估值，暂不年化'}
    elif cost <= ZERO:
        annual = {'rate': None, 'status': '尚无可核实买入本金，不计算年化'}
    else:
        annual = xirr((flow['date'], flow['amount']) for summary in summaries for flow in summary['cash_flows'])
    rate = annual['rate']

    latest_quote = max((next((row['date'] for row in reversed(snapshot['history'])
                             if row['has_investment_quote']), '') for snapshot in snapshots), default='')
    trading_day = day(latest_quote) if latest_quote else None
    rows = [
        {'id': portfolio['id'], 'name': portfolio['name'], 'archived': portfolio.get('archived', False),
         'start_date': portfolio['start_date'], 'running_days': (as_of - day(portfolio['start_date'])).days + 1,
         'total_assets': decimal_text(snapshot['total_assets']),
         'cumulative': return_metric(summary['profit'], summary['return_on_cost'])}
        for portfolio, snapshot, summary in projections
    ]
    return {
        'date': str(as_of), 'revision': revision, 'currency': 'CNY', 'portfolios': rows,
        'total_assets': decimal_text(assets) if rows else None,
        'start_date': str(first) if first else None,
        'running_days': (as_of - first).days + 1 if first else None,
        'estimated': any(snapshot['estimated'] for snapshot in snapshots)
                     or any(summary['provisional'] or summary['pending_settlement'] for summary in summaries),
        'incomplete': any(snapshot['incomplete'] for snapshot in snapshots) or profit is None,
        'cumulative': return_metric(profit, ratio(profit, cost) if profit is not None else None),
        'annualized': return_metric(cost * rate if rate is not None else None, rate, status=annual['status'],
                                   xirr_basis=XIRR_BASIS),
        'year_to_date': period_summary(snapshots, as_of.replace(month=1, day=1), as_of),
        'month_to_date': period_summary(snapshots, as_of.replace(day=1), as_of),
        'latest_trading_day': period_summary(snapshots, trading_day, trading_day, check_daily_close=True),
    }
