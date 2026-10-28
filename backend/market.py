from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal
import certifi

from .common import DomainError, ZONE, day, dec, ds, now, today, wire

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36'
TLS_CONTEXT = ssl.create_default_context()
TLS_CONTEXT.load_verify_locations(cafile=certifi.where())


class ProviderError(DomainError):
    pass


def fetch(url, referer=None, timeout=12, attempts=2, request_headers=None):
    headers = dict(request_headers) if request_headers is not None else {'User-Agent': UA, 'Accept': 'application/json,text/plain,*/*'}
    if referer:
        headers['Referer'] = referer
    error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=TLS_CONTEXT) as response:
                data = response.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise ProviderError('数据响应超过8MB限制')
                return data.decode('utf-8-sig')
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            error = ('HTTP '+str(exc.code)) if isinstance(exc,urllib.error.HTTPError) else type(exc).__name__
            if isinstance(exc,urllib.error.HTTPError) and exc.code==429:break
            if attempt + 1 < attempts:
                time.sleep(.4 * (attempt + 1))
    raise ProviderError(f'数据源暂时不可用（{error}），已保留本地数据，请稍后重试同步')


def parse_json(text):
    return json.loads(text, parse_float=Decimal)


def quote_record(date_value, price, source, kind='raw', **extra):
    return {'date': day(date_value).isoformat(), 'price': ds(dec(price, '收盘价/净值', positive=True)),
            'kind': kind, 'source': source, 'manual': False, 'fetched_at': now(), **extra}


def eastmoney(instrument, start, end, adjusted=False):
    market = {'SH': '1', 'SZ': '0', 'HK': '116'}[instrument['market']]
    query = urllib.parse.urlencode({'secid': market + '.' + instrument['code'], 'klt': '101',
        'fqt': '1' if adjusted else '0', 'beg': start.replace('-', ''), 'end': end.replace('-', ''),
        'lmt': '1000000', 'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b'})
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + query
    obj = parse_json(fetch(url, 'https://quote.eastmoney.com/'))
    data = obj.get('data') or {}
    if str(data.get('code', '')).zfill(len(instrument['code'])) != instrument['code']:
        raise ProviderError('东方财富未返回匹配代码，请核对市场与代码')
    rows = []
    for line in data.get('klines') or []:
        fields = line.split(',')
        if len(fields) >= 3 and start <= fields[0] <= end:
            rows.append(quote_record(fields[0], fields[2], '东方财富', 'adjusted' if adjusted else 'raw'))
    if not rows:
        raise ProviderError('东方财富未返回所选区间日线')
    return rows, data.get('name')


def tencent(instrument, start, end):
    symbol = instrument['market'].lower() + instrument['code']
    rows = {}; current = day(start); stop = day(end)
    # The endpoint truncates at 320 records. Use <=300 calendar-day windows.
    while current <= stop:
        until = min(stop, current + timedelta(days=299))
        query = urllib.parse.urlencode({'param': f'{symbol},day,{current},{until},320,'})
        obj = parse_json(fetch('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + query,
                               'https://gu.qq.com/'))
        if obj.get('code') not in (None, 0):
            raise ProviderError('腾讯日线返回错误状态')
        data = (obj.get('data') or {}).get(symbol) or {}
        for row in data.get('day') or []:
            if len(row) >= 3 and start <= row[0] <= end:
                rows[row[0]] = quote_record(row[0], row[2], '腾讯财经（备用）')
        current = until + timedelta(days=1)
    if not rows:
        raise ProviderError('腾讯备用源未返回该标的日线')
    name = (data.get('qt') or {}).get(symbol, [None, None])[1]
    return list(rows.values()), name


def fund(instrument, start, end):
    text = fetch('https://fund.eastmoney.com/pingzhongdata/' + instrument['code'] + '.js',
                 'https://fund.eastmoney.com/')
    code = re.search(r'\bfS_code\s*=\s*["\'](\d+)["\']', text)
    if not code or code.group(1) != instrument['code']:
        raise ProviderError('基金源返回代码不匹配')
    match = re.search(r'\bData_netWorthTrend\s*=\s*(\[.*?\])\s*;', text, re.S)
    if not match:
        raise ProviderError('基金单位净值数据结构已变化')
    # Remote JavaScript is treated as text; it is never executed.
    raw = parse_json(match.group(1)); rows = []
    for row in raw:
        if row.get('y') is None:
            continue
        d = datetime.fromtimestamp(int(row['x']) / 1000, ZONE).date().isoformat()
        if start <= d <= end and dec(row['y']) > 0:
            rows.append(quote_record(d, row['y'], '天天基金·单位净值',
                                     dividend_hint=str(row.get('unitMoney') or '')[:300]))
    if not rows:
        raise ProviderError('基金所选区间无单位净值，可能尚未公布')
    name = re.search(r'\bfS_name\s*=\s*["\']([^"\']+)["\']', text)
    return rows, name.group(1) if name else None


