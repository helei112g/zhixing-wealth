"""Free public sources, bounded requests, explicit fields and atomic group publication."""
import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests

from ..common import DomainError, day, dec, ds, now, today
from .metrics import monthly_rows
from .catalog import GROUPS, SERIES

HOSTS = {'datacenter-web.eastmoney.com', 'www.chinamoney.com.cn', 'yield.chinabond.com.cn',
         'home.treasury.gov', 'www.pbc.gov.cn', 'www.stats.gov.cn', 'legulegu.com', 'money.finance.sina.com.cn'}
EM = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
PBC = 'https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html'
CALENDAR = 'https://www.stats.gov.cn/sj/fbrc/bnxxfb/'


class Document(HTMLParser):
    """Read text, links and table cells without executing remote HTML or JavaScript."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows, self.links, self.text = [], [], []
        self.meta = {}
        self.row, self.cell, self.anchor = None, None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('name')] = attrs.get('content')
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
        elif tag == 'a':
            self.anchor = [attrs.get('href', ''), [], ''.join(self.text)[-500:]]

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row.append(''.join(self.cell).strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == 'a' and self.anchor is not None:
            url, text, context = self.anchor
            self.links.append((url, ''.join(text).strip(), context))
            self.anchor = None


def number(value, limit=10000):
    if value is None or str(value).strip() in ('', '-', '--', '…', '……'):
        return None
    result = dec(str(value).strip().replace(',', ''))
    if abs(result) > limit:
        raise DomainError('来源数值超出指标校验范围')
    return ds(result)


def text_body(raw):
    match = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', raw[:2048], re.I)
    encoding = match.group(1).decode('ascii') if match else 'utf-8-sig'
    return raw.decode(encoding)


class Reader:
    def __init__(self, cancelled=lambda: False):
        self.evidence = []
        self.notes = []
        self.cancelled = cancelled

    def get(self, url, *, session=None, headers=None, evidence_url=None):
        if self.cancelled():
            raise DomainError('市场数据更新已中断')
        for _ in range(4):
            parsed = urlsplit(url)
            if parsed.scheme not in ('https', 'http') or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port:
                raise DomainError('来源地址不在公开数据白名单内')
            # Public PBC links sometimes use HTTP; always transport them with TLS.
            url = 'https://' + parsed.netloc + parsed.path + ('?' + parsed.query if parsed.query else '')
            try:
                with (session or requests).get(url, timeout=(5, 25), headers={'User-Agent': 'Mozilla/5.0', **(headers or {})},
                                  stream=True, allow_redirects=False) as response:
                    if response.is_redirect:
                        target = urljoin(url, response.headers.get('Location', ''))
                        if headers and urlsplit(target).hostname != parsed.hostname:
                            raise DomainError('来源会话不能跨域重定向')
                        url = target
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 8 * 1024 * 1024 or self.cancelled():
                            raise DomainError('来源响应过大或更新已中断')
                        chunks.append(chunk)
            except requests.RequestException as exc:
                raise DomainError(f'{parsed.hostname} 请求失败（{type(exc).__name__}），保留原数据') from None
            raw = b''.join(chunks)
            self.evidence.append(dict(url=evidence_url or url, fetched_at=now(), sha256=hashlib.sha256(raw).hexdigest()))
            return raw
        raise DomainError('来源重定向超过限制')

    def html(self, url):
        return Document(text_body(self.get(url)))


def full_market(reader, asof):
    """Keep the original Skill's exact median fields, reduced to final observation per month."""
    result = {}
    specifications = (
        ('full_market_pe', 'middlePETTM', 'market-ttm-lyr', '5', 'a-ttm-lyr'),
        ('full_market_pb', 'middlePB', 'market-index-pb', 'ALL', 'all-pb'),
    )
    with requests.Session() as session:
        for key, field, endpoint, market, page in specifications:
            page_url = 'https://legulegu.com/stockdata/' + page
            document = Document(text_body(reader.get(page_url, session=session)))
            csrf = document.meta.get('_csrf')
            if not csrf:
                raise DomainError('乐咕页面缺少会话字段，保留原数据')
            endpoint_url = 'https://legulegu.com/api/stock-data/' + endpoint
            # Public page request signature; never persist session tokens or cookies.
            signature = hashlib.md5(str(today()).encode()).hexdigest()
            raw = reader.get(endpoint_url + '?' + urlencode(dict(marketId=market, token=signature)),
                             session=session, headers={'X-CSRF-Token': csrf, 'Referer': page_url},
                             evidence_url=endpoint_url + '?' + urlencode(dict(marketId=market)))
            payload = json.loads(raw)
            rows = payload.get('data') if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or not rows or len(rows) > 20000:
                raise DomainError('乐咕全市场历史为空或结构变化')
            normalized, seen = [], set()
            for row in rows:
                if field not in row:
                    raise DomainError('乐咕全市场中位数字段缺失，不用其他口径替代')
                stamp = row.get('date')
                stamp = (datetime.fromtimestamp(stamp / 1000, ZoneInfo('Asia/Shanghai')).date().isoformat()
                         if isinstance(stamp, (int, float)) else str(day(str(stamp)[:10])))
                if stamp in seen:
                    raise DomainError('乐咕全市场存在重复日期')
                seen.add(stamp)
                if stamp <= asof:
                    normalized.append(dict(date=stamp, value=number(row[field]), published_at=None, field=field))
            result[key] = list(monthly_rows(sorted(normalized, key=lambda row: row['date'])).values())
    reader.notes.append('乐咕全市场中位数middlePETTM / middlePB；历史按各月最后观测保留，未补造日频数据。')
    return result


