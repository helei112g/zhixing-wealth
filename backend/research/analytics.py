"""Local daily calculations. Supplier requests are deliberately unavailable here."""
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal
from math import sqrt
from ..common import DomainError, day, dec, ds, wire
from .metrics import full_window, quantiles, years_before


def sample(values, limit=1000):
    if len(values) <= limit: return values
    step = max(1, len(values)//limit)
    return values[::step] + ([values[-1]] if values[-1] != values[::step][-1] else [])


def history_window(rows, at, years, frequency='daily', allow_partial=False):
    if years:
        complete = full_window(rows, at, years, frequency)
        if complete: return complete, False
        if not allow_partial: return [], False
    previous = [r for r in rows if r['date'] < at]
    if not previous: return [], False
    # An expanding window requires at least a year, density, and no long gap.
    span = (day(at)-day(previous[0]['date'])).days
    if span < 365 or frequency != 'daily' or len(previous) < span/365.25*180: return [], False
    if any((day(b['date'])-day(a['date'])).days > 45 for a,b in zip(previous,previous[1:])): return [], False
    if (day(at)-day(previous[-1]['date'])).days > 45: return [], False
    return previous, bool(years)


def ranks(series, allow_zero=False):
    rows = sorted([r for r in series.get('rows',[]) if r.get('value') is not None],key=lambda r:r['date'])
    if not rows: return {}
    valid=lambda v: dec(v)>=0 if allow_zero else dec(v)>0
    current = rows[-1]; positive = [r for r in rows if valid(r['value'])]
    result = {}
    for y in (3,5,10,0):
        shorter = bool(y and day(rows[0]['date']) > years_before(current['date'],y))
        h, partial = history_window(positive,current['date'],y,series.get('frequency','daily'),allow_partial=shorter)
        vals = [dec(r['value']) for r in h]
        rank = (sum(v < dec(current['value']) for v in vals)+Decimal('.5')*sum(v == dec(current['value']) for v in vals))/len(vals) if vals and valid(current['value']) else None
        result[str(y)] = dict(rank=ds(rank) if rank is not None else None, partial=partial,
                             count=len(vals), start=h[0]['date'] if h else None,end=h[-1]['date'] if h else None,
                             mean=ds(sum(vals)/len(vals)) if vals else None, quantiles=quantiles(vals) if vals else {})
    return result


def valuation_curve(series, prices, window):
    rows = sorted((r for r in series.get('rows', []) if r.get('value') is not None), key=lambda r: r['date'])
    if not rows:
        return []

    dates = [r['date'] for r in rows]
    prefix = [Decimal(0)]
    for r in rows:
        prefix.append(prefix[-1] + dec(r['value']))
    gap_indices = [i for i in range(1, len(rows)) if (day(dates[i]) - day(dates[i - 1])).days > 45]
    close = {r['date']: r['close'] for r in prices}
    price_dates = sorted(close)
    start = str(years_before(dates[-1], window)) if window else dates[0]
    out = []
    for i, r in enumerate(rows):
        if r['date'] < start:
            continue
        point = dict(r, close=close.get(r['date']))
        for years in (3, 5, 10):
            boundary = str(years_before(r['date'], years))
            lo = bisect_right(dates, boundary, 0, i + 1)
            count = i + 1 - lo
            # A long holiday may exceed seven calendar days. Require the source
            # to span the boundary instead; never fabricate observations on closed days.
            spans_start = 0 < lo <= i and (day(dates[lo]) - day(dates[lo - 1])).days <= 45
            first_session = bisect_right(price_dates, boundary)
            if spans_start and 0 < first_session < len(price_dates):
                spans_start = dates[lo] <= price_dates[first_session]
            no_gap = bisect_right(gap_indices, i) == bisect_right(gap_indices, lo)
            complete = spans_start and count >= 180 * years and no_gap and series.get('frequency') == 'daily'
            point['mean' + str(years)] = ds((prefix[i + 1] - prefix[lo]) / count) if complete else None
        out.append(point)
    return sample(out)


def flow_months(rows, prices, stock_field, flow_field, quarterly=False, missing_dates=(), *,
                purchase_field=None, repayment_field=None, ratio_field=None):
    groups=defaultdict(list)
    for r in sorted(rows,key=lambda r:r['date']): groups[r['date'][:7]].append(r)
    output = []
    previous = None
    calendar = defaultdict(set)
    for row in prices:
        calendar[row['date'][:7]].add(row['date'])
    last_month = max(calendar, default='')
    gap_months = {d[:7] for d in missing_dates}
    for month, values in sorted(groups.items()):
        stock = next((row for row in reversed(values) if row.get(stock_field) is not None), None)
        expected = calendar.get(month)
        def total(field):
            known = [row for row in values if row.get(field) is not None] if field else []
            complete = len(known) == 1 if quarterly else bool(
                expected and month < last_month and month not in gap_months and expected <= {row['date'] for row in known})
            amount = ds(sum((dec(row[field]) for row in known), Decimal(0))) if complete else None
            return amount, complete, len(known)

        flow, complete, count = total(flow_field)
        value = stock[stock_field] if stock else None
        change = ds(dec(value) - dec(previous)) if value is not None and previous is not None else None
        observation = dict(date=stock['date'] if stock else values[-1]['date'], stock=value, flow=flow,
                           change=change, complete=complete, observations=count)
        if purchase_field:
            observation['purchase'] = total(purchase_field)[0]
        if repayment_field:
            observation['repayment'] = total(repayment_field)[0]
        if ratio_field:
            observation['ratio'] = stock.get(ratio_field) if stock else None
        output.append(observation)
        previous = value
    return output


def studio_analysis(service,iid,basis,method,window,metric):
    if window not in (0,3,5,10): raise DomainError('显示窗口无效')
    with service.store.lock:
        d=service.detail(iid,basis,method,window)
        v=service.module(iid,'valuations') or {}; price=service.module(iid,basis) or {}
        series=v.get('series',{}).get(metric,{}).get(method,{})
        # Official older sequences remain readable; unsupported methods do not silently fall back.
        price_rows=price.get('rows',[]); activity=d['market_activity']; fundamental=d['fundamental_activity']
    changes=[float(dec(b['close'])/dec(a['close'])-1) for a,b in zip(price_rows,price_rows[1:])][-250:]
    vol=None
    if len(changes)>=250:
        avg=sum(changes)/len(changes); vol=ds(dec(str(sqrt(sum((r-avg)**2 for r in changes)/(len(changes)-1))*sqrt(250))))
    weighted=d['memberships_complete'] and bool(d['companies']) and all(r.get('weight') is not None for r in d['companies'])
    hhi=sum((dec(r['weight'])/100)**2 for r in d['companies']) if weighted else None
    missing = price.get('missing_dates', [])
    margin = activity.get('margin', [])
    northbound = activity.get('northbound', [])
    north_args = ('shareholdingsMoney', 'netBuyAmount')
    funds = dict(
        etf=flow_months(fundamental, price_rows, 'fet_as_ma', 'fet_snif_ma', missing_dates=missing),
        margin=flow_months(margin, price_rows, 'financingBalance', 'financingNetPurchaseAmount', missing_dates=missing,
                          purchase_field='financingPurchaseAmount', repayment_field='financingRepaymentAmount',
                          ratio_field='financingBalanceToMarketCap'),
        securities=flow_months(margin, price_rows, 'securitiesBalance', 'securitiesNetSellAmount', missing_dates=missing,
                              purchase_field='securitiesSellAmount', repayment_field='securitiesRepaymentAmount',
                              ratio_field='securitiesBalanceToMarketCap'),
        northbound=flow_months([r for r in northbound if r['date'] >= '2024-08-16'], price_rows, *north_args,
                              quarterly=True, ratio_field='shareholdingsMoneyToMarketCap'),
        northbound_daily=flow_months([r for r in northbound if r['date'] < '2024-08-16'], price_rows, *north_args,
                                    missing_dates=missing, ratio_field='shareholdingsMoneyToMarketCap'),
        capitalization={k: flow_months(fundamental, price_rows, k, None) for k in ('mc', 'cmc', 'ecmc')},
    )
    return wire(dict(analysis_id=d['analysis_id'],research_revision=d['research_revision'],metric=metric,method=method,window=window,basis=basis,
                     ranks=ranks(series,allow_zero=metric=='dividend_yield'),valuation_curve=valuation_curve(series,price_rows,window),
                     funds=funds,
                     volatility_250=vol,concentration_hhi=hhi))
