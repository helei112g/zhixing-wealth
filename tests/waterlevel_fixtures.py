"""Synthetic market histories only. No public-data captures or real ledgers."""
from calendar import monthrange
from datetime import date, timedelta
from math import sin

from backend.common import json_dumps
from backend.research import providers
from backend.waterlevel.catalog import FULL_MARKET_CODE, FULL_MARKET_FIELDS, GROUPS, LIXINGER_FIELDS, SERIES

ASOF = '2026-10-30'


def history(value='10', monthly=False, start=date(2014, 1, 1)):
    result = []
    stamp = start
    while str(stamp) <= ASOF:
        if monthly and stamp.day == 1 or not monthly and stamp.weekday() < 5:
            result.append(dict(date=str(stamp), value=value, published_at=None))
        stamp += timedelta(days=1)
    return result


def payload(group, asof=ASOF, previous=None, cancelled=lambda: False, *, access=None, full=False, series=None):
    target = series
    values = {'cn_2y':'1', 'cn_10y':'2', 'aaa_3y':'3', 'gov_3y':'2', 'fr007':'2.2', 'fdr007':'1.8',
              'm1_yoy':'4', 'm2_yoy':'7', 'pmi':'51', 'nonmanufacturing_pmi':'52', 'sf_stock_yoy':'8',
              'margin_balance':'200000000000', 'sh_margin_balance':'110000000000',
              'sz_margin_balance':'90000000000', 'a_share_turnover':'800000000000'}
    series = {key: history(values.get(key, '1'), frequency == 'monthly')
              for key, (owner, _, _, frequency) in SERIES.items() if owner == group}
    if group == 'full_market':
        series = {key: [] for key in FULL_MARKET_FIELDS}
    if group in ('full_market', 'composites'):
        for key in series:
            rows = history(start=date(2005,1,1))
            if group == 'full_market':
                field, method = FULL_MARKET_FIELDS[key].split('.')
                scale = (25 if field=='pe_ttm' else 2.5) * {'mcw':1, 'ew':1.2, 'ewpvo':1.1, 'median':1.4}[method]
            else:
                scale = {'sh_composite':1800, 'sz_composite':900}[key]
            for row in rows:
                offset = (date.fromisoformat(row['date'])-date(2005,1,1)).days / 365
                row['value'] = f'{scale * (1 + .2*sin(offset) + offset*.01):.6f}'
                if group == 'full_market':
                    row['field'] = FULL_MARKET_FIELDS[key]
            series[key] = rows
    for rows in series.values():
        for row in rows:
            if group == 'social_financing':
                row['source_url'] = 'https://www.pbc.gov.cn/synthetic-test.html'
    if group == 'market_turnover':
        for row in series['a_share_turnover']:
            row['field'] = 'ta'
    if target:
        series = {**(previous or {}).get('series', {}), target: series[target]}
    value = dict(group=group, publisher=GROUPS[group]['publisher'], series=series, checked_at=ASOF+'T19:00:00+08:00',
                 requested_asof=asof, published_at=None, evidence=[dict(url='https://www.pbc.gov.cn/synthetic-test.html',
                 fetched_at=ASOF+'T19:00:00+08:00', sha256='a'*64)])
    if group in LIXINGER_FIELDS:
        value['evidence'][0].update(url='https://www.lixinger.com/api/open-api/html-doc/cn/index/fundamental',stock_code=FULL_MARKET_CODE)
    if group == 'margin_balances':
        value['evidence'][0]['url'] = 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_RZRQ_LSDB'
    if group == 'calendar':
        value['calendar'] = dict(year=2026, events=[dict(group='pmi', date='2026-10-31', period='2026-10'),
                                                  dict(group='prices', date='2026-11-09', period='2026-10')])
    return value


def seed_indices(research, *, valuation_only=False):
    for code in ('000300', '000905', '000852', '930050', '000510'):
        profile = providers.identity(code)
        iid = profile['provider'] + ':' + code
        if valuation_only:
            profile['subscription'] = dict(enabled=True, modules=['valuations'], history_start=None)
        with research.transaction(bump=True) as db:
            db.execute('INSERT INTO research_indices VALUES (?,?)', (iid, json_dumps(dict(profile, id=iid))))
        series = {}
        for metric in ('pe', 'pb', 'dividend_yield'):
            series[metric] = {}
            for method, value in (('mcw', '10'), ('ew', '-4'), ('ewpvo', '15'), ('median', '20')):
                if metric == 'dividend_yield':
                    value = '.02'
                rows = [row for row in history(value) if row['date'] >= profile['launch']]
                series[metric][method] = dict(rows=rows, source='理杏仁', field={'pe':'pe_ttm','pb':'pb','dividend_yield':'dyr'}[metric]+'.'+method,
                                               frequency='daily')
        research.publish(iid, 'index_info', dict(info={'stockCode': code}, evidence=[]))
        research.publish(iid, 'valuations', dict(provider_id='lixinger', series=series, effective_date=ASOF,
                                                source='理杏仁', evidence=[]))


def install_providers():
    import os
    from pathlib import Path
    from backend import app as app_module
    from backend.common import DomainError
    from backend.waterlevel import sources
    calls = {}

    def browser_payload(group, *args, **kwargs):
        calls[group] = calls.get(group, 0) + 1
        # The second credit request fails immediately without publishing data.
        # Browser regression verifies failure polling and exact-resource retry.
        if group == 'credit' and calls[group] == 2:
            raise DomainError('合成信用来源失败，用于验收保留旧值与重试')
        return payload(group, *args, **kwargs)

    sources.collect = browser_payload
    original = app_module.create_app

    def synthetic_app(directory, **kwargs):
        expected = Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve()
        if (kwargs.get('environment') != 'dev' or Path(directory).resolve() != expected
                or not (expected / '.synthetic-fixture').is_file()):
            raise RuntimeError('Synthetic waterlevel fixtures require the exact synthetic development ledger')
        application = original(directory, **kwargs)
        research = application.state.research
        seed_indices(research, valuation_only=True)

        def synthetic_valuation(iid, module, end, *, full=False):
            from copy import deepcopy
            if module != 'valuations':
                raise RuntimeError('Unexpected index module in synthetic market browser')
            value = deepcopy(research.module(iid, module))
            if iid == 'csi:000510':
                for series in value['series']['pe'].values():
                    series['rows'][-1]['value'] = '31' if full else '29'
            sid = research.publish(iid, module, value)
            return dict(value, snapshot_id=sid)

        research.collection.collect = synthetic_valuation
        return application

    app_module.create_app = synthetic_app


def drop_tables(db):
    for table in ('market_heads', 'market_snapshots', 'market_reports', 'market_settings'):
        db.execute('DROP TABLE IF EXISTS ' + table)
