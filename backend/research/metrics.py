"""Deterministic Decimal calculations. Render sampling never determines extremes."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from ..common import day, dec, ds

METHOD_VERSION = 'index-library-2'
WINDOWS = (3, 5, 10)


def years_before(value, years):
    d = day(value); year = d.year - years
    return d.replace(year=year, day=min(d.day, calendar.monthrange(year, d.month)[1]))


def clean_prices(rows, asof):
    result = {}; end = day(asof)
    for row in rows:
        d = day(row['date'])
        if d > end: continue
        if str(d) in result: raise ValueError('指数日线存在重复日期')
        result[str(d)] = {'date': str(d), 'close': ds(dec(row['close'], positive=True))}
    return [result[d] for d in sorted(result)]


def full_window(rows, at, years, frequency='daily'):
    start = years_before(at, years); end = day(at)
    previous = [r for r in rows if start <= day(r['date']) < end]
    if frequency == 'snapshot': return []
    if frequency == 'monthly':
        # Only completed calendar months; never multiply sparse points into days.
        stop = end.replace(day=1); start = years_before(stop, years)
        months = {r['date'][:7]: r for r in rows if start <= day(r['date']) < stop}
        return list(months.values()) if len(months) == years * 12 else []
    if not previous or len(previous) < 180 * years or day(previous[0]['date']) > start + timedelta(days=7): return []
    if (end-day(previous[-1]['date'])).days>45:return []
    if any((day(b['date']) - day(a['date'])).days > 45 for a, b in zip(previous, previous[1:])): return []
    for year in range(start.year + 1, end.year):
        if sum(r['date'].startswith(str(year)) for r in previous) < 180: return []
    return previous


def drawdown(rows, asof, years):
    rows = clean_prices(rows, asof)
    if not rows: return {'status': 'missing', 'high_date': None, 'high_close': None, 'drawdown_ratio': None}
    at = rows[-1]['date']; start = years_before(at, years)
    if not full_window(rows, at, years):
        return {'status': 'insufficient_history', 'high_date': None, 'high_close': None, 'drawdown_ratio': None}
    sample = [r for r in rows if day(r['date']) >= start]
    peak = dec(sample[0]['close']); high_date = sample[0]['date']; low = Decimal(0); curve = []
    for row in sample:
        v = dec(row['close'])
        if v >= peak: peak = v; high_date = row['date']
        ratio = v / peak - 1; low = min(low, ratio)
        curve.append({'date': row['date'], 'close': row['close'], 'drawdown_ratio': ds(ratio)})
    return {'status': 'available', 'from': sample[0]['date'], 'to': at, 'high_date': high_date,
            'high_close': ds(peak), 'drawdown_ratio': ds(dec(sample[-1]['close']) / peak - 1),
            'max_drawdown_ratio': ds(low), 'high_date_policy': 'latest_equal_high', 'curve': curve}


def valuation(series, asof):
    rows = sorted([r for r in series.get('rows', []) if r.get('value') is not None and day(r['date']) <= day(asof)], key=lambda r: r['date'])
    if len({r['date'] for r in rows}) != len(rows): raise ValueError('估值观测重复')
    current = rows[-1] if rows else None
    windows = {}
    for years in WINDOWS:
        history = full_window([r for r in rows if dec(r['value'])>0], current['date'], years, series.get('frequency', 'daily')) if current else []
        if not history or dec(current['value']) <= 0:
            windows[str(years)] = {'status': 'insufficient_history', 'rank': None}; continue
        v = dec(current['value']); vals = [dec(r['value']) for r in history if dec(r['value']) > 0]
        rank = (sum(x < v for x in vals) + Decimal('.5') * sum(x == v for x in vals)) / len(vals) if vals else None
        windows[str(years)] = {'status': 'available' if rank is not None else 'insufficient_history',
                               'rank': ds(rank) if rank is not None else None, 'count': len(vals),
                               'quantiles': quantiles(vals)}
    return dict(source=series.get('source'), field=series.get('field'), frequency=series.get('frequency'),
                date=current['date'] if current else None, current=current['value'] if current else None, windows=windows)


def financial_totals(rows, members, period):
    weights = {m['code']: dec(m.get('weight', '0')) for m in members}
    out = {}; previous = str(int(period[:4]) - 1) + period[4:]
    for metric in ('revenue', 'profit', 'operating_cf'):
        eligible = {m['code'] for m in members if metric != 'operating_cf' or
                    (m.get('industry') and not any(t in m['industry'] for t in ('银行', '证券', '保险', '金融')))}
        totals = []
        for p in (period, previous):
            valid = {r['code']: r for r in rows if r['code'] in eligible and r['period'] == p and r.get(metric) is not None}
            coverage_weight = sum(weights[c] for c in valid)
            denominator = sum(weights[c] for c in eligible)
            coverage = coverage_weight / denominator if denominator else None
            total = sum((dec(r[metric]) for r in valid.values()), Decimal(0)) if valid else None
            totals.append(dict(amount=ds(total) if total is not None else None, count=len(valid), denominator=len(eligible),
                               coverage=ds(coverage) if coverage is not None else None,count_coverage=ds(Decimal(len(valid))/len(eligible)) if eligible else None,missing_codes=sorted(eligible-set(valid))))
        a, b = totals; status = 'available'; growth = None
        if any(t['amount'] is None or t['coverage'] is None or dec(t['coverage']) < Decimal('.8') for t in totals): status = 'insufficient_coverage'
        elif dec(b['amount']) <= 0: status = 'nonpositive_base'
        else: growth = ds(dec(a['amount']) / dec(b['amount']) - 1)
        out[metric] = dict(current=a, previous=b, growth=growth, status=status,
                           change=ds(dec(a['amount']) - dec(b['amount'])) if a['amount'] is not None and b['amount'] is not None else None,
                           yoy_basis='independent_period_totals',excluded_count=len(members)-len(eligible),excluded_weight=ds(sum(weights.values())-sum(weights[c] for c in eligible)))
    return out


def quantiles(values):
    ordered=sorted(values)
    result={}
    for q in (10,25,50,75,90):
        position=Decimal(len(ordered)-1)*q/100
        lower=int(position);upper=min(lower+1,len(ordered)-1)
        result['p'+str(q)]=ds(ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower))
    return result


def price_changes(rows,asof):
    """Calendar week/YTD anchors use the last close strictly before the boundary."""
    if not rows:return {k:None for k in ('daily','weekly','ytd')}
    latest=rows[-1];end=day(latest['date']);at=day(asof)
    monday=at-timedelta(days=at.weekday());year=at.replace(month=1,day=1)
    def change(base):
        if not base:return None
        return dict(ratio=ds(dec(latest['close'])/dec(base['close'])-1),from_date=base['date'],to_date=latest['date'])
    previous=rows[-2] if len(rows)>1 and (end-day(rows[-2]['date'])).days<=15 else None
    def anchor(boundary):
        before=[r for r in rows if day(r['date'])<boundary]
        return before[-1] if before and (boundary-day(before[-1]['date'])).days<=15 else None
    return dict(daily=change(previous),weekly=change(anchor(monday)) if end>=monday else None,
                ytd=change(anchor(year)) if end>=year else None)


def moving_averages(rows,window):
    """SMA over trading observations, computed before the display-window cut."""
    if not rows:return []
    starts=years_before(rows[-1]['date'],window);sums={n:Decimal(0) for n in (51,120,250,850)};out=[]
    for i,row in enumerate(rows):
        point=dict(row)
        for n in sums:
            sums[n]+=dec(row['close'])
            if i>=n:sums[n]-=dec(rows[i-n]['close'])
            point['ma'+str(n)]=ds(sums[n]/n) if i+1>=n else None
        if day(row['date'])>=starts:out.append(point)
    return out
