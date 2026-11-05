"""Bounded public-data adapters. No portfolio information leaves the device."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from .. import market
from ..common import ZONE, day, dec, ds, now, today

CSI = 'https://www.csindex.com.cn'
CNI = 'https://www.cnindex.com.cn'
# Explicitly paired official identities; unknown codes are never prefix-matched.
CATALOG = [
    dict(code='000688', return_code='000688CNY01', name='科创50', provider='csi', kind='宽基', launch='2020-07-23', exposure='CN'),
    dict(code='H11136', return_code='H01136', name='中国互联网', provider='csi', kind='行业', launch='2011-09-20', exposure='overseas'),
    dict(code='000300', return_code='H00300', name='沪深300', provider='csi', kind='宽基', launch='2005-04-08'),
    dict(code='000905', return_code='H00905', name='中证500', provider='csi', kind='宽基', launch='2007-01-15'),
    dict(code='000985', return_code='H00985', name='中证全指', provider='csi', kind='宽基', launch='2011-08-02'),
    dict(code='399006', return_code='399606', name='创业板指', provider='cni', kind='宽基', launch='2010-06-01',return_launch='2010-06-01',identity_source='https://www.cnindex.com.cn/zh_information/notices_news/2010/201206/P020191213351661589949.pdf'),
    dict(code='000922', return_code='H00922', name='中证红利', provider='csi', kind='策略', launch='2008-05-26'),
    dict(code='000819', return_code='H00819', name='有色金属', provider='csi', kind='行业', launch='2009-07-03'),
    dict(code='000510', return_code='000510CNY010', name='中证A500', provider='csi', kind='宽基', launch='2024-09-23'),
    dict(code='000993', return_code='H00993', name='全指信息', provider='csi', kind='行业', launch='2011-08-02'),
    dict(code='930955', return_code='H20955', name='红利低波100', provider='csi', kind='策略', launch='2017-05-26'),
    dict(code='399998', return_code='H20596', name='中证煤炭', provider='csi', kind='行业', launch='2015-02-13'),
    dict(code='000991', return_code='H00991', name='全指医药', provider='csi', kind='行业', launch='2011-08-02'),
]
def identity(code):
    code = str(code).strip().upper()
    found = next((p for p in CATALOG if code in (p['code'], p['return_code'])), None)
    if not found: raise market.ProviderError('当前尚未支持这个指数的官方代码配对；不按代码前缀猜测')
    p = dict(found, currency='CNY', input_basis='total_return' if code == found['return_code'] else 'price')
    p['method_url'] = (CNI + '/docs/gz_399006.pdf' if p['provider'] == 'cni' else
        'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/' + p['code'] + 'factsheet.pdf')
    p['return_launch_status'] = 'verified' if p.get('return_launch') else 'not_independently_verified'
    return p


def module_capabilities(profile):
    from .lixinger import MODULES
    result={m:dict(supported=True,reason=None) for m in MODULES}
    if profile.get('exposure')=='overseas':
        for m in MODULES:
            if m not in ('price','total_return','memberships'):
                result[m]=dict(supported=False,reason='跨市场理杏仁身份、权限及币种未核验；已有官方行情与当前成分保留')
    return result


def constituent_identity(code, exchange, overseas=False):
    value = str(code).strip().upper()
    if overseas:
        if exchange in ('香港证券交易所','香港联合交易所') and re.fullmatch(r'\d{1,5}\.HK', value):
            return dict(code=value[:-3].zfill(5)+'.HK',market='HK',ticker=value[:-3].zfill(5),exchange=exchange)
        if any(x in exchange for x in ('纳斯达克','纽约','美国证券交易所')) and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,15}\.(O|N|A)',value):
            return dict(code=value,market='US',ticker=value.rsplit('.',1)[0],exchange=exchange)
        raise market.ProviderError('境外成分代码与官方交易所无法匹配；拒绝按A股代码推断')
    normalized = cell_code(code)
    if not re.fullmatch(r'\d{6}', normalized): raise market.ProviderError('境内成分代码无效')
    venue = {'上海证券交易所':'SH','深圳证券交易所':'SZ','北京证券交易所':'BJ'}.get(exchange) or ('SH' if normalized.startswith('6') else 'SZ')
    return dict(code=normalized,market=venue,exchange=exchange)


def cutoff():
    t = datetime.now(ZONE)
    d = t.date() - timedelta(days=1) if t.hour < 16 else t.date()
    while d.weekday() > 4: d -= timedelta(days=1)
    return d.isoformat()


def request(url):
    raw = market.fetch(url, timeout=20, attempts=2)
    return market.parse_json(raw), {'url': url, 'fetched_at': now(), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def prices(profile, basis, asof, start_date=None):
    code = profile['return_code'] if basis == 'total_return' else profile['code']
    if profile['provider'] == 'cni':
        obj, evidence = request('https://hq.cnindex.com.cn/market/market/getIndexDailyData?' + urlencode(
            dict(indexCode=code, startDate=start_date or '2000-01-01', endDate=asof)))
        data = obj.get('data') or {}
        if data.get('indexCode') != code: raise market.ProviderError('国证日线返回身份不匹配')
        columns = data['item']; result = [dict(date=datetime.fromtimestamp(int(r[columns.index('timestamp')]) / 1000, ZONE).date().isoformat(),
             close=ds(dec(r[columns.index('close')], positive=True))) for r in data['data']]
    else:
        obj, evidence = request(CSI + '/csindex-home/perf/index-perf?' + urlencode(
            dict(indexCode=code, startDate=(start_date or '1999-01-01').replace('-',''), endDate=asof.replace('-', ''))))
        raw = sorted(obj.get('data') or [], key=lambda r: str(r['tradeDate']))
        if any(r.get('indexCode') != code for r in raw): raise market.ProviderError('中证日线返回身份不匹配')
        # Preserve the verified exclusion of the API's artificial query-start row.
        result = [dict(date=day(str(r['tradeDate'])).isoformat(), close=ds(dec(r['close'], positive=True)),
                       reference_pe=ds(dec(r['peg'])) if r.get('peg') is not None and dec(r['peg'])>0 else None) for r in raw[1:]]
    start=(profile.get('return_launch') or profile['launch']) if basis=='total_return' else profile['launch']
    result = sorted([r for r in result if start <= r['date'] <= asof],key=lambda r:r['date'])
    if not result: raise market.ProviderError('官方源未返回有效完整日线')
    return dict(rows=result, source='国证指数' if profile['provider'] == 'cni' else '中证指数', evidence=[evidence],
                series_code=code, basis=basis, effective_date=result[-1]['date'], theoretical_history_note='官方全收益回算序列；独立发布日期未核验，不代表ETF实盘' if basis=='total_return' and not profile.get('return_launch') else None)


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


def constituents(profile, asof):
    import requests
    if profile['provider']=='csi':
        url='https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/'+profile['code']+'closeweight.xls'
        r=requests.get(url,timeout=20);r.raise_for_status();data=excel_rows(r.content)[1:]
        if any(cell_code(row[1])!=profile['code'] for row in data):raise market.ProviderError('成分指数身份不匹配')
        rows=[dict(date=cell_date(row[0]),name=str(row[5]),weight=ds(dec(row[9])),industry='',source_code=str(row[4]),**constituent_identity(row[4],str(row[7]),profile.get('exposure')=='overseas' or profile['code']=='H11136')) for row in data]
    else:
        url=CNI+'/sample-detail/download-history?'+urlencode({'indexcode':profile['code']})
        r=requests.get(url,timeout=20);r.raise_for_status();data=excel_rows(r.content)[1:]
        rows=[dict(date=cell_date(row[0]),code=cell_code(row[1]),name=str(row[2]),weight=ds(dec(row[5])),market='SZ',industry=str(row[3]) if row[3] not in (None,'nan','-','') else '') for row in data]
    if not rows or len({r['date'] for r in rows})!=1 or rows[0]['date']>asof:raise market.ProviderError('权重快照日期无效或晚于截止日')
    if len({r['code'] for r in rows})!=len(rows) or abs(sum(dec(r['weight']) for r in rows)-100)>dec('.5'):raise market.ProviderError('成分重复或权重合计异常')
    return dict(rows=rows,effective_date=rows[0]['date'],source='官方月末权重',evidence=[dict(url=url,fetched_at=now(),sha256=hashlib.sha256(r.content).hexdigest())])


def stock_valuations(members, asof):
    if any(r['market'] not in ('SH','SZ','BJ') for r in members): raise market.ProviderError('境外成分估值尚无核验适配，不调用A股接口')
    rows=[];evidence=[]
    for pos in range(0,len(members),50):
        codes={r['code'] for r in members[pos:pos+50]}
        quoted=','.join('"'+c+'"' for c in sorted(codes))
        params=dict(reportName='RPT_VALUEANALYSIS_DET',columns='SECURITY_CODE,TRADE_DATE,PE_TTM,PB_MRQ,BOARD_NAME',pageSize=500,pageNumber=1,
                    filter=f"(SECURITY_CODE in ({quoted}))(TRADE_DATE='{asof}')",source='WEB',client='WEB')
        obj,src=request('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params));evidence.append(src)
        result=obj.get('result') or {}
        if not isinstance(result.get('data'),list):raise market.ProviderError('历史公司估值源未返回指定日期数据')
        if int(result.get('pages',1))>1:raise market.ProviderError('公司估值返回超出单日批量上限，保留旧数据')
        seen=set()
        for r in result['data']:
            code=r['SECURITY_CODE'];d=str(r['TRADE_DATE'])[:10]
            if code not in codes or code in seen or d!=asof:raise market.ProviderError('公司估值代码或日期不符，拒绝混用盘中/其他日期')
            seen.add(code)
            def optional(v):return None if v in (None,'-','') else ds(dec(v))
            pe,pb=optional(r.get('PE_TTM')),optional(r.get('PB_MRQ'))
            rows.append(dict(code=code,date=d,pe=pe,pb=pb,
                pe_status='loss' if pe and dec(pe)<0 else 'zero_or_missing' if pe is not None and dec(pe)==0 else 'available' if pe is not None else 'missing_or_zero_base',
                pb_status='nonpositive_equity' if pb and dec(pb)<0 else 'zero_or_missing' if pb is not None and dec(pb)==0 else 'available' if pb is not None else 'missing',
                source='东方财富·历史估值',pe_field='PE_TTM',pb_field='PB_MRQ',industry=r.get('BOARD_NAME'),methodology_note='指定交易日PE TTM与PB MRQ；供应商分母未逐家公司独立复原'))
    if not rows:raise market.ProviderError('未取得指定交易日的公司估值，缺失不使用盘中快照替代')
    return dict(rows=rows,evidence=evidence,effective_date=max(r['date'] for r in rows),
                missing_codes=sorted({m['code'] for m in members}-{r['code'] for r in rows}),
                limitation='东方财富历史PE_TTM与PB_MRQ；按实际交易日期读取，缺失不补零。')


def financials(members, asof):
    if any(r['market'] not in ('SH','SZ','BJ') for r in members): raise market.ProviderError('境外成分财报口径尚未核验，不调用A股财务接口')
    # Official availability is conservative: supplier notice date is used unless separately verified.
    periods = sorted({f'{int(asof[:4])-y}-12-31' for y in range(1,11)} | {asof[:4]+'-'+m for m in ('03-31','06-30','09-30') if asof[:4]+'-'+m <= asof})
    periods = sorted(set(periods) | {str(int(p[:4])-1)+p[4:] for p in periods})
    def fetch_group(group):
        indexed={};evidence=[];allowed={r['code'] for r in group}
        codes=','.join('"'+r['code']+'"' for r in group)
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
                    if r['SECURITY_CODE'] not in allowed:raise market.ProviderError('财务源返回非请求代码')
                    item=indexed.setdefault((r['SECURITY_CODE'],p),dict(code=r['SECURITY_CODE'],period=p))
                    for field, col in ({'revenue':'TOTAL_OPERATE_INCOME','profit':'PARENT_NETPROFIT'} if kind=='profit' else {'operating_cf':'NETCASH_OPERATE'}).items():
                        item[field]=None if r.get(col) is None else ds(dec(r[col]));item[field+'_published_at']=published
                if page>=int(result.get('pages',1)): break
                page+=1
                if page>100: raise market.ProviderError('财务分页超过安全上限')
        return list(indexed.values()),evidence
    groups=[members[n:n+50] for n in range(0,len(members),50)]
    # Parallelize independent batches only; each report's pagination stays ordered.
    # A failed batch fails the module, preserving the last published snapshot.
    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='index-financials') as pool:
        results=list(pool.map(fetch_group,groups))
    rows=[r for batch,_ in results for r in batch];evidence=[e for _,sources in results for e in sources]
    return dict(rows=rows,evidence=evidence, effective_date=max((r[k] for r in rows for k in r if k.endswith('_published_at')),default=None), periods=periods,
                history_status='unavailable',history_reason='未取得逐年核验的历史有效成分；不以当前篮子回填历史指数', version_basis='current_restated')
