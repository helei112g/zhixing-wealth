"""Deterministic synthetic supplier responses; never copied from a user's ledger."""
import calendar
from datetime import date,timedelta
from backend.research import lixinger, providers

CUSTOM_CODE = 'SYNIDX1'


def transport(endpoint, params):
    code=(params.get('stockCodes') or [params.get('stockCode')])[0]
    if endpoint=='cn/index':
        known = providers.catalog_identity(code)
        if not known and code != CUSTOM_CODE:
            return 200, dict(code=1, data=[])
        launch = known['launch'] if known else '2026-07-01'
        return 200,dict(code=1,data=[dict(stockCode=code,name='合成自定义指数' if not known else '合成指数',
            source=known['provider'] if known else 'lxri', areaCode='cn',market='a',currency='CNY',fsTableType='hybrid',
            launchDate=launch,series='thematic',rebalancingFrequency='quarterly')])
    if endpoint.endswith('/tracking-fund'):
        return 200,dict(code=1,data=[dict(stockCode='510001',name='合成跟踪基金',areaCode='cn',market='a',exchange='sh')])
    if endpoint.endswith('/constituents'):
        return 200,dict(code=1,data=[dict(stockCode=code,constituents=[dict(stockCode=c,stockName={'cmn_hans_cn':n},areaCode='cn',market='a') for c,n in [('600201','合成科技'),('000201','合成银行')]])])
    start=date.fromisoformat(params['startDate']);end=date.fromisoformat(params['endDate']);rows=[];cursor=start
    while cursor<=end:
        if cursor.weekday()<5:
            n=(cursor-date(2005,1,1)).days;price=100+n/100+((n%800)-400)/20
            row=dict(date=cursor.isoformat()+'T00:00:00+08:00')
            if endpoint.endswith('/candlestick'):
                row['close']=price*(1.5 if params['type']=='total_return' else 1);rows.append(row)
            elif endpoint.endswith('/volatility'):
                assert params['volatilityDays'] == lixinger.VOLATILITY_DAYS
                rows.append(dict(row, value=(150+n%125)/1000))
            elif endpoint.endswith('/fundamental'):
                row['stockCode']=code
                for k in params['metricsList']:
                    row[k]=(.02+(n%40)/10000) if k.startswith('dyr.') else (10+(n%800)/100) if '.' in k else price if k in ('cp','r_cp') else 1000000000+n*10000
                rows.append(row)
            elif endpoint.endswith('/constituent-weightings'):
                for c,n,w in [('600201','合成科技',.6),('000201','合成银行',.4)]:rows.append(dict(row,stockCode=c,stockName={'cmn_hans_cn':n},weighting=w))
            elif '/fs/' in endpoint:
                if cursor.month in (3,6,9,12) and cursor.day>=28:
                    if cursor.day!=calendar.monthrange(cursor.year,cursor.month)[1]:cursor+=timedelta(days=1);continue
                    row.update(stockCode=code,currency='CNY',reportDate=str(cursor+timedelta(days=1)),reportType='synthetic',q={})
                    for k in params['metricsList']:
                        _,table,field,expr=k.split('.');row['q'].setdefault(table,{}).setdefault(field,{})[expr]=.10 if table=='m' or field.endswith('_r') else .12 if expr.endswith('y2y') else 1e9+n*1000
                    rows.append(row)
            elif endpoint.endswith('/mutual-market'):
                if cursor.month in (3,6,9,12) and cursor.day==calendar.monthrange(cursor.year,cursor.month)[1]:rows.append(dict(row,shareholdingsMoney=1e8+n*1000,netBuyAmount=1000000,shareholdingsMoneyToMarketCap=.025))
            else:rows.append(dict(row,financingBalance=1e8+n*1000,financingNetPurchaseAmount=10000,financingPurchaseAmount=12000,financingRepaymentAmount=2000,financingBalanceToMarketCap=.014,
                                  securitiesBalance=1e7+n*100,securitiesSellAmount=200,securitiesRepaymentAmount=80,
                                  securitiesNetSellAmount=120,securitiesBalanceToMarketCap=.002))
        cursor+=timedelta(days=1)
    return 200,dict(code=1,data=rows[-params['limit']:] if params.get('limit') else rows)


def install():
    from backend.research.lixinger_access import LocalAccess
    original=LocalAccess.request
    def request(self,endpoint,params,**kwargs):
        import os
        from pathlib import Path
        expected = Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve()
        if self.directory.parent.resolve() != expected or not (expected / '.synthetic-fixture').is_file():
            raise RuntimeError('Synthetic adapter requires the exact synthetic development ledger')
        return original(self,endpoint,params,**dict(kwargs,transport=transport))
    LocalAccess.request=request