def composites(reader):
    result = {}
    for key, symbol in (('sh_composite', 'sh000001'), ('sz_composite', 'sz399106')):
        params = dict(symbol=symbol, scale=240, ma='no', datalen=20000)
        url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?'
        rows = json.loads(reader.get(url + urlencode(params)))
        if not isinstance(rows, list) or not rows or len(rows) > 20000:
            raise DomainError('新浪综指历史为空或规模无效')
        result[key] = [dict(date=str(day(row['day'])), value=number(row['close'], limit=1000000), published_at=None) for row in rows]
    reader.notes.append('上证综指000001与深证综指399106；日频收盘点位，未复权价格指数，不含分红再投资。')
    return result


def em_series(reader, report, fields, date_field, start, end):
    series = {key: [] for key in fields}
    pages = 1
    for page in range(1, 31):
        params = dict(reportName=report, columns=','.join([date_field, *fields.values()]),
                      pageSize=1000, pageNumber=page, sortColumns=date_field, sortTypes=-1,
                      source='WEB', client='WEB', filter=f'({date_field}>=\'{start}\')({date_field}<=\'{end}\')')
        value = json.loads(reader.get(EM + '?' + urlencode(params)))
        result = value.get('result')
        if value.get('success') is not True or not isinstance(result, dict) or not result.get('data'):
            raise DomainError('东方财富未返回可核验数据')
        pages = result.get('pages')
        if type(pages) is not int or not 1 <= pages <= 30:
            raise DomainError('东方财富分页规模无效')
        for row in result['data']:
            stamp = str(day(row[date_field][:10]))
            if any(field not in row for field in fields.values()):
                raise DomainError('东方财富指标字段变化，未替换历史')
            if all(row[field] is None for field in fields.values()):
                continue
            if not start <= stamp <= end:
                raise DomainError('东方财富返回日期超出请求范围')
            for key, field in fields.items():
                if field not in row:
                    raise DomainError('东方财富指标字段变化，未替换历史')
                series[key].append(dict(date=stamp, value=number(row[field]), published_at=None))
        if page == pages:
            return series
    raise DomainError('东方财富分页未完成')


def treasury(reader, start, end):
    series = {key: [] for key in ('us_2y', 'us_10y', 'us_30y')}
    for year in range(int(start[:4]), int(end[:4]) + 1):
        url = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?'
        raw = reader.get(url + urlencode(dict(data='daily_treasury_yield_curve', field_tdr_date_value=year)))
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise DomainError('美国财政部返回非预期XML')
        root = ET.fromstring(raw)
        properties = root.findall('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties')
        if not properties:
            raise DomainError('美国财政部年度数据为空，未发布残缺历史')
        for prop in properties:
            row = {node.tag.rsplit('}', 1)[-1]: node.text for node in prop}
            stamp = str(day(row['NEW_DATE'][:10]))
            if not start <= stamp <= end:
                continue
            for key, field in (('us_2y', 'BC_2YEAR'), ('us_10y', 'BC_10YEAR'), ('us_30y', 'BC_30YEAR')):
                if field not in row:
                    raise DomainError('美国财政部期限字段变化')
                series[key].append(dict(date=stamp, value=number(row[field]), published_at=None))
    return series


