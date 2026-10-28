"""Pure, portfolio-scoped transaction filtering shared by UI and API clients."""
from .common import DomainError, canonical_region


def market_key(row):
    if row['event']['kind'] in ('DEPOSIT', 'OPENING_CASH', 'WITHDRAW', 'FX'):
        return 'CASH'
    market = (row.get('instrument') or {}).get('market')
    return 'CN' if market in ('SH', 'SZ') else market if market in ('HK', 'US', 'FUND') else 'OTHER'


def paginate(rows, allocations, *, page=1, page_size=20, market='ALL', tag='', asset_class='', region=''):
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DomainError('page 必须是正整数')
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise DomainError('page_size 必须为1至100')
    if market not in ('ALL', 'CN', 'HK', 'US', 'FUND', 'CASH', 'OTHER'):
        raise DomainError('市场筛选条件无效')
    region = canonical_region(region) if region else ''
    current = {a['instrument_id']: a for a in allocations}
    def labels(row):
        # Current portfolio allocation is the filter basis; historical event tags remain untouched.
        return current.get(row['event'].get('instrument_id'), {})
    def matches(row):
        a = labels(row)
        return ((market == 'ALL' or market_key(row) == market)
                and (not tag or tag in a.get('tags', []))
                and (not asset_class or a.get('asset_class', '未分类') == asset_class)
                and (not region or canonical_region(a.get('region')) == region))
    filtered = [r for r in rows if matches(r)]
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    return {'items': filtered[start:start + page_size], 'total': len(filtered), 'unfiltered_total': len(rows),
            'page': page, 'page_size': page_size, 'page_count': pages,
            'filters': {'market': market, 'tag': tag, 'asset_class': asset_class, 'region': region},
            'facets': {'tags': sorted({t for r in rows for t in labels(r).get('tags', [])}),
                       'asset_classes': sorted({labels(r).get('asset_class', '未分类') for r in rows}),
                       'regions': sorted({canonical_region(labels(r).get('region')) for r in rows})}}
