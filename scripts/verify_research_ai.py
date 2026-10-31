#!/usr/bin/env python3
"""Real HTTP AI-client contract on the explicitly marked synthetic research server."""
import json
import os
import time
from pathlib import Path
from ai_client import Client


def main():
    c=Client(os.environ['PORTFOLIO_TEST_URL'],environment='test',expected_data_dir=os.environ['PORTFOLIO_TEST_DATA_DIR']);checks=[]
    def check(v,label):
        if not v:raise AssertionError(label)
        checks.append(label);print('PASS research AI',len(checks),label,flush=True)
    cap=c.request('/api/capabilities');check(cap['research']['windows']==[3,5,10],'capability discovery')
    before=c.connect()['revision'];preview=c.resolve_index('H00300');check(c.connect()['revision']==before,'resolve does not change ledger')
    body=dict(resolve_token=preview['resolve_token'],expected_research_revision=preview['research_revision'],request_id='synthetic-ai-add')
    saved=c.write('/api/research/indices',body,'POST');check(c.write('/api/research/indices',body,'POST')==saved,'replayed add returns original result')
    end=time.monotonic()+20
    while time.monotonic()<end:
        jobs=c.request('/api/data-updates/jobs')['jobs'];job=next(j for j in jobs if j['id']==saved['job_id'])
        if job['state'] not in ('pending','running'):break
        time.sleep(.05)
    check(job['state']=='complete','initial update task completes over HTTP')
    d=c.research_index(saved['id']);check(d['latest'] is not None and len(d['drawdown'])==3,'read back actual created identity and windows')
    check(d['top10_weight']=='95' and d['companies'][0]['weight_rank']==1,'full-basket top10 and deterministic ranks')
    check('2025-12-31' in d['companies'][0]['financials'] and len(d['financials']['current_years'])==10,'company financial periods and ten annual aggregates')
    check(d['daily_series'][-1]['ma850'] is not None and d['changes']['ytd']['ratio']=='0.2','full-history moving average and calendar YTD')
    scenario=c.research_scenario(saved['id'],dict(analysis_id=d['analysis_id'],metric='pe',target_multiple='10',change_ratio='0.25',reference_window=3,accept_fixed_basket_assumption=True))
    check(scenario['point']=='150' and len(scenario['curve'])==21 and 'p10' in scenario['reference_points'],'scenario curve and reference band use the same backend response')
    check(cap['research']['moving_averages']==[51,120,250,850] and cap['research']['valuation_methods']==['overall','median','arithmetic'],'enhanced research is machine discoverable')
    windows=[c.research_index(saved['id'],window=w) for w in (3,5,10)]
    check([v['window'] for v in windows]==[3,5,10] and len({v['analysis_id'] for v in windows})==1 and len({len(v['daily_series']) for v in windows})==3,'all windows return distinct series under one immutable analysis version')
    variants=[c.research_index(saved['id'],basis=b,method=m) for b in ('price','total_return') for m in ('overall','median','arithmetic')]
    check(all(v['basis']==b and v['method']==m for v,(b,m) in zip(variants,[(b,m) for b in ('price','total_return') for m in ('overall','median','arithmetic')])),'both bases and all valuation methods work through the standard client')
    parts=[c.research_part(saved['id'],part,analysis_id=d['analysis_id']) for part in ('series','constituents','financials','sources')]
    check(all(v['analysis_id']==d['analysis_id'] for v in parts) and parts[1]['total']==12 and parts[2]['total']>0 and len(parts[3]['items'])==8,'all four projections preserve versioned series companies financials and sources')
    pages=[c.research_part(saved['id'],'constituents',analysis_id=d['analysis_id'],page=n,page_size=5) for n in (1,2,3)]
    check([r['weight_rank'] for v in pages for r in v['items']]==list(range(1,13)),'company pagination preserves the full weight ranking')
    for metric in ('pe','pb'):
        for ref in (3,5,10):
            v=c.research_scenario(saved['id'],dict(analysis_id=d['analysis_id'],metric=metric,target_multiple=d['valuation'][metric]['current'],change_ratio='0',reference_window=ref,accept_fixed_basket_assumption=True))
            assert v['point']==d['latest']['close'] and v['reference_window']==ref and 'p90' in v['reference_points']
    check(True,'PE PB scenarios support every reference window and reset to current close')
    settings=c.update_settings();payload=dict(policies=settings['policies'],expected_settings_revision=settings['settings_revision']);c.write('/api/settings/data-updates',payload,'PUT')
    try:c.write('/api/settings/data-updates',payload,'PUT')
    except RuntimeError as e:check('409' in str(e),'stale settings CAS rejected')
    else:raise AssertionError('stale settings accepted')
    resource=dict(scope='index_prices',target=saved['id'],module='total_return');task=dict(scopes=['index_prices'],resources=[resource],request_id='synthetic-ai-exact')
    queued=c.write('/api/data-updates/run',task,'POST');check(c.write('/api/data-updates/run',task,'POST')['job_id']==queued['job_id'],'update receipt remains idempotent')
    check(c.connect()['revision']==before,'research and update metadata do not invalidate ledger previews')
    try:c.request('/api/research/indices/'+saved['id']+'/constituents?analysis_id=old')
    except RuntimeError as e:check('409' in str(e),'cross-version page rejected')
    else:raise AssertionError('stale analysis page accepted')
    check(all((Path(__file__).resolve().parents[1]/'.agents/skills'/name/'SKILL.md').is_file() for name in ('zhixing-research','zhixing-portfolios')),'project skill paths exist')
    Path(os.environ['PORTFOLIO_TEST_OUTPUT'],'research-ai-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
