"""Entirely synthetic research payloads; never copied from public or personal datasets."""
import calendar
from datetime import date,timedelta
from backend.research import providers

ASOF='2026-10-30'


def price_payload(profile,basis,asof=ASOF):
    cursor=date(2015,1,1);end=date.fromisoformat(min(asof,ASOF));rows=[]
    highs={'2017-06-01':'300','2022-06-01':'250','2024-06-03':'200','2025-06-02':'200'}
    if basis=='total_return':highs={'2018-06-01':'500','2022-07-01':'450','2024-07-01':'400'}
    while cursor<=end:
        if cursor.weekday()<5:rows.append(dict(date=str(cursor),close=highs.get(str(cursor),'120' if str(cursor)==ASOF else '100')))
        cursor+=timedelta(days=1)
    return dict(rows=rows,basis=basis,series_code=profile['return_code'] if basis=='total_return' else profile['code'],effective_date=rows[-1]['date'],source='synthetic',evidence=[])


def valuation_payload(profile,asof=ASOF):
    series={}
    for metric in ('pe','pb'):
        methods={}
        for method,field in [('overall','addTtmPe' if metric=='pe' else 'addPb'),('median','middleTtmPe' if metric=='pe' else 'middlePb'),('arithmetic','ttmPe' if metric=='pe' else 'pb')]:
            rows=[]
            for year in range(2015,2027):
                for month in range(1,13):
                    d=date(year,month,calendar.monthrange(year,month)[1])
                    while d.weekday()>4:d-=timedelta(days=1)
                    if str(d)<=asof:rows.append(dict(date=str(d),value=str(10+(month%5)) if metric=='pe' else str(1+(month%5)/10)))
            methods[method]=dict(rows=rows,source='synthetic',field=field,frequency='monthly')
        series[metric]=methods
    return dict(series=series,effective_date=ASOF,evidence=[])


def member_payload(profile,asof=ASOF):
    return dict(rows=[dict(code='600201',name='合成科技',weight='60',market='SH',industry='信息技术',date='2026-09-30'),dict(code='000201',name='合成银行',weight='40',market='SZ',industry='银行',date='2026-09-30')],effective_date='2026-09-30',evidence=[],source='synthetic')


def stock_payload(members,asof=ASOF):
    return dict(rows=[dict(code=m['code'],date=ASOF,pe='-10' if n else '20',pb='1.5',pe_status='loss' if n else 'available',pb_status='available',source='synthetic',pe_field='f115',pb_field='f23') for n,m in enumerate(members)],effective_date=ASOF,evidence=[],limitation='合成测试数据')


def finance_payload(members,asof=ASOF):
    rows=[]
    for m in members:
        for y in range(2015,2026):rows.append(dict(code=m['code'],period=str(y)+'-12-31',revenue=str(100*y),profit=str(10*y),operating_cf=str(8*y)))
    return dict(rows=rows,periods=[str(y)+'-12-31' for y in range(2016,2026)],effective_date='2026-04-30',evidence=[],history_status='unavailable',history_reason='合成样例未提供核验历史成分')


def install_providers():
    providers.prices=price_payload;providers.valuations=valuation_payload;providers.constituents=member_payload;providers.stock_valuations=stock_payload;providers.financials=finance_payload;providers.cutoff=lambda:ASOF


def historical_memberships(profile,asof=ASOF):
    return dict(baskets=[dict(period=str(y)+'-12-31',status='official' if y>=2020 else 'unavailable',members=[dict(m,weight=None) for m in member_payload(profile)['rows']] if y>=2020 else [],reason='合成历史缺口' if y<2020 else None) for y in range(2015,2026)],effective_date=ASOF,evidence=[])


_base_price=price_payload
_attempts={}

def browser_prices(profile,basis,asof=ASOF):
    if profile['code']=='000819' and basis=='total_return' and not _attempts.get('short_return'):
        _attempts['short_return']=True
        raise TimeoutError('合成全收益源暂时不可用')
    value=_base_price(profile,basis,asof)
    if profile['code']=='000819':value['rows']=value['rows'][-1000:]
    return value


_original_install=install_providers

def install_providers():
    _original_install()
    from backend.research import history
    history.memberships=historical_memberships
    providers.prices=browser_prices