def quotes(instrument, start, end, include_adjusted=True):
    start, end = day(start).isoformat(), min(day(end), today()).isoformat()
    if instrument.get('builtin'):
        return benchmark_quotes(instrument,start,end)
    if instrument['market']=='US':
        from . import yahoo
        return yahoo.quotes(instrument,start,end)
    if instrument['market'] == 'CUSTOM':
        raise ProviderError('该自定义或非上市标的暂无适用的价格同步数据源')
    if instrument['market'] == 'FUND':
        rows, name = fund(instrument, start, end)
        return {'rows': rows, 'name': name, 'source': rows[0]['source'], 'notes': []}
    notes = []
    try:
        rows, name = eastmoney(instrument, start, end)
    except (ProviderError, ValueError, KeyError) as exc:
        rows, name = tencent(instrument, start, end)
        notes.append('主源未成功，已使用腾讯备用；来源单独保留')
    if include_adjusted:
        try:
            adjusted_rows, _ = eastmoney(instrument, start, end, True)
            rows += adjusted_rows
        except (ProviderError, ValueError, KeyError):
            notes.append('复权参考序列暂不可得，复盘保留价格口径标识')
    return {'rows': rows, 'name': name, 'source': rows[0]['source'], 'notes': notes}


def benchmark_quotes(instrument,start,end):
    code=instrument['code'];rows=[];notes=[]
    if instrument['market']=='CSI' and code in ('H00300','H00985'):
        # CSI can synthesize its query-start row using the NEXT close, even on a holiday.
        # Ask earlier, unconditionally discard that boundary, then apply the true requested range.
        padded=(day(start)-timedelta(days=31)).strftime('%Y%m%d')
        query=urllib.parse.urlencode({'indexCode':code,'startDate':padded,'endDate':end.replace('-','')})
        obj=parse_json(fetch('https://www.csindex.com.cn/csindex-home/perf/index-perf?'+query,'https://www.csindex.com.cn/'))
        if str(obj.get('code'))!='200' or not isinstance(obj.get('data'),list):raise ProviderError('中证全收益日线响应格式异常')
        raw=sorted(obj['data'],key=lambda r:r.get('tradeDate',''))[1:]
        for row in raw:
            if row.get('indexCode')!=code:raise ProviderError('中证返回的全收益代码不匹配')
            d=datetime.strptime(str(row['tradeDate']),'%Y%m%d').date().isoformat()
            if start<=d<=end:
                rows.append(quote_record(d,row['close'],'中证指数·官方全收益日线','total_return',index_code=code))
        notes.append('请求起点前推31天并舍弃接口首条边界记录，避免节假日伪日期或前视值')
    elif instrument['market']=='CNI' and code=='399606':
        query=urllib.parse.urlencode({'indexCode':code,'startDate':start,'endDate':end})
        obj=parse_json(fetch('https://hq.cnindex.com.cn/market/market/getIndexDailyData?'+query,'https://www.cnindex.com.cn/'))
        data=obj.get('data') or {}
        if str(obj.get('code'))!='200' or data.get('indexCode')!=code:raise ProviderError('国证返回的创业板全收益代码不匹配')
        columns=data.get('item') or []
        if 'timestamp' not in columns or 'close' not in columns:raise ProviderError('国证全收益日线字段已变化')
        for row in data.get('data') or []:
            d=datetime.fromtimestamp(int(row[columns.index('timestamp')])/1000,ZONE).date().isoformat()
            if start<=d<=end:rows.append(quote_record(d,row[columns.index('close')],'国证指数·官方全收益日线','total_return',index_code=code))
    else:raise ProviderError('内置全收益指数身份不匹配')
    if not rows:raise ProviderError('官方源未返回该区间全收益日线；保留旧缓存，不使用价格指数替代')
    return {'rows':rows,'name':instrument['name'],'source':rows[0]['source'],'notes':notes}


def frankfurter(c, start=None, end=None):
    query = {'base': c, 'quotes': 'CNY'}
    if start:
        query['from'] = day(start).isoformat()
        query['to'] = min(day(end or today()), today()).isoformat()
    obj = parse_json(fetch('https://api.frankfurter.dev/v2/rates?' + urllib.parse.urlencode(query)))
    if not isinstance(obj, list):
        raise ProviderError('汇率源结构不符合v2格式')
    rows = []
    for r in obj:
        if r.get('base', '').upper() != c or r.get('quote', '').upper() != 'CNY':
            continue
        d = day(r['date'])
        if d > today(): continue
        rows.append({'currency': c, 'date': d.isoformat(), 'rate': ds(dec(r['rate'], '汇率', positive=True)),
                     'source': 'Frankfurter日频参考',
                     'manual': False, 'fetched_at': now()})
    if not rows: raise ProviderError('汇率源未返回指定货币对')
    return rows


def ecb(c, start=None, end=None):
    filename = 'eurofxref-hist.xml' if start and day(start) < today() - timedelta(days=85) else 'eurofxref-hist-90d.xml'
    xml = fetch('https://www.ecb.europa.eu/stats/eurofxref/' + filename)
    root = ET.fromstring(xml); rows = []
    for cube in root.iter():
        if 'time' not in cube.attrib: continue
        d = cube.attrib['time']
        if start and d < start or end and d > end or d > today().isoformat(): continue
        rates = {r.attrib['currency']: dec(r.attrib['rate']) for r in cube if 'currency' in r.attrib}
        if c in rates and 'CNY' in rates:
            rows.append({'currency': c, 'date': d, 'rate': ds(rates['CNY'] / rates[c]),
                         'source': 'ECB交叉参考（备用）', 'manual': False, 'fetched_at': now()})
    if not rows: raise ProviderError('ECB备用源未返回该区间汇率')
    return rows


def exchange_rates(c, start=None, end=None):
    if c not in ('HKD', 'USD'): raise DomainError('仅拉取HKD/USD兑CNY')
    try:
        return frankfurter(c, start, end)
    except (ProviderError, ValueError, KeyError):
        return ecb(c, start, end)
