"""Public dividend notices only. Never infer an investor's tax or actual receipt."""
import re
import urllib.parse
import copy
import time
from html.parser import HTMLParser
from decimal import Decimal
from .common import DomainError, day, dec, ds
from . import market


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.stack=[];self.cell=None;self.row=None;self.ignore=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.ignore+=1
        if tag=='table':self.stack.append([])
        if self.stack and tag=='tr':self.row=[]
        if self.stack and tag in ('td','th'):self.cell=[]
    def handle_data(self,data):
        if self.cell is not None and not self.ignore:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.ignore:self.ignore-=1
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(re.sub(r'\s+','',''.join(self.cell)))
            self.cell=None
        if tag=='tr' and self.stack and self.row is not None:self.stack[-1].append(self.row);self.row=None
        if tag=='table' and self.stack:self.tables.append(self.stack.pop())


def date_or_none(value):
    if value is None:return None
    text=str(value).strip()
    if text in ('','--','-','None','null'):return None
    m=re.search(r'(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})',text)
    if not m:raise market.ProviderError('分红日期格式已变化，请核对来源')
    return str(day(f'{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}'))


def parse_fund(text,code):
    parser=Tables();parser.feed(text)
    for table in parser.tables:
        header=next((r for r in table if any('每10份分红' in c for c in r)),None)
        if header is None:continue
        def index(label):
            try:return next(n for n,c in enumerate(header) if label in c)
            except StopIteration:raise market.ProviderError('基金分红表缺少'+label) from None
        reg,ex,rate,pay=(index(label) for label in ('权益登记日','除息日','每10份分红','分红发放日'))
        rows=[]
        for row in table[table.index(header)+1:]:
            if len(row)==len(header)-1 and '年份' in header[0]:row=['']+row
            if len(row)!=len(header):continue
            ex_date=date_or_none(row[ex])
            if not ex_date:continue
            match=re.fullmatch(r'(?:每10份派现金)?([\d,]+(?:\.\d+)?)元?',row[rate])
            if not match:raise market.ProviderError('基金每10份分红不是可核对的数字')
            per_ten=dec(match[1].replace(',',''))
            if per_ten<=0:continue
            rows.append({'source_ref':f'fund:{code}:{ex_date}', 'code':code,'record_date':date_or_none(row[reg]),
                'ex_date':ex_date,'pay_date':date_or_none(row[pay]),'per_share':ds(per_ten/Decimal(10)),
                'currency':'CNY','source':'天天基金·分红送配','source_url':f'https://fundf10.eastmoney.com/fhsp_{code}.html',
                'original_rate':ds(per_ten),'original_unit':'每10份','status':'published'})
        return rows
    raise market.ProviderError('未找到基金每10份分红表；保留旧公告，不把缺失当作没有分红')


def _fetch_notices(instrument):
    if instrument.get('builtin') or instrument['type']=='index':return None
    if instrument['market'] in ('SH','SZ','FUND') and instrument['type'] in ('etf','lof','fund'):
        if instrument['currency']!='CNY':return None
        code=instrument['code']
        return parse_fund(market.fetch(f'https://fundf10.eastmoney.com/fhsp_{code}.html','https://fund.eastmoney.com/'),code)
    if instrument['market'] in ('SH','SZ') and instrument['type']=='stock':
        code=instrument['code']
        records=stock_records(code)
        return parse_stock(records,code) if records is not None else None
    if instrument['market'] in ('HK','US'):return overseas_notices(instrument)
    return None


_NOTICE_CACHE={}
_STOCK_CACHE={}

def stock_records(code):
    cached=_STOCK_CACHE.get(code)
    if cached and time.monotonic()-cached[0]<600:return copy.deepcopy(cached[1])
    rows=reports('https://datacenter-web.eastmoney.com/api/data/v1/get',{
        'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','filter':f'(SECURITY_CODE="{code}")',
        'pageSize':100,'sortColumns':'REPORT_DATE','sortTypes':'-1','source':'WEB','client':'WEB'})
    _STOCK_CACHE[code]=(time.monotonic(),copy.deepcopy(rows))
    return rows