def credit(reader, start, end):
    result = {'gov_3y': [], 'aaa_3y': []}
    first = day(start)
    while first <= day(end):
        last = min(date(first.year, 12, 31), day(end))
        query = urlencode(dict(startDate=str(first), endDate=str(last), gjqx='0', qxId='ycqx', locale='cn_ZH'))
        document = reader.html('https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?' + query)
        header = next((r for r in document.rows if r[:2] == ['曲线名称', '日期']), None)
        if not header or '3年' not in header:
            raise DomainError('中债历史表头变化，未发布')
        column = header.index('3年')
        counts = {key: 0 for key in result}
        for row in document.rows:
            if not row:
                continue
            key = {'中债国债收益率曲线': 'gov_3y', '中债中短期票据收益率曲线(AAA)': 'aaa_3y'}.get(row[0])
            if key:
                stamp = str(day(row[1]))
                result[key].append(dict(date=stamp, value=number(row[column]), published_at=None))
                counts[key] += 1
        if not all(counts.values()):
            raise DomainError('中债年度分片或精确AAA中票曲线缺失，保留原数据')
        first = last + timedelta(days=1)
    return result


def repo(reader):
    result = {}
    for key, name in (('fr007', 'frr'), ('fdr007', 'fdr')):
        url = f'https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/{name}-chrt.csv'
        rows = []
        for row in csv.reader(io.StringIO(text_body(reader.get(url)))):
            fields = [cell.strip() for cell in row if cell.strip()]
            if not fields:
                continue
            if len(fields) != 4:
                raise DomainError('定盘利率CSV结构变化')
            rows.append(dict(date=str(day(fields[0])), value=number(fields[2]), published_at=None))
        result[key] = rows
    return result


def social_financing(reader, start, end):
    directory = reader.html(PBC)
    result = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        year_link = next((urljoin(PBC, href) for href, label, _ in directory.links if label == f'{year}年统计数据'), None)
        if not year_link:
            raise DomainError('人民银行年度统计目录缺失')
        year_page = reader.html(year_link)
        section = next((urljoin(year_link, href) for href, label, _ in year_page.links if label == '社会融资规模'), None)
        if not section:
            raise DomainError('人民银行社融目录缺失')
        page = reader.html(section)
        link = next((urljoin(section, href) for href, _, context in page.links
                     if href.lower().endswith(('.htm', '.html')) and '社会融资规模存量统计表' in context
                     and context.rfind('社会融资规模存量统计表') > context.rfind('地区社会融资规模')), None)
        if not link:
            raise DomainError('人民银行社融存量表链接无法确认')
        data = reader.html(link)
        months = next(([re.sub(r'\s+', '', cell) for cell in row if re.fullmatch(r'\s*\d{4}\.\d{1,2}\s*', cell)]
                       for row in data.rows if sum(bool(re.fullmatch(r'\s*\d{4}\.\d{1,2}\s*', c)) for c in row) == 12), None)
        values = next((row for row in data.rows if len(row) >= 25 and '社会融资规模存量' in re.sub(r'\s+', '', row[0])), None)
        header = next((row for row in data.rows if len(row) >= 25 and '项目' in row[0]), None)
        text = ''.join(data.text)
        if months is None and re.search(rf'{year}\.Q[1-4]', text):
            reader.notes.append(f'{year}年社融存量表为季度观测，未混入月度序列。')
            continue
        columns_match = header and all('存量' in header[1 + offset * 2] and '增速' in header[2 + offset * 2] for offset in range(12))
        if not months or not values or not columns_match:
            raise DomainError(f'{year}年人民银行存量同比列无法核验，未以增量替代')
        for index, month in enumerate(months):
            year_value, month_value = map(int, month.split('.'))
            if year_value != year:
                raise DomainError('人民银行年度表身份不匹配')
            stamp = f'{year_value:04d}-{month_value:02d}-01'
            value = number(values[2 + index * 2])
            if value is not None and start <= stamp <= end:
                result.append(dict(date=stamp, value=value, published_at=None, source_url=link))
    return {'sf_stock_yoy': result}


