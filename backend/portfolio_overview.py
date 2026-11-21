"""Read-only rollup of the same investment projections used by portfolio detail."""

from decimal import Decimal
from typing import NamedTuple

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


def is_provisional(summary):
    return summary['provisional'] or summary['pending_settlement']


class PeriodChange(NamedTuple):
    profit: Decimal | None
    cost: Decimal
    estimated: bool

    def metric(self, start, end):
        rate = ratio(self.profit, self.cost) if self.profit is not None else None
        return return_metric(self.profit, rate, start_date=str(start) if start else None,
                             end_date=str(end) if end else None, estimated=self.estimated)


def period_changes(projections, investment_at, start, end, *, check_daily_close=False):
    changes = {}
    if end is None:
        return changes
    for portfolio, snapshot, current in projections:
        history = snapshot['history']
        origin = day(history[0]['date'])
        first = max(0, (start - origin).days)
        last = (end - origin).days
        if last < first:
            continue

        incomplete, estimated = False, False
        # Include the baseline in the quality check because its value is
        # subtracted from the ending investment profit.
        for row in history[max(0, first - 1):last + 1]:
            estimated |= row['investment_estimated'] or (check_daily_close and row['pending_close'])
            incomplete |= row['investment_incomplete']

        ending = current if last == len(history) - 1 else investment_at(portfolio['id'], str(end))
        estimated |= is_provisional(ending)
        prior_profit = ZERO
        if first > 0:
            prior = investment_at(portfolio['id'], history[first - 1]['date'])
            estimated |= is_provisional(prior)
            prior_profit = prior['profit']
        profit = None
        if not incomplete and ending['profit'] is not None and prior_profit is not None:
            profit = ending['profit'] - prior_profit
        changes[portfolio['id']] = PeriodChange(profit, ending['cost'], estimated)

    return changes


def combined_period(changes, start, end):
    total = PeriodChange(
        complete_sum(change.profit for change in changes.values()) if end else None,
        sum((change.cost for change in changes.values()), ZERO),
        any(change.estimated for change in changes.values()),
    )
    return total.metric(start, end)


def portfolio_overview(projections, as_of, revision, *, investment_at):
    snapshots = [snapshot for _, snapshot, _ in projections]
    summaries = [summary for _, _, summary in projections]
    assets = complete_sum(snapshot['total_assets'] for snapshot in snapshots)
    market_value = complete_sum(summary['market_value'] for summary in summaries)
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
    daily = period_changes(projections, investment_at, trading_day, trading_day, check_daily_close=True)
    unavailable = PeriodChange(None, ZERO, False)
    rows = [
        {'id': portfolio['id'], 'name': portfolio['name'], 'archived': portfolio.get('archived', False),
         'start_date': portfolio['start_date'], 'running_days': (as_of - day(portfolio['start_date'])).days + 1,
         'total_assets': decimal_text(snapshot['total_assets']),
         'market_value': decimal_text(summary['market_value']),
         'cumulative': return_metric(summary['profit'], summary['return_on_cost']),
         'latest_trading_day': daily.get(portfolio['id'], unavailable).metric(trading_day, trading_day)}
        for portfolio, snapshot, summary in projections
    ]

    def period_since(start):
        return combined_period(period_changes(projections, investment_at, start, as_of), start, as_of)

    return {
        'date': str(as_of), 'revision': revision, 'currency': 'CNY', 'portfolios': rows,
        'total_assets': decimal_text(assets) if rows else None,
        'market_value': decimal_text(market_value) if rows else None,
        'start_date': str(first) if first else None,
        'running_days': (as_of - first).days + 1 if first else None,
        'estimated': any(snapshot['investment_estimated'] for snapshot in snapshots)
                     or any(is_provisional(summary) for summary in summaries),
        'incomplete': market_value is None or profit is None,
        'cumulative': return_metric(profit, ratio(profit, cost) if profit is not None else None),
        'annualized': return_metric(cost * rate if rate is not None else None, rate, status=annual['status'],
                                   xirr_basis=XIRR_BASIS),
        'year_to_date': period_since(as_of.replace(month=1, day=1)),
        'month_to_date': period_since(as_of.replace(day=1)),
        'latest_trading_day': combined_period(daily, trading_day, trading_day),
    }