def parse_stock_splits(records, code):
    """Implemented per-ten bonus/conversion shares; never infer from price jumps."""
    rows={}
    for r in records:
        if r.get('SECURITY_CODE')!=code:raise market.ProviderError('A股送转源代码不匹配')
        if r.get('ASSIGN_PROGRESS')!='实施分配':continue
        ex=date_or_none(r.get('EX_DIVIDEND_DATE'))
        if not ex:continue
        parts=[r.get('BONUS_RATIO'),r.get('IT_RATIO')]
        total=r.get('BONUS_IT_RATIO')
        if all(v is not None for v in parts):
            value=sum((dec(v,nonnegative=True) for v in parts),Decimal(0))
            if total is not None and value!=dec(total,nonnegative=True):
                raise market.ProviderError('送股与转增合计不一致，请核对公告')
        elif total is not None:value=dec(total,nonnegative=True)
        else:raise market.ProviderError('已实施公告缺少送转比例，未推定为零')
        if value==0:continue
        record_date=date_or_none(r.get('EQUITY_RECORD_DATE'))
        if not record_date or record_date>=ex:raise market.ProviderError('送转股登记日或除权日不完整')
        action=dict(kind='split_notice',date=ex,factor=ds(1+value/10),record_date=record_date,
                    source='东方财富·已实施送转股',source_url=f'https://data.eastmoney.com/yjfp/detail/{code}.html',
                    source_ref=f'stock-split:{code}:{ex}',bonus_per_ten=ds(value),
                    coverage_note='每10股送股与转增合计；仅生成核对事项，不自动改写持仓。')
        if ex in rows and rows[ex]['factor']!=action['factor']:raise market.ProviderError('同日送转比例冲突，请核对公告')
        rows[ex]=action
    return list(rows.values())

def fetch_stock_splits(instrument):
    records=stock_records(instrument['code'])
    if records is None:raise market.ProviderError('送转股公告暂不可得，保留旧缓存')
    return parse_stock_splits(records,instrument['code'])

def fetch_notices(instrument):
    key=(instrument['market'],instrument['code'],instrument['currency']);cached=_NOTICE_CACHE.get(key)
    if cached and time.monotonic()-cached[0]<600:return copy.deepcopy(cached[1])
    result=_fetch_notices(instrument);_NOTICE_CACHE[key]=(time.monotonic(),copy.deepcopy(result));return result


def reports(base,params):
    result=[];page=1
    while True:
        obj=market.parse_json(market.fetch(base+'?'+urllib.parse.urlencode(dict(params,pageNumber=page)),'https://data.eastmoney.com/'))
        if obj.get('code')==9201:return None
        data=obj.get('result')
        if not obj.get('success') or not isinstance(data,dict) or not isinstance(data.get('data'),list):
            raise market.ProviderError('分红接口结构已变化，保留旧公告')
        result.extend(data['data']);pages=int(data.get('pages') or 1)
        if pages>50:raise market.ProviderError('分红公告页数超过限制，未把截断结果当完整历史')
        if page>=pages:break
        page+=1
    return result


def parse_stock(records,code):
    rows=[]
    for record in records:
        if record.get('SECURITY_CODE')!=code:raise market.ProviderError('A股分红源代码不匹配')
        if record.get('ASSIGN_PROGRESS')!='实施分配':continue
        ex=date_or_none(record.get('EX_DIVIDEND_DATE'))
        value=record.get('PRETAX_BONUS_RMB')
        if not ex or value is None or dec(value)<=0:continue
        period=date_or_none(record.get('REPORT_DATE')) or ''
        rows.append({'source_ref':f'stock:{code}:{period}:{ex}','code':code,'record_date':date_or_none(record.get('EQUITY_RECORD_DATE')),
            'ex_date':ex,'pay_date':None,'per_share':ds(dec(value)/Decimal(10)),'currency':'CNY','source':'东方财富·已实施现金分红',
            'source_url':'https://data.eastmoney.com/yjfp/detail/'+code+'.html','original_rate':ds(dec(value)),'original_unit':'每10股',
            'status':'implemented','coverage_note':'接口未提供派息日，请按实际到账补充；不把公告日当作到账日。'})
    return rows


