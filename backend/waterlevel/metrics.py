"""Waterlevel statistics; intentionally distinct from index-studio statistics."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, localcontext
from statistics import median

from ..common import day, dec, ds
from ..research.lixinger import METHODS

METHOD_VERSION = 'waterlevel-v4'


def month_shift(month, offset):
    year, number = map(int, month[:7].split('-'))
    index = year * 12 + number - 1 + offset
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def years_before(value, years):
    stamp = day(value)
    return date(stamp.year - years, stamp.month, min(stamp.day, monthrange(stamp.year - years, stamp.month)[1]))


def usable(value, positive=False):
    return value is not None and (not positive or dec(value) > 0)


def monthly_rows(rows):
    """Keep the final observation, including an invalid final observation."""
    months = {}
    for row in rows:
        months[row['date'][:7]] = row
    return months


def complete_mean(months, end_month, count, positive=False):
    selected = [months.get(month_shift(end_month, -offset)) for offset in range(count)]
    values = [dec(row['value']) for row in selected if row and usable(row['value'], positive)]
    if len(values) != count:
        return dict(value=None, count=len(values), required=count, status='incomplete_months')
    return dict(value=ds(sum(values) / count), count=count, required=count, status='available')


def analyze(rows, asof, *, positive=False, stale_days=14, frequency='daily'):
    rows = sorted((row for row in rows if row['date'] <= asof), key=lambda row: row['date'])
    latest = rows[-1] if rows else None
    result = dict(current=latest['value'] if latest else None, date=latest['date'] if latest else None,
                  frequency=frequency, observations=len(rows), status='missing', windows={}, means={}, chart=[])
    if latest:
        result['status'] = ('invalid_current' if not usable(latest['value'], positive)
                            else 'stale' if (day(asof) - day(latest['date'])).days > stale_days
                            else 'available')

    months = monthly_rows(rows)
    completed = month_shift(asof, -1)
    valid = [row for row in rows if usable(row['value'], positive)]
    for years in (5, 10):
        mean = complete_mean(months, completed, years * 12, positive)
        mean['through_month'] = completed
        mean['deviation_ratio'] = (ds(dec(latest['value']) / dec(mean['value']) - 1)
                                   if latest and usable(latest['value'], positive) and mean['value'] is not None
                                   and dec(mean['value']) != 0 else None)
        result['means'][str(years)] = mean
        start = str(years_before(latest['date'] if latest else asof, years))
        sample = [row for row in valid if row['date'] >= start]
        complete = bool(valid and valid[0]['date'] <= start and len(sample) >= 30)
        percentile = None
        if complete and latest and usable(latest['value'], positive):
            with localcontext() as ctx:
                # The input guard accepts 19 integer digits, plus the 10 comparison decimals.
                ctx.prec = 32
                quantum = Decimal('0.0000000001')
                current = dec(latest['value']).quantize(quantum)
                lower = sum(dec(row['value']).quantize(quantum) < current for row in sample)
            percentile = ds(Decimal(lower) / len(sample))
        result['windows'][str(years)] = dict(percentile_ratio=percentile, count=len(sample),
                                           start=start, end=latest['date'] if latest else None,
                                           status='available' if percentile is not None else 'insufficient_history')

    chart_months = []
    if months:
        month = min(months)
        while month <= max(months):
            chart_months.append(month)
            month = month_shift(month, 1)
    for month in chart_months:
        row = months.get(month, dict(date=month + '-01', value=None))
        # Current month uses the last completed month; it never enters its own mean.
        end = month if month < asof[:7] else completed
        point = dict(date=row['date'], value=row['value'] if usable(row['value'], positive) else None)
        for years in (5, 10):
            point[f'mean{years}'] = complete_mean(months, end, years * 12, positive)['value']
        result['chart'].append(point)
    return result


def difference(left, right, *, factor=1):
    """Same-date intersection only; missing values never become zero or carry forward."""
    other = {row['date']: row for row in right}
    return [dict(date=row['date'], value=ds((dec(row['value']) - dec(other[row['date']]['value'])) * factor)
                 if row['value'] is not None and other[row['date']]['value'] is not None else None)
            for row in left if row['date'] in other]


def earnings_spread(pe, yields):
    inverse = [dict(date=row['date'], value=ds(100 / dec(row['value'])) if usable(row['value'], True) else None)
               for row in pe]
    return difference(inverse, yields)


def comparison(valuation, bond, sh, sz):
    """Align monthly references without filling gaps; normalize prices at one common month."""
    series = {key: {row['date'][:7]: row for row in rows}
              for key, rows in (('valuation', valuation), ('bond_inverse', bond), ('sh', sh), ('sz', sz))}
    base = next((month for month in sorted(series['valuation'])
                 if all(usable(series[key].get(month, {}).get('value'), True) for key in ('sh', 'sz'))), None)
    rows = []
    for month, point in series['valuation'].items():
        row = dict(point, date=month, valuation_date=point['date'])
        for key in ('bond_inverse', 'sh', 'sz'):
            reference = series[key].get(month, {})
            value = reference.get('value')
            row[key + '_date'] = reference.get('date')
            row[key] = (ds(dec(value) / dec(series[key][base]['value']) * 100)
                        if key in ('sh', 'sz') and base and month >= base and usable(value, True)
                        else value if key == 'bond_inverse' else None)
        rows.append(row)
    return dict(chart=rows, base_month=base, price_basis='price', normalization=100,
                alignment='各序列月末最后观测独立取样；同月对照，不表示同日比价')


def waterlevel(value):
    if value is None:
        return '数据不足'
    number = dec(value)
    return ('极低' if number < 20 else '偏低' if number < 40 else '中性' if number < 60
            else '偏高' if number < 80 else '极高')


def score(entries):
    """Input entries are fixed by the service, never by the display-method switch."""
    categories = {}
    for category in ('equity', 'rates', 'credit'):
        members = []
        for entry in entries:
            if entry['category'] != category:
                continue
            stats = entry['statistics']
            window = next((years for years in ('10', '5')
                           if stats['windows'][years]['percentile_ratio'] is not None), None)
            value = None
            if window and stats['status'] == 'available':
                value = dec(stats['windows'][window]['percentile_ratio']) * 100
                if entry.get('inverse'):
                    value = 100 - value
            members.append(dict(id=entry['id'], label=entry['label'], method=entry.get('method'),
                                date=stats['date'], window=int(window) if window else None,
                                score=ds(value) if value is not None else None,
                                inverse=bool(entry.get('inverse')), status=stats['status'],
                                count=stats['windows'][window]['count'] if window else 0))
        values = [dec(member['score']) for member in members if member['score'] is not None]
        value = ds(median(values)) if values else None
        categories[category] = dict(score=value, label=waterlevel(value), members=members,
                                    included=len(values), expected=len(members))
    values = [dec(item['score']) for item in categories.values() if item['score'] is not None]
    overall = ds(median(values)) if values else None
    return dict(overall=dict(score=overall, label=waterlevel(overall), included=len(values), expected=3),
                categories=categories, method_version=METHOD_VERSION)
