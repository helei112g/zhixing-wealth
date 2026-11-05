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
    rows = sorted([r for r in series.get('rows',[]) if r.get('value') is not None],key=lambda r:r['date'])
    if not rows: return []
    dates=[r['date'] for r in rows]; prefix=[Decimal(0)]
    for r in rows: prefix.append(prefix[-1]+dec(r['value']))
    gap_indices=[i for i in range(1,len(rows)) if (day(dates[i])-day(dates[i-1])).days>45]
    close={r['date']:r['close'] for r in prices}; start=str(years_before(dates[-1],window)) if window else dates[0]
    out=[]
    for i,r in enumerate(rows):
        if r['date']<start: continue
        point=dict(r,close=close.get(r['date']))
        for y in (3,5,10):
            boundary=str(years_before(r['date'],y)); lo=bisect_right(dates,boundary,0,i+1); n=i+1-lo
            no_gap=bisect_right(gap_indices,i)==bisect_right(gap_indices,lo)
            complete=n>=180*y and lo<len(rows) and (day(dates[lo])-day(boundary)).days<=7 and no_gap and series.get('frequency')=='daily'
            point['mean'+str(y)]=ds((prefix[i+1]-prefix[lo])/n) if complete else None
        out.append(point)
    return sample(out)


def flow_months(rows, prices, stock_field, flow_field, quarterly=False, missing_dates=()):
    groups=defaultdict(list)
    for r in sorted(rows,key=lambda r:r['date']): groups[r['date'][:7]].append(r)
    output=[]; previous=None; calendar={r['date'] for r in prices}
    for month,values in sorted(groups.items()):
        stock=next((r for r in reversed(values) if r.get(stock_field) is not None),None)
        known=[r for r in values if r.get(flow_field) is not None] if flow_field else []
        expected={d for d in calendar if d.startswith(month)}
        complete=bool(expected and expected<={r['date'] for r in known} and month<max(calendar)[:7]) if calendar else False
        if any(d.startswith(month) for d in missing_dates): complete=False
        if quarterly: complete=len(known)==1
        total=sum((dec(r[flow_field]) for r in known),Decimal(0)) if complete and known else None
        value=stock.get(stock_field) if stock else None
        change=ds(dec(value)-dec(previous)) if value is not None and previous is not None else None
        output.append(dict(date=stock['date'] if stock else values[-1]['date'],stock=value,flow=ds(total) if total is not None else None,change=change,complete=complete,observations=len(known)))
        previous=value
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
    funds=dict(etf=flow_months(fundamental,price_rows,'fet_as_ma','fet_snif_ma',missing_dates=price.get('missing_dates',[])),
               margin=flow_months(activity.get('margin',[]),price_rows,'financingBalance','financingNetPurchaseAmount',missing_dates=price.get('missing_dates',[])),
               northbound=flow_months([r for r in activity.get('northbound',[]) if r['date']>='2024-08-16'],price_rows,'shareholdingsMoney','netBuyAmount',True),
               northbound_daily=flow_months([r for r in activity.get('northbound',[]) if r['date']<'2024-08-16'],price_rows,'shareholdingsMoney','netBuyAmount'),
               capitalization={k:flow_months(fundamental,price_rows,k,None) for k in ('mc','cmc','ecmc')})
    return wire(dict(analysis_id=d['analysis_id'],research_revision=d['research_revision'],metric=metric,method=method,window=window,basis=basis,
                     ranks=ranks(series,allow_zero=metric=='dividend_yield'),valuation_curve=valuation_curve(series,price_rows,window),
                     funds=funds,
                     volatility_250=vol,concentration_hhi=hhi))