def parse_hk(records,code):
    rows=[]
    for record in records:
        if record.get('SECURITY_CODE')!=code:raise market.ProviderError('港股分红源代码不匹配')
        text=re.sub(r'\s+','',record.get('PLAN_EXPLAIN') or '')
        match=re.fullmatch(r'每股派(?:发)?(港币|港元|美元|人民币)([\d,.]+)元',text)
        ex=date_or_none(record.get('EX_DIVIDEND_DATE'))
        if not match or not ex:continue
        value=dec(match[2].replace(',',''))
        if value<=0:continue
        rows.append({'source_ref':f'hk:{code}:{record.get("YEAR","")}:{record.get("REPORT_TYPE","")}:{ex}',
            'code':code,'record_date':None,'ex_date':ex,'pay_date':date_or_none(record.get('DIVIDEND_DATE')),
            'per_share':ds(value),'currency':{'港币':'HKD','港元':'HKD','美元':'USD','人民币':'CNY'}[match[1]],
            'source':'东方财富·港股现金分红公告','source_url':f'https://emweb.eastmoney.com/PC_HKF10/pages/home/index.html?code={code}&type=web&color=b',
            'original_rate':ds(value),'original_unit':'每股','status':'published',
            'coverage_note':'仅解析明确币种的现金方案；实物派股等未自动处理。港股按除息日前账本持仓估算权益，过户截止期间不当作登记日。'})
    return rows if rows else None

# Explicit issuer identities: no guessing URL slugs or mixing unlisted classes.
GLOBALX_HK={
    '03110':'https://www.globalxetfs.com.hk/funds/hang-seng-high-dividend-yield-etf/',
    '03116':'https://www.globalxetfs.com.hk/funds/asia-pacific-high-dividend-yield-etf/',
    '03419':'https://www.globalxetfs.com.hk/funds/covered-call-etf/',
}

class DistributionCalendar(HTMLParser):
    def __init__(self):super().__init__();self.depth=0;self.active=False;self.row=None;self.span=None;self.rows=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='ul':
            if attrs.get('id')=='dcal':self.active=True;self.depth=1
            elif self.active:self.depth+=1
        if not self.active:return
        if tag=='li' and 'clearfix' in attrs.get('class','').split():self.row=[]
        if tag=='span' and self.row is not None:self.span=[]
    def handle_data(self,data):
        if self.active and self.span is not None:self.span.append(data)
    def handle_endtag(self,tag):
        if not self.active:return
        if tag=='span' and self.span is not None:
            self.row.append(' '.join(''.join(self.span).split()));self.span=None
        if tag=='li' and self.row is not None:
            if len(self.row)==5:self.rows.append(self.row)
            self.row=None
        if tag=='ul':
            self.depth-=1
            if self.depth<=0:self.active=False

def parse_globalx(text,code):
    if not re.search(r'Dividend\s+Per\s+Share\s*\(HKD\)',re.sub('<[^>]+>',' ',text),re.I):raise market.ProviderError('发行方派息表币种或单位已变化，未自动套用港币')
    parser=DistributionCalendar();parser.feed(text);rows=[]
    from datetime import datetime
    for ex,pay,amount,income,capital in parser.rows:
        try:ex=datetime.strptime(ex,'%d %B %Y').date().isoformat();pay=datetime.strptime(pay,'%d %B %Y').date().isoformat()
        except ValueError:raise market.ProviderError('发行方分红日期格式已变化') from None
        value=dec(amount.replace(',',''),positive=True)
        rows.append({'code':code,'source_ref':f'cash:HK:{code}:{ex}:HKD','record_date':None,'ex_date':ex,'pay_date':pay,
            'per_share':ds(value),'currency':'HKD','source':'Global X香港·官方上市类别分派表','source_url':GLOBALX_HK[code],
            'original_rate':ds(value),'original_unit':'每份HKD','income_distribution_ratio':income,'capital_distribution_ratio':capital,'status':'published',
            'coverage_note':f'官网展示的历史分派记录；收入支付比例{income}、资本支付比例{capital}，不代表全部来自经营收入。港股按除息日前持仓估算。'})
    if not rows:raise market.ProviderError('未找到发行方上市类别派息表，保留旧缓存')
    return rows

