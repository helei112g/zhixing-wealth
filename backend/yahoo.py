"""Public listed-equity fallback. Yahoo daily closes/dividends are split-adjusted.

Always fetch splits through the current exchange date, including splits after
requested historical end, and reverse ONLY those split factors for nominal raw
prices. Adjusted closes stay reference series, never an accounting/TR substitute.
"""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import time
import urllib.parse
from .common import dec,ds,day,today
from . import market
_CACHE={}
_RETRY_AFTER=0

def symbol_for(inst):
    if inst['market']=='HK':return str(int(inst['code'])).zfill(4)+'.HK'
    if inst['market']=='US':return inst['code'].replace('.','-').upper()
    raise market.ProviderError('该市场不使用港美股行情源')

def chart(inst,start='1970-01-01'):
    global _RETRY_AFTER
    symbol=symbol_for(inst);key=(symbol,inst['currency'],str(today()));cached=_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<600:return cached[1]
    if time.monotonic()<_RETRY_AFTER:raise market.ProviderError('Yahoo暂时限流，10分钟内不重复请求；已保留缓存')
    period1=0 # one cached full history also supplies all subsequent split events
    period2=int(datetime.combine(today()+timedelta(days=2),datetime.min.time(),timezone.utc).timestamp())
    params={'period1':max(0,period1),'period2':period2,'interval':'1d','events':'div,splits','includeAdjustedClose':'true'}
    error=None
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        try:
            obj=market.parse_json(market.fetch('https://'+host+'/v8/finance/chart/'+urllib.parse.quote(symbol,safe='')+'?'+urllib.parse.urlencode(params),request_headers={'User-Agent':'Mozilla/5.0'}))
            data=(obj.get('chart') or {}).get('result')
            if not isinstance(data,list) or len(data)!=1:raise market.ProviderError('未返回港美股行情，保留旧缓存')
            result=data[0];meta=result.get('meta') or {}
            if meta.get('symbol','').upper()!=symbol.upper() or meta.get('currency')!=inst['currency']:raise market.ProviderError('行情代码或币种与标的档案不匹配')
            if meta.get('instrumentType') not in ('EQUITY','ETF'):raise market.ProviderError('港美股仅支持场内股票和ETF')
            ZoneInfo(meta['exchangeTimezoneName'])
            _CACHE[key]=(time.monotonic(),result);return result
        except (market.ProviderError,KeyError,ValueError) as exc:
            error=exc
            if 'HTTP 429' in str(exc):
                _RETRY_AFTER=time.monotonic()+600
                raise market.ProviderError('Yahoo请求受限（HTTP 429），保留缓存；请稍后重试行情同步；分派可按实际交割单核对') from exc
    raise market.ProviderError(str(error))

def local_date(stamp,data):return datetime.fromtimestamp(int(stamp),ZoneInfo(data['meta']['exchangeTimezoneName'])).date().isoformat()

def split_events(data):
    current=datetime.now(ZoneInfo(data['meta']['exchangeTimezoneName'])).date().isoformat();rows=[]
    for r in (data.get('events') or {}).get('splits',{}).values():
        d=local_date(r['date'],data)
        if d>current:continue
        numerator=dec(r['numerator'],positive=True);denominator=dec(r['denominator'],positive=True)
        rows.append({'date':d,'factor':ds(numerator/denominator),'numerator':ds(numerator),'denominator':ds(denominator)})
    return sorted(rows,key=lambda r:r['date'])

def factor_after(date,splits):
    result=Decimal(1)
    for s in splits:
        if s['date']>date:result*=dec(s['factor'])
    return result

def parse_quotes(data,inst,start,end,adjusted_start=None):
    splits=split_events(data);times=data.get('timestamp') or [];ind=data.get('indicators') or {};quotes=(ind.get('quote') or [{}])[0].get('close') or []
    if len(times)!=len(quotes):raise market.ProviderError('港美股日线日期与价格长度不一致')
    adjusted=(ind.get('adjclose') or [{}])[0].get('adjclose') or []
    current=datetime.now(ZoneInfo(data['meta']['exchangeTimezoneName'])).date().isoformat();rows=[]
    for n,ts in enumerate(times):
        d=local_date(ts,data);p=quotes[n]
        if not (adjusted_start or start)<=d<=end or d>=current or p is None:continue
        factor=factor_after(d,splits)
        if d>=start:rows.append(market.quote_record(d,dec(p,positive=True)*factor,'Yahoo日线·还原拆分前名义收盘','raw',provider_close=ds(p),split_restore_factor=ds(factor),exchange_timezone=data['meta']['exchangeTimezoneName']))
        if n<len(adjusted) and adjusted[n] is not None:
            rows.append(market.quote_record(d,adjusted[n],'Yahoo复权参考（含分派调整）','adjusted',exchange_timezone=data['meta']['exchangeTimezoneName']))
    if not rows:raise market.ProviderError('该区间暂无已结束交易日的行情；保留已有数据')
    return {'rows':rows,'name':data['meta'].get('longName') or data['meta'].get('shortName'),'source':'Yahoo Finance日线',
        'notes':['按交易所时区归日，T+1不写当前交易日未结束的日线；使用起点至今全部拆分恢复名义价格，复权序列仅供参考'],
        'actions':[dict(r,kind='split_notice',source='Yahoo拆分事件',source_url='https://finance.yahoo.com/quote/'+symbol_for(inst)+'/history/') for r in splits if (adjusted_start or start)<=r['date']<=end]}

def quotes(inst,start,end,adjusted_start=None):return parse_quotes(chart(inst,start),inst,start,end,adjusted_start)

def dividends(inst):
    data=chart(inst);splits=split_events(data);rows=[];symbol=symbol_for(inst)
    for r in (data.get('events') or {}).get('dividends',{}).values():
        ex=local_date(r['date'],data);factor=factor_after(ex,splits);amount=dec(r['amount'],positive=True)*factor
        rows.append({'code':inst['code'],'source_ref':f'cash:{inst["market"]}:{inst["code"]}:{ex}:{inst["currency"]}',
            'record_date':None,'ex_date':ex,'pay_date':None,'per_share':ds(amount),'currency':inst['currency'],
            'source':'Yahoo现金分派参考','source_url':'https://finance.yahoo.com/quote/'+symbol+'/history/?filter=div',
            'original_rate':ds(r['amount']),'original_unit':'每股（源已按拆分调整）','split_restore_factor':ds(factor),'status':'published',
            'coverage_note':'源金额存在舍入，已逆向恢复后续拆分因子；不含派息日，请按公告补充并核对精确金额。仅代表源返回的现金分派记录。'})
    return rows
