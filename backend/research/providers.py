"""Bounded public-data adapters. No portfolio information leaves the device."""
import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import urlencode
from .. import market
from ..common import ZONE, day, dec, ds, now, today

CSI = 'https://www.csindex.com.cn'
CNI = 'https://www.cnindex.com.cn'
# Explicitly paired official identities; unknown codes are never prefix-matched.
CATALOG = [
    dict(code='000300', return_code='H00300', name='沪深300', provider='csi', kind='宽基', launch='2005-04-08'),
    dict(code='000905', return_code='H00905', name='中证500', provider='csi', kind='宽基', launch='2007-01-15'),
    dict(code='000985', return_code='H00985', name='中证全指', provider='csi', kind='宽基', launch='2011-08-02'),
    dict(code='399006', return_code='399606', name='创业板指', provider='cni', kind='宽基', launch='2010-06-01',return_launch='2010-06-01',identity_source='https://www.cnindex.com.cn/zh_information/notices_news/2010/201206/P020191213351661589949.pdf'),
    dict(code='000922', return_code='H00922', name='中证红利', provider='csi', kind='策略', launch='2008-05-26'),
    dict(code='000819', return_code='H00819', name='有色金属', provider='csi', kind='行业', launch='2009-07-03'),
]
SOURCE_LOCK = threading.Lock()


def identity(code):
    code = str(code).strip().upper()
    found = next((p for p in CATALOG if code in (p['code'], p['return_code'])), None)
    if not found: raise market.ProviderError('未找到已核验的官方代码对；不按前缀猜测，请等待身份适配扩展')
    p = dict(found, currency='CNY', input_basis='total_return' if code == found['return_code'] else 'price')
    p['method_url'] = (CNI + '/docs/gz_399006.pdf' if p['provider'] == 'cni' else
        'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/' + p['code'] + 'factsheet.pdf')
    p['return_launch_status'] = 'verified' if p.get('return_launch') else 'not_independently_verified'
    return p


def cutoff():
    t = datetime.now(ZONE)
    d = t.date() - timedelta(days=1) if t.hour < 16 else t.date()
    while d.weekday() > 4: d -= timedelta(days=1)
    return d.isoformat()


def request(url):
    raw = market.fetch(url, timeout=20, attempts=2)
    return market.parse_json(raw), {'url': url, 'fetched_at': now(), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def prices(profile, basis, asof):
    code = profile['return_code'] if basis == 'total_return' else profile['code']
    if profile['provider'] == 'cni':
        obj, evidence = request('https://hq.cnindex.com.cn/market/market/getIndexDailyData?' + urlencode(
            dict(indexCode=code, startDate='2000-01-01', endDate=asof)))
        data = obj.get('data') or {}
        if data.get('indexCode') != code: raise market.ProviderError('国证日线返回身份不匹配')
        columns = data['item']; result = [dict(date=datetime.fromtimestamp(int(r[columns.index('timestamp')]) / 1000, ZONE).date().isoformat(),
             close=ds(dec(r[columns.index('close')], positive=True))) for r in data['data']]
    else:
        obj, evidence = request(CSI + '/csindex-home/perf/index-perf?' + urlencode(
            dict(indexCode=code, startDate='19990101', endDate=asof.replace('-', ''))))
        raw = sorted(obj.get('data') or [], key=lambda r: str(r['tradeDate']))
        if any(r.get('indexCode') != code for r in raw): raise market.ProviderError('中证日线返回身份不匹配')
        # Preserve the verified exclusion of the API's artificial query-start row.
        result = [dict(date=day(str(r['tradeDate'])).isoformat(), close=ds(dec(r['close'], positive=True))) for r in raw[1:]]
    start=(profile.get('return_launch') or profile['launch']) if basis=='total_return' else profile['launch']
    result = sorted([r for r in result if start <= r['date'] <= asof],key=lambda r:r['date'])
    if not result: raise market.ProviderError('官方源未返回有效完整日线')
    return dict(rows=result, source='国证指数' if profile['provider'] == 'cni' else '中证指数', evidence=[evidence],
                series_code=code, basis=basis, effective_date=result[-1]['date'], theoretical_history_note='官方全收益回算序列；独立发布日期未核验，不代表ETF实盘' if basis=='total_return' and not profile.get('return_launch') else None)


def valuations(profile, asof):
    out = {}; evidence = []
    with SOURCE_LOCK:
        for metric, fields in {'pe': {'overall': 'addTtmPe', 'median': 'middleTtmPe', 'arithmetic': 'ttmPe'},
                               'pb': {'overall': 'addPb', 'median': 'middlePb', 'arithmetic': 'pb'}}.items():
            token=hashlib.md5(today().isoformat().encode(),usedforsecurity=False).hexdigest()
            options=legu_session('https://legulegu.com/stockdata/sz50-'+('ttm-lyr' if metric=='pe' else 'pb'))
            # Cookies and token stay in memory; sanitized provenance contains no secrets.
            import requests
            code = profile['code'] + ('.SZ' if profile['provider'] == 'cni' else '.SH')
            url = 'https://legulegu.com/api/stockdata/index-basic-' + metric
            try:
                r = requests.get(url, params={'token': token, 'indexCode': code}, timeout=20, **options)
                r.raise_for_status(); raw = r.json().get('data') or []
            except (requests.RequestException,ValueError):
                raise market.ProviderError('乐咕估值请求失败或签名失效；旧数据保留，请稍后重试') from None
            if not raw: raise market.ProviderError('估值源未覆盖此指数，不使用相似指数替代')
            methods = {}
            for method, field in fields.items():
                rows = []
                for row in raw:
                    d = str(row.get('date', ''))[:10]
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) and d <= asof and row.get(field) is not None:
                        value = dec(row[field])
                        rows.append(dict(date=d, value=ds(value)))
                if rows: methods[method] = dict(rows=rows, field=field, source='乐咕乐股', frequency='monthly')
            if 'overall' not in methods: raise market.ProviderError('缺少约定整体法字段')
            out[metric] = methods
            evidence.append(dict(url=url, parameters={'indexCode': code}, fetched_at=now(), sha256=hashlib.sha256(r.content).hexdigest()))
    return dict(series=out, evidence=evidence, effective_date=min(v['overall']['rows'][-1]['date'] for v in out.values()),
                methodology_note='供应商整体法亏损剔除与加权细节未独立复原；历史为当前可得版本')


