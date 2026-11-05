"""Annual, contemporaneous membership baskets. Never substitute the current basket."""
from decimal import Decimal
from ..common import dec,ds


def totals(rows,baskets):
    result=[]
    for basket in baskets:
        members=basket['members'];metrics={}
        for metric in ('revenue','profit','operating_cf'):
            unknown=sum(not m.get('industry') for m in members) if metric=='operating_cf' else 0
            eligible={m['code'] for m in members if metric!='operating_cf' or m.get('industry') and not any(t in m['industry'] for t in ('银行','证券','保险','金融'))}
            valid={r['code']:r for r in rows if r['code'] in eligible and r['period']==basket['period'] and r.get(metric) is not None}
            coverage=Decimal(len(valid))/len(eligible) if eligible else None
            amount=sum((dec(r[metric]) for r in valid.values()),Decimal(0)) if valid else None
            metrics[metric]=dict(amount=ds(amount) if amount is not None else None,count=len(valid),denominator=len(eligible),count_coverage=ds(coverage) if coverage is not None else None,weight_coverage=None,
                excluded_count=len(members)-len(eligible),excluded_unknown_industry=unknown,missing_codes=sorted(eligible-set(valid)),growth=None,status='available' if not unknown and basket['status'] in ('official','checked','reconstructed') and coverage is not None and coverage>=Decimal('.8') else 'insufficient_coverage')
        result.append(dict(period=basket['period'],membership_status=basket['status'],membership_reason=basket.get('reason'),metrics=metrics))
    for r in result:
        for v in r['metrics'].values():v['coverage_status']=v['status']
    by_period={r['period']:r for r in result}
    for r in result:
        for metric,v in r['metrics'].items():
            previous=by_period.get(str(int(r['period'][:4])-1)+r['period'][4:])
            prior=previous['metrics'][metric] if previous else None
            if v['coverage_status']=='available' and prior and prior['coverage_status']=='available':
                if dec(prior['amount'])>0:v['growth']=ds(dec(v['amount'])/dec(prior['amount'])-1)
                else:v['status']='nonpositive_base'
            if prior and v['amount'] is not None and prior['amount'] is not None:v['change']=ds(dec(v['amount'])-dec(prior['amount']))
    return result[-10:]