def calendar(reader, year):
    document = reader.html(CALENDAR)
    text = ''.join(document.text)
    if f'{year}年国家统计局主要统计信息发布日程表' not in text:
        raise DomainError('国家统计局当前年度日历尚未取得；月度数据按低频检查')
    events = []
    for label, group in (('采购经理指数月度报告', 'pmi'), ('居民消费价格指数月度报告', 'prices')):
        row = next((row for row in document.rows if any(label in cell for cell in row)), None)
        if not row or len(row) < 14:
            raise DomainError('国家统计局日历表结构变化')
        for month, cell in enumerate(row[-12:], 1):
            for value in re.findall(r'(\d{1,2})\s*/', cell):
                stamp = date(year, month, int(value))
                period = stamp.strftime('%Y-%m')
                if group == 'prices' or stamp.day <= 10:
                    period = (stamp.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
                events.append(dict(group=group, date=str(stamp), period=period))
    if not all(any(e['group'] == group for e in events) for group in ('pmi', 'prices')):
        raise DomainError('国家统计局日历缺少目标指标')
    return dict(year=year, events=events)


def collect(group, asof, previous=None, cancelled=lambda: False):
    if group not in GROUPS:
        raise DomainError('市场数据组无效')
    reader = Reader(cancelled)
    earliest = f'{int(asof[:4]) - 11}-01-01'
    if group in ('full_market', 'composites', 'china_rates'):
        earliest = '1990-01-01'
    # Reconcile complete recent years; old observations retain their original source and dates.
    start = earliest if previous is None else f'{int(asof[:4]) - 1}-01-01'
    common = dict(group=group, publisher=GROUPS[group]['publisher'], evidence=reader.evidence,
                  checked_at=now(), requested_asof=asof, published_at=None, series={})
    if group == 'calendar':
        return dict(common, calendar=calendar(reader, int(asof[:4])))
    if group == 'full_market':
        result = full_market(reader, asof)
    elif group == 'composites':
        result = composites(reader)
    elif group == 'china_rates':
        result = em_series(reader, 'RPTA_WEB_TREASURYYIELD',
                           {'cn_2y': 'EMM00588704', 'cn_10y': 'EMM00166466', 'cn_30y': 'EMM00166469'}, 'SOLAR_DATE', start, asof)
    elif group == 'us_rates':
        result = treasury(reader, start, asof)
    elif group == 'credit':
        result = credit(reader, start, asof)
    elif group == 'repo':
        result = repo(reader)
    elif group == 'social_financing':
        result = social_financing(reader, start, asof)
    else:
        definitions = {
            'pmi': [('RPT_ECONOMY_PMI', {'pmi': 'MAKE_INDEX', 'nonmanufacturing_pmi': 'NMAKE_INDEX'})],
            'prices': [('RPT_ECONOMY_CPI', {'cpi_yoy': 'NATIONAL_SAME'}), ('RPT_ECONOMY_PPI', {'ppi_yoy': 'BASE_SAME'})],
            'money': [('RPT_ECONOMY_CURRENCY_SUPPLY', {'m1_yoy': 'CURRENCY_SAME', 'm2_yoy': 'BASIC_CURRENCY_SAME'})],
        }
        result = {}
        for report, fields in definitions[group]:
            result.update(em_series(reader, report, fields, 'REPORT_DATE', earliest, asof))
    for key, rows in result.items():
        rows = [row for row in rows if earliest <= row['date'] <= asof]
        if not any(row['value'] is not None for row in rows):
            raise DomainError(f'{SERIES[key][1]}没有有效观测，保留原数据')
        old = (previous or {}).get('series', {}).get(key, [])
        if old and max(row['date'] for row in rows) < old[-1]['date']:
            raise DomainError(f'{SERIES[key][1]}返回旧日期，保留当前有效历史')
        combined = {row['date']: row for row in old}
        seen = set()
        for row in rows:
            if row['date'] in seen:
                raise DomainError('来源存在重复日期，未覆盖历史')
            seen.add(row['date'])
            combined[row['date']] = row
        result[key] = sorted(combined.values(), key=lambda row: row['date'])
        if group == 'full_market':
            result[key] = list(monthly_rows(result[key]).values())
    prior = (previous or {}).get('evidence', [])
    evidence = {(item['url'], item['sha256']): item for item in [*prior, *reader.evidence]}
    notes = list(dict.fromkeys([*(previous or {}).get('notes', []), *reader.notes]))
    return dict(common, series=result, evidence=list(evidence.values()), notes=notes)