def excel_rows(raw):
    if len(raw)>32*1024*1024:raise market.ProviderError('官方成分文件超过32 MiB')
    if raw.startswith(b'\xd0\xcf\x11\xe0'):
        import xlrd
        book=xlrd.open_workbook(file_contents=raw)
        sheet=book.sheet_by_index(0)
        return [sheet.row_values(n) for n in range(sheet.nrows)]
    import openpyxl
    book=openpyxl.load_workbook(BytesIO(raw),read_only=True,data_only=True)
    try:
        sheet=book.worksheets[0];sheet.reset_dimensions()
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    finally:book.close()


def cell_code(value):
    if isinstance(value,(float,int)):value=str(int(value))
    return str(value).strip().zfill(6)


def cell_date(value):
    if hasattr(value,'date'):value=value.date()
    return day(str(value)[:10]).isoformat()


def legu_session(url):
    import requests
    from html.parser import HTMLParser
    class CSRF(HTMLParser):
        token=None
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='meta' and attrs.get('name')=='_csrf':self.token=attrs.get('content')
    headers={'User-Agent':'Mozilla/5.0','Referer':url}
    try:
        r=requests.get(url,headers=headers,timeout=20);r.raise_for_status()
    except requests.RequestException:raise market.ProviderError('乐咕页面无法访问；旧估值保留') from None
    parser=CSRF();parser.feed(r.text)
    if not parser.token:raise market.ProviderError('乐咕页面缺少请求令牌，请稍后重试')
    return dict(cookies=r.cookies,headers=dict(headers,**{'X-CSRF-Token':parser.token}))


def constituents(profile, asof):
    import requests
    if profile['provider']=='csi':
        url='https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/'+profile['code']+'closeweight.xls'
        r=requests.get(url,timeout=20);r.raise_for_status();data=excel_rows(r.content)[1:]
        if any(cell_code(row[1])!=profile['code'] for row in data):raise market.ProviderError('成分指数身份不匹配')
        rows=[dict(date=cell_date(row[0]),code=cell_code(row[4]),name=str(row[5]),weight=ds(dec(row[9])),market='SH' if cell_code(row[4]).startswith('6') else 'SZ',industry='') for row in data]
    else:
        url=CNI+'/sample-detail/download-history?'+urlencode({'indexcode':profile['code']})
        r=requests.get(url,timeout=20);r.raise_for_status();data=excel_rows(r.content)[1:]
        rows=[dict(date=cell_date(row[0]),code=cell_code(row[1]),name=str(row[2]),weight=ds(dec(row[5])),market='SZ',industry=str(row[3]) if row[3] not in (None,'nan','-','') else '') for row in data]
    if not rows or len({r['date'] for r in rows})!=1 or rows[0]['date']>asof:raise market.ProviderError('权重快照日期无效或晚于截止日')
    if len({r['code'] for r in rows})!=len(rows) or abs(sum(dec(r['weight']) for r in rows)-100)>dec('.5'):raise market.ProviderError('成分重复或权重合计异常')
    return dict(rows=rows,effective_date=rows[0]['date'],source='官方月末权重',evidence=[dict(url=url,fetched_at=now(),sha256=hashlib.sha256(r.content).hexdigest())])


