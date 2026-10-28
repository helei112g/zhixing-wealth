"""Code identities and the small built-in, verified total-return benchmark catalogue."""
import re
from .common import DomainError

BENCHMARKS = [
    {'id':'benchmark-csi300-tr','code':'H00300','name':'沪深300全收益','market':'CSI','type':'index','currency':'CNY',
     'source_url':'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300factsheet.pdf'},
    {'id':'benchmark-csiall-tr','code':'H00985','name':'中证全指全收益','market':'CSI','type':'index','currency':'CNY',
     'source_url':'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000985factsheet.pdf'},
    {'id':'benchmark-chinext-tr','code':'399606','name':'创业板全收益','market':'CNI','type':'index','currency':'CNY',
     'source_url':'https://www.cnindex.com.cn/docs/gz_399606.pdf'},
]
BENCHMARKS = [dict(i, builtin=True, series_kind='total_return', custom_name=True) for i in BENCHMARKS]


def describe(data):
    code = str(data.get('code') or '').strip().upper()
    market = str(data.get('market') or 'CN').upper()
    kind = data.get('type') or 'stock'
    if market in ('CN','SH','SZ'):
        if not re.fullmatch(r'\d{6}', code): raise DomainError('A股 / 场内基金代码须为6位数字')
        if code[0] in '0123': market = 'SZ'
        elif code[0] in '569': market = 'SH'
        else: raise DomainError('此代码不在当前沪深市场范围，请核对代码或选择其他品种')
        kind = 'etf' if code[0] in '15' else 'stock'
        if data.get('type') == 'index': kind = 'index'
        key = 'CN:' + code
    elif market == 'HK':
        if not code.isdigit() or len(code)>5: raise DomainError('港股 / 香港ETF代码须为1到5位数字')
        code = code.zfill(5); key = 'HK:' + code
        kind = 'etf' if kind == 'etf' else 'stock'
    elif market == 'US':
        code=code.replace('.','-')
        if not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}(?:-[A-Z0-9]{1,3})?',code):raise DomainError('美股代码须为股票或ETF交易代码，例如AAPL、VOO、BRK-B')
        if kind not in ('stock','etf'):raise DomainError('美股仅支持场内股票和ETF')
        if data.get('currency') not in (None,'','USD'):raise DomainError('美股场内标的使用USD记账')
        key='US:'+code
    elif market == 'FUND':
        if not re.fullmatch(r'\d{6}', code): raise DomainError('场外基金代码须为6位数字')
        kind = 'fund'; key = 'FUND:' + code
    elif market == 'CUSTOM':
        if not re.fullmatch(r'[A-Z0-9_.-]{1,40}', code): raise DomainError('自定义代码只接受字母、数字、点、横线和下划线')
        kind = 'custom'; key = 'CUSTOM:' + code
    else: raise DomainError('请选择A股 / 场内基金、场外基金、港股、美股或自定义品种')
    return {'code':code,'market':market,'type':kind,'identity':key,
            'currency':data.get('currency') or ('USD' if market=='US' else 'HKD' if market=='HK' else 'CNY')}


def identity(instrument):
    if instrument.get('builtin'): return 'BENCHMARK:' + instrument['code']
    return describe(instrument)['identity']


def market_label(instrument):
    return 'A股' if instrument['market'] in ('SH','SZ') else {'HK':'港股','US':'美股','FUND':'场外基金'}.get(instrument['market'],'其他')


def sort_key(instrument):
    rank={'SH':0,'SZ':0,'FUND':1,'HK':2,'US':3}.get(instrument['market'],4)
    code=instrument['code'].upper()
    return (rank,code.zfill(12) if code.isdigit() else code,instrument['id'])