def parse_nasdaq(obj,code):
    from datetime import datetime
    table=(obj.get('data') or {}).get('dividends') or {};records=table.get('rows')
    if not isinstance(records,list):return None
    def date(value):
        if value in (None,'','N/A','--','-'):return None
        try:return datetime.strptime(value,'%m/%d/%Y').date().isoformat()
        except ValueError:raise market.ProviderError('美股派息日期格式已变化') from None
    groups={};unknown=set()
    for r in records:
        ex=date(r.get('exOrEffDate'))
        if not ex:continue
        if r.get('type')!='Cash':unknown.add(ex);continue
        amount=str(r.get('amount') or '').strip();cur=r.get('currency')
        if cur not in (None,'','USD') or not re.fullmatch(r'\$[\d,]+(?:\.\d+)?',amount):unknown.add(ex);continue
        value=dec(amount[1:].replace(',',''),positive=True)
        groups.setdefault(ex,[]).append((r,value))
    rows=[]
    for ex,items in groups.items():
        if ex in unknown:continue
        pays={date(r.get('paymentDate')) for r,_ in items}
        if len(pays)>1:continue
        value=sum((v for _,v in items),Decimal(0));r=items[0][0]
        rows.append({'code':code,'source_ref':f'cash:US:{code}:{ex}:USD','record_date':date(r.get('recordDate')),'ex_date':ex,'pay_date':pays.pop(),
            'per_share':ds(value),'currency':'USD','source':'Nasdaq现金派息表','source_url':'https://www.nasdaq.com/market-activity/stocks/'+code.lower()+'/dividend-history',
            'original_rate':ds(value),'original_unit':'每股USD','status':'published','source_types':[r.get('type') for r,_ in items],
            'coverage_note':'仅接入源返回的Cash行，未知分派类型未自行归类；按已核实除息日前持仓估算，实际扣税与到账需核对。'})
    return rows

def overseas_notices(inst):
    from . import yahoo
    primary=None;errors=[]
    if inst['market']=='HK' and inst['code'] in GLOBALX_HK:
        try:primary=parse_globalx(market.fetch(GLOBALX_HK[inst['code']],GLOBALX_HK[inst['code']]),inst['code'])
        except (market.ProviderError,ValueError) as exc:errors.append(str(exc))
    elif inst['market']=='US':
        try:primary=parse_nasdaq(market.parse_json(market.fetch('https://api.nasdaq.com/api/quote/'+urllib.parse.quote(inst['code'],safe='')+'/dividends?assetclass=stocks','https://www.nasdaq.com/')),inst['code'])
        except (market.ProviderError,ValueError,KeyError) as exc:errors.append(str(exc))
    else:
        try:
            records=reports('https://datacenter.eastmoney.com/securities/api/data/v1/get',{'reportName':'RPT_HKF10_MAIN_DIVBASIC','columns':'ALL','filter':f'(SECURITY_CODE="{inst["code"]}")(IS_BFP="0")','pageSize':100,'sortColumns':'NOTICE_DATE,EX_DIVIDEND_DATE','sortTypes':'-1,-1','source':'F10','client':'PC'})
            primary=parse_hk(records,inst['code']) if records is not None else None
        except (market.ProviderError,ValueError,KeyError) as exc:errors.append(str(exc))
    # A successful primary table is authoritative for its displayed coverage.
    # Avoid redundant Yahoo traffic and mixing rounded secondary amounts.
    if primary:
        return [dict(r,source_ref=f'cash:{inst["market"]}:{inst["code"]}:{r["ex_date"]}:{r["currency"]}') for r in primary]
    # Never add amounts from two providers for the same ex-day.
    try:fallback=yahoo.dividends(inst)
    except (market.ProviderError,ValueError,KeyError) as exc:
        errors.append(str(exc));fallback=None
    if primary is None and fallback is None:raise market.ProviderError('港美股分派源暂不可用；'+'；'.join(errors)[:300])
    chosen={r['ex_date']:dict(r,source_ref=f'cash:{inst["market"]}:{inst["code"]}:{r["ex_date"]}:{r["currency"]}') for r in (primary or [])}
    for r in fallback or []:
        if r['ex_date'] not in chosen:chosen[r['ex_date']]=r
    return sorted(chosen.values(),key=lambda r:r['ex_date'],reverse=True)
