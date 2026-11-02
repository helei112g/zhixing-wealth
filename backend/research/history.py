"""Annual, contemporaneous membership baskets. Never substitute the current basket."""
import hashlib
from io import BytesIO
from decimal import Decimal
from ..common import dec,ds,now
from ..market import ProviderError


def memberships(profile,asof):
    years=range(int(asof[:4])-11,int(asof[:4]));periods=[f'{y}-12-31' for y in years]
    result=[dict(period=p,status='unavailable',members=[],reason='尚无核验的年度有效名单与临调证据') for p in periods]
    evidence=[]
    if profile['provider']=='cni' and profile['code']=='399006':
        from .providers import excel_rows,cell_code,cell_date
        import requests
        url='https://www.cnindex.com.cn/sample-detail/download-adjustment?indexcode=399006'
        r=requests.get(url,timeout=20);r.raise_for_status()
        evidence=[dict(url=url,fetched_at=now(),sha256=hashlib.sha256(r.content).hexdigest())]
        if len(r.content)<1000 and profile['code'] in r.text and '文件不存在' in r.text:
            for b in result:b['reason']='官方历史调样文件未提供，年度有效名单待核验'
            return dict(baskets=result,evidence=evidence,effective_date=asof,source_cutoff=asof,status='unavailable')
        if not r.content.startswith((b'PK',b'\xd0\xcf')):raise ProviderError('官方历史名单未返回Excel，旧版本保留')
        data=excel_rows(r.content);headers=data[0];frame=[dict(zip(headers,row)) for row in data[1:]]
        needed={'开始日期','结束日期','样本代码','样本简称','调整类型'}
        if not needed.issubset(headers):raise ProviderError('国证历史样本字段改变，旧版本保留')
        evidence=[dict(url=url,fetched_at=now(),sha256=hashlib.sha256(r.content).hexdigest())]
        for b in result:
            eligible=[v for v in frame if cell_date(v['开始日期'])<=b['period']<=cell_date(v['结束日期'])]
            if not eligible:continue
            effective=max(cell_date(v['开始日期']) for v in eligible);chosen=[v for v in eligible if cell_date(v['开始日期'])==effective and v['调整类型'] in ('OLD','+')]
            members=[dict(code=cell_code(v['样本代码']),name=v['样本简称'],market='SZ',industry=v.get('所属行业') if isinstance(v.get('所属行业'),str) else None,weight=None) for v in chosen]
            if len(members)!=100 or len({m['code'] for m in members})!=100:
                b.update(status='conflict',reason='区间名单数量不完整或重复');continue
            b.update(status='official',members=members,effective_date=effective,reason=None)
    return dict(baskets=result,evidence=evidence,effective_date=asof,source_cutoff=asof)


def totals(rows,baskets):
    result=[]
    for basket in baskets:
        members=basket['members'];metrics={}
        for metric in ('revenue','profit','operating_cf'):
            eligible={m['code'] for m in members if metric!='operating_cf' or m.get('industry') and not any(t in m['industry'] for t in ('银行','证券','保险','金融'))}
            valid={r['code']:r for r in rows if r['code'] in eligible and r['period']==basket['period'] and r.get(metric) is not None}
            coverage=Decimal(len(valid))/len(eligible) if eligible else None
            amount=sum((dec(r[metric]) for r in valid.values()),Decimal(0)) if valid else None
            metrics[metric]=dict(amount=ds(amount) if amount is not None else None,count=len(valid),denominator=len(eligible),count_coverage=ds(coverage) if coverage is not None else None,weight_coverage=None,
                excluded_count=len(members)-len(eligible),missing_codes=sorted(eligible-set(valid)),growth=None,status='available' if basket['status'] in ('official','checked','reconstructed') and coverage is not None and coverage>=Decimal('.8') else 'insufficient_coverage')
        result.append(dict(period=basket['period'],membership_status=basket['status'],membership_reason=basket.get('reason'),metrics=metrics))
    for r in result:
        for v in r['metrics'].values():v['coverage_status']=v['status']
    for n,r in enumerate(result):
        for metric,v in r['metrics'].items():
            prior=result[n-1]['metrics'][metric] if n else None
            if v['coverage_status']=='available' and prior and prior['coverage_status']=='available':
                if dec(prior['amount'])>0:v['growth']=ds(dec(v['amount'])/dec(prior['amount'])-1)
                else:v['status']='nonpositive_base'
            if prior and v['amount'] is not None and prior['amount'] is not None:v['change']=ds(dec(v['amount'])-dec(prior['amount']))
    return result[-10:]
