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



def historical_memberships(profile,asof=ASOF):
    """Legacy frozen-source fixture still used by the isolated product showcase."""
    return dict(baskets=[dict(period=str(y)+'-12-31',status='official' if y>=2020 else 'unavailable',members=[dict(m,weight=None) for m in member_payload(profile)['rows']] if y>=2020 else [],reason='合成历史缺口' if y<2020 else None) for y in range(2015,2026)],effective_date=ASOF,evidence=[])


def browser_prices(profile,basis,asof=ASOF,start_date=None):
    value=price_payload(profile,basis,asof)
    if profile['code']=='000819':value['rows']=value['rows'][-1000:]
    if profile['code']=='H11136':value['source']='中证指数'
    return value


# A richer synthetic browser basket verifies top10 boundaries and fixed weight ordering.
def browser_members(profile,asof=ASOF):
    if profile['code']=='H11136':
        return dict(rows=[dict(code='00999.HK',name='合成境外公司甲',weight='60',market='HK',industry='',date='2026-09-30'),dict(code='SYN.O',name='合成境外公司乙',weight='40',market='US',industry='',date='2026-09-30')],effective_date='2026-09-30',source='synthetic',evidence=[])
    weights=[30,20,10,8,6,5,5,4,4,3,3,2]
    rows=[dict(code='600201' if n==0 else f'{200+n:06d}',name='合成科技' if n==0 else '合成银行' if n==1 else f'合成公司{n+1}',weight=str(w),market='SH' if n==0 else 'SZ',industry='银行' if n==1 else '信息技术',date='2026-09-30') for n,w in enumerate(weights)]
    if profile['code']=='000991':rows[-1].update(code='920201',name='合成北交所公司',market='BJ',industry='医药卫生')
    return dict(rows=rows,effective_date='2026-09-30',source='synthetic',evidence=[])

def browser_finance(members,asof=ASOF):
    value=finance_payload(members,asof)
    for r in value['rows']:
        for field in ('revenue','profit','operating_cf'):
            r[field]=str(int(r[field])*1000000)
            r[field+'_published_at']=str(int(r['period'][:4])+1)+'-04-30'
    return value

def industry_payload(members,period):
    return dict(rows=[dict(code=m['code'],date=period,industry='银行' if m['code']=='000201' else '信息技术') for m in members],evidence=[])

def install_providers():
    from tests.lixinger_fixtures import install
    install()
    providers.prices=browser_prices
    providers.constituents=browser_members
    providers.stock_valuations=stock_payload
    providers.financials=browser_finance
    providers.historical_industries=industry_payload
    providers.cutoff=lambda:ASOF
