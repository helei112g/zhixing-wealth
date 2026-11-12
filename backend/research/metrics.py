"""Deterministic Decimal calculations. Render sampling never determines extremes."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from ..common import day, dec, ds
from ..trading_calendar import no_expected_session

METHOD_VERSION = 'index-studio-6'
WINDOWS = (3, 5, 10)
PRIMARY_VALUATIONS = ('pe', 'pb', 'dividend_yield', 'ps')
VALUATION_GROUPS = (*PRIMARY_VALUATIONS, 'unclassified')


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
    if not previous or len(previous) < 180 * years: return []
    earlier = [r['date'] for r in rows if day(r['date']) <= start]
    if not earlier or (day(previous[0]['date']) - day(max(earlier))).days > 45: return []
    if (end-day(previous[-1]['date'])).days>45:return []
    if any((day(b['date']) - day(a['date'])).days > 45 for a, b in zip(previous, previous[1:])): return []
    for year in range(start.year + 1, end.year):
        if sum(r['date'].startswith(str(year)) for r in previous) < 180: return []
    return previous


def drawdown(rows, asof, years, allow_partial=False):
    rows = clean_prices(rows, asof)
    if not rows: return {'status': 'missing', 'high_date': None, 'high_close': None, 'drawdown_ratio': None}
    at = rows[-1]['date']; start = years_before(at, years) if years else day(rows[0]['date'])
    partial = bool(years and not full_window(rows, at, years))
    if partial and not (allow_partial and day(rows[0]['date']) > start and not any((day(b['date'])-day(a['date'])).days>45 for a,b in zip(rows,rows[1:]))):
        return {'status': 'insufficient_history', 'high_date': None, 'high_close': None, 'drawdown_ratio': None}
    sample = [r for r in rows if day(r['date']) >= start]
    peak = dec(sample[0]['close']); high_date = sample[0]['date']; low = Decimal(0); curve = []
    for row in sample:
        v = dec(row['close'])
        if v >= peak: peak = v; high_date = row['date']
        ratio = v / peak - 1; low = min(low, ratio)
        curve.append({'date': row['date'], 'close': row['close'], 'drawdown_ratio': ds(ratio)})
    return {'status': 'available', 'partial':partial, 'from': sample[0]['date'], 'to': at, 'high_date': high_date,
            'high_close': ds(peak), 'drawdown_ratio': ds(dec(sample[-1]['close']) / peak - 1),
            'max_drawdown_ratio': ds(low), 'high_date_policy': 'latest_equal_high', 'curve': curve}


def valuation(series, asof, allow_zero=False):
    rows = sorted([r for r in series.get('rows', []) if r.get('value') is not None and day(r['date']) <= day(asof)], key=lambda r: r['date'])
    if len({r['date'] for r in rows}) != len(rows): raise ValueError('估值观测重复')
    current = rows[-1] if rows else None
    valid=lambda v: dec(v)>=0 if allow_zero else dec(v)>0
    windows = {}
    for years in WINDOWS:
        history = full_window([r for r in rows if valid(r['value'])], current['date'], years, series.get('frequency', 'daily')) if current else []
        if not history or not valid(current['value']):
            windows[str(years)] = {'status': 'insufficient_history', 'rank': None}; continue
        v = dec(current['value']); vals = [dec(r['value']) for r in history if valid(r['value'])]
        rank = (sum(x < v for x in vals) + Decimal('.5') * sum(x == v for x in vals)) / len(vals) if vals else None
        windows[str(years)] = {'status': 'available' if rank is not None else 'insufficient_history',
                               'rank': ds(rank) if rank is not None else None, 'count': len(vals),
                               'quantiles': quantiles(vals)}
    return dict(source=series.get('source'), field=series.get('field'), frequency=series.get('frequency'),
                date=current['date'] if current else None, current=current['value'] if current else None, windows=windows)


def valuation_position(metric, valuations):
    """Put all primary metrics on a scale from cheaper to more expensive."""
    if metric is None:
        return None
    raw_windows = valuations.get(metric, {}).get('windows', {})
    windows = {}
    for years in ('5', '10'):
        raw_rank = raw_windows.get(years, {}).get('rank')
        if raw_rank is None:
            windows[years] = {'rank': None, 'grade': None}
            continue
        rank = dec(raw_rank)
        if metric == 'dividend_yield':
            rank = 1 - rank
        if rank <= Decimal('.2'):
            grade = 'low'
        elif rank <= Decimal('.5'):
            grade = 'fair_low'
        elif rank < Decimal('.8'):
            grade = 'fair_high'
        else:
            grade = 'high'
        windows[years] = {'rank': ds(rank), 'grade': grade}
    return {'metric': metric, 'windows': windows}


def financial_totals(rows, members, period):
    weights = {m['code']: dec(m.get('weight') or '0') for m in members}
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


def price_changes(rows, asof, *, missing_dates=(), annual=False):
    """Calendar week/month/YTD anchors use the last close before the boundary."""
    at = day(asof)
    years = range(at.year - 1, at.year - 4, -1) if annual else ()
    result = dict.fromkeys(('daily', 'weekly', 'monthly', 'ytd'))
    if annual:
        result['annual'] = {str(year): None for year in years}
    if not rows:
        return result
    latest=rows[-1];end=day(latest['date'])
    monday=at-timedelta(days=at.weekday());year=at.replace(month=1,day=1)
    month = at.replace(day=1)
    def change(base, close=latest):
        if not base:return None
        return dict(ratio=ds(dec(close['close'])/dec(base['close'])-1),from_date=base['date'],to_date=close['date'])

    def gap_is_closed(last, through):
        if no_expected_session('CN', last, str(through)):
            return True
        start = day(last)
        return all((start + timedelta(days=offset)).weekday() >= 5
                   for offset in range(1, (through - start).days + 1))

    previous=rows[-2] if len(rows)>1 and (end-day(rows[-2]['date'])).days<=15 else None
    def anchor(boundary):
        before=[r for r in rows if day(r['date'])<boundary]
        if not before or (boundary - day(before[-1]['date'])).days > 15:
            return None
        last = before[-1]
        if any(last['date'] < missing < str(boundary) for missing in missing_dates):
            return None
        # The index list only shows a period return with a verified closing anchor.
        if annual and not gap_is_closed(last['date'], boundary - timedelta(days=1)):
            return None
        return last

    if previous:
        if (any(previous['date'] < missing < latest['date'] for missing in missing_dates) or
                annual and not gap_is_closed(previous['date'], end - timedelta(days=1))):
            previous = None
    result.update(daily=change(previous), weekly=change(anchor(monday)) if end >= monday else None,
                  monthly=change(anchor(month)) if end >= month else None,
                  ytd=change(anchor(year)) if end >= year else None)
    for completed_year in years:
        close = anchor(date(completed_year + 1, 1, 1))
        if not close:
            continue
        result['annual'][str(completed_year)] = change(anchor(date(completed_year, 1, 1)), close)
    return result


def moving_averages(rows,window,missing_dates=()):
    """SMA over trading observations, computed before the display-window cut."""
    if not rows:return []
    starts=years_before(rows[-1]['date'],window) if window else day(rows[0]['date']);sums={n:Decimal(0) for n in (51,120,250,850)};out=[]
    for i,row in enumerate(rows):
        point=dict(row)
        for n in sums:
            sums[n]+=dec(row['close'])
            if i>=n:sums[n]-=dec(rows[i-n]['close'])
            point['ma'+str(n)]=ds(sums[n]/n) if i+1>=n and not any(rows[i-n+1]['date']<=d<=row['date'] for d in missing_dates) else None
        if day(row['date'])>=starts:out.append(point)
    return out
