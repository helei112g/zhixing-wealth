"""Validate payload identity and safe, public provenance before publication/restore."""
import re
from urllib.parse import urlsplit
from ..common import DomainError,day,dec
from .metrics import clean_prices

MODULES={'price','total_return','valuations','memberships','stock_valuations','financials','historical_memberships','historical_financials'}
HOSTS={'www.csindex.com.cn','oss-ch.csindex.com.cn','hq.cnindex.com.cn','www.cnindex.com.cn','legulegu.com','push2.eastmoney.com','datacenter-web.eastmoney.com'}


def public_url(value):
    u=urlsplit(value)
    if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443):raise DomainError('研究证据地址不是受支持的公开HTTPS来源')


def validate(module,value):
    if module not in MODULES or not isinstance(value,dict):raise DomainError('研究模块或结构无效')
    effective=value.get('effective_date')
    if effective is not None:day(effective)
    for source in value.get('evidence',[]):
        if not isinstance(source,dict):raise DomainError('证据结构无效')
        if source.get('url'):public_url(source['url'])
        if source.get('sha256') and not re.fullmatch('[a-f0-9]{64}',source['sha256']):raise DomainError('来源摘要格式无效')
    if module in ('price','total_return'):
        if not effective or not value.get('rows'):raise DomainError('价格快照缺少有效行情')
        rows=clean_prices(value['rows'],effective)
        if len(rows)!=len(value['rows']) or rows[-1]['date']!=effective:raise DomainError('行情日期与快照截止不一致')
    if module in ('memberships','stock_valuations'):
        rows=value.get('rows',[])
        if len({r['code'] for r in rows})!=len(rows):raise DomainError('公司代码重复')
        for r in rows:
            if not re.fullmatch(r'\d{6}',r['code']):raise DomainError('公司代码格式无效')
            if r.get('date'):day(r['date'])
            for field in (('weight',) if module=='memberships' else ('pe','pb')):
                if r.get(field) is not None:dec(r[field],nonnegative=field=='weight')
        if module=='memberships' and (not rows or abs(sum(dec(r['weight']) for r in rows)-100)>dec('.5')):raise DomainError('成分权重不完整')
    if module=='financials':
        for r in value.get('rows',[]):
            day(r['period'])
            for f in ('revenue','profit','operating_cf'):
                if r.get(f) is not None:dec(r[f])