def stock_valuations(members, asof):
    rows = []; evidence = []
    for pos in range(0, len(members), 50):
        group = members[pos:pos+50]
        codes = {r['code']: r for r in group}
        url = 'https://push2.eastmoney.com/api/qt/ulist.np/get?' + urlencode(dict(fltt='2', fields='f12,f13,f14,f115,f23,f124,f100',
               secids=','.join(('1.' if r['market'] == 'SH' else '0.') + r['code'] for r in group)))
        obj, src = request(url); evidence.append(src)
        for r in (obj.get('data') or {}).get('diff') or []:
            if r['f12'] not in codes: raise market.ProviderError('成分估值返回非请求代码')
            stamp = r.get('f124'); d = datetime.fromtimestamp(int(stamp), ZONE).date().isoformat() if stamp else None
            if not d or d > asof: continue  # Intraday/future snapshots are not relabelled as yesterday.
            def optional(v):
                return None if v in (None, '-', '') else ds(dec(v))
            pe, pb = optional(r.get('f115')), optional(r.get('f23'))
            rows.append(dict(code=r['f12'], date=d, pe=pe, pb=pb, pe_status='loss' if pe and dec(pe)<0 else 'zero_or_missing' if pe is not None and dec(pe)==0 else 'available' if pe is not None else 'missing_or_zero_base',
                             pb_status='nonpositive_equity' if pb and dec(pb)<0 else 'zero_or_missing' if pb is not None and dec(pb)==0 else 'available' if pb is not None else 'missing', source='东方财富', pe_field='f115', pb_field='f23',
                             industry=r.get('f100') if isinstance(r.get('f100'),str) and r['f100']!='-' else None, methodology_note='供应商TTM字段；分母与亏损处理未逐家公司独立复原'))
    if not rows: raise market.ProviderError('未取得截止日内的成分估值快照；盘中数据不充当已完成收盘')
    return dict(rows=rows, evidence=evidence, effective_date=max((r['date'] for r in rows), default=None),
                limitation='PE TTM采用供应商f115，PB采用f23；分母未逐家公司独立复原。空值不填零。')


def financials(members, asof):
    # Official availability is conservative: supplier notice date is used unless separately verified.
    periods = sorted({f'{int(asof[:4])-y}-12-31' for y in range(1,11)} | {asof[:4]+'-'+m for m in ('03-31','06-30','09-30') if asof[:4]+'-'+m <= asof})
    periods = sorted(set(periods) | {str(int(p[:4])-1)+p[4:] for p in periods})
    rows = []; evidence = []
    for start in range(0,len(members),50):
        group = members[start:start+50]; codes = ','.join('"'+r['code']+'"' for r in group)
        for kind, report, cols in [('profit','RPT_LICO_FN_CPD','SECURITY_CODE,REPORTDATE,NOTICE_DATE,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT'),
                                    ('cash','RPT_DMSK_FN_CASHFLOW','SECURITY_CODE,REPORT_DATE,NOTICE_DATE,NETCASH_OPERATE')]:
            params = dict(reportName=report, columns=cols, pageSize=500, pageNumber=1, filter=f'(SECURITY_CODE in ({codes}))(REPORT_DATE>=\'{periods[0]}\')', source='WEB', client='WEB')
            # CPD uses REPORTDATE while cash-flow uses REPORT_DATE.
            if kind=='profit': params['filter']=params['filter'].replace('REPORT_DATE','REPORTDATE')
            page = 1
            while True:
                params['pageNumber']=page
                obj, src=request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params));evidence.append(src)
                result=obj.get('result') or {}
                if not isinstance(result.get('data'),list): raise market.ProviderError('财务源未返回匹配报表')
                for r in result['data']:
                    p=str(r.get('REPORTDATE') or r.get('REPORT_DATE'))[:10]; published=str(r.get('NOTICE_DATE') or '')[:10]
                    if p not in periods or not published or published>asof: continue
                    item=next((x for x in rows if x['code']==r['SECURITY_CODE'] and x['period']==p),None)
                    if item is None: item=dict(code=r['SECURITY_CODE'],period=p);rows.append(item)
                    for field, col in ({'revenue':'TOTAL_OPERATE_INCOME','profit':'PARENT_NETPROFIT'} if kind=='profit' else {'operating_cf':'NETCASH_OPERATE'}).items():
                        item[field]=None if r.get(col) is None else ds(dec(r[col]));item[field+'_published_at']=published
                if page>=int(result.get('pages',1)): break
                page+=1
                if page>100: raise market.ProviderError('财务分页超过安全上限')
    return dict(rows=rows,evidence=evidence, effective_date=max((r[k] for r in rows for k in r if k.endswith('_published_at')),default=None), periods=periods,
                history_status='unavailable',history_reason='未取得逐年核验的历史有效成分；不以当前篮子回填历史指数', version_basis='current_restated')
