#!/usr/bin/env python3
"""Local HTTP research contract using a guarded synthetic test server; no credential reads."""
import os
import time
from ai_client import Client


def main():
    c=Client(os.environ['PORTFOLIO_TEST_URL'],environment='dev',expected_data_dir=os.environ['PORTFOLIO_TEST_DATA_DIR'])
    if not c.request('/api/health').get('synthetic_fixture'):
        raise RuntimeError('Research verification requires a synthetic development ledger')
    count=0
    def check(value,label):
        nonlocal count
        if not value:raise AssertionError(label)
        count+=1;print('PASS research AI',count,label,flush=True)
    cap=c.request('/api/capabilities');check(cap['research']['windows']==[0,3,5,10],'discover complete windows')
    check(cap['research']['local_credentials']['local_request_limit'] is None,'discover no local request cap')
    before=c.connect()['revision'];counts=c.request('/api/settings/lixinger')['counts']
    p=c.collection_preview('000905',['index_info','price','total_return','valuations','memberships'],'2026-10-01',
                           primary_valuation='dividend_yield')
    check(c.request('/api/settings/lixinger')['counts']==counts,'saved subscription preview uses no supplier calls')
    check(p['endpoint_budget'] and all(row['remaining'] is None for row in p['endpoint_budget']),'call estimates have no local quota')
    body=dict(code='000905',modules=p['modules'],history_start=p['history_start'],primary_valuation=p['primary_valuation'],
              enabled=True,expected_research_revision=p['research_revision'])
    saved=c.write('/api/research/collection',body,'PUT');check(c.request('/api/research/catalog')['subscriptions'][0]['subscription']['enabled'] and saved['job_id'] is None,'subscription read back without a second initial sync')
    try:c.write('/api/research/collection',body,'PUT')
    except RuntimeError as e:check('409' in str(e),'stale subscription rejected')
    else:raise AssertionError('stale subscription accepted')
    d=c.research_index(saved['id'],window=0);check(d['latest'] and d['valuation']['pe']['source']=='理杏仁','reads synthetic cached Lixinger sequence')
    listing=c.research_indices()
    row=next(item for item in listing['items'] if item['id']==saved['id'])
    check(listing['method']=='mcw' and row['valuation']['pe']==d['valuation']['pe'] and
          set(row['changes']['annual'])=={str(listing['calendar_year']-n) for n in (1,2,3)},
          'index list shares the same valuation and provides three completed years')
    index_list=cap['research']['index_list']
    filtered=c.research_indices(valuation_group='dividend_yield')
    check(index_list['valuation_position']['windows']==[5,10] and row['profile']['primary_valuation']=='dividend_yield'
          and row['valuation_position']['metric']=='dividend_yield' and filtered['items']==[row]
          and filtered['valuation_counts']['dividend_yield']==1, 'AI reads the reviewed category and both valuation positions')
    check(set(index_list['sort_by'])=={'annual_1','annual_2','annual_3','ytd','monthly','daily'} and
          index_list['sort_direction']==['asc','desc'] and index_list['sort_missing']=='last' and
          c.research_indices(sort_by='daily',sort_direction='asc')['items']==listing['items'],
          'AI discovers and requests the same return sort as the index list')
    activity=cap['market_waterlevel']['market_activity']
    market_sources=c.market_data_sources()
    daily = c.market_activity()
    check(set(activity['series']) == set(daily['series']) and
          activity['history'] == '/api/market-waterlevel/activity' and activity['frequency'] == 'daily' and
          set(activity['series']) <= {row['id'] for row in c.market_waterlevel(facts=True)['series']} and
          set(activity['groups']) <= {row['id'] for row in market_sources['items']},
          'market facts, daily history and source discovery expose both activity groups')
    check(market_sources['schedule']==cap['market_waterlevel']['schedule'] and
          market_sources['schedule']==dict(weekday=5, time='18:00', timezone='Asia/Shanghai', enabled=False),
          'AI sees the Friday market update schedule and current instance state')
    a=c.research_studio(saved['id'],method='mcw',window=3,metric='pe')
    check(a['analysis_id']==c.research_index(saved['id'])['analysis_id'],'UI and AI share analysis snapshot')
    check(a['research_revision']==d['research_revision'],'independent chart windows remain bound to the same research revision')
    check('backtest' not in a and 'overlaps' not in a and 'total_return_comparison' not in a,'retired return backtest and cross-index comparisons are absent')
    check(c.request('/api/settings/lixinger')['counts']==counts,'analysis reads are offline')
    for part in ('series','constituents','financials','sources'):
        check(c.research_part(saved['id'],part,analysis_id=d['analysis_id'],window=0)['analysis_id']==d['analysis_id'],'versioned '+part)
    from decimal import Decimal
    check(d['scenario_metrics']['pe'],'same-day mcw scenario capability is explicit')
    body=dict(analysis_id=d['analysis_id'],basis='price',method='mcw',metric='pe',target_multiple=d['valuation']['pe']['current'],change_ratio='0.1',reference_window=5,accept_fixed_basket_assumption=True)
    scenario=c.research_scenario(saved['id'],body)
    check(Decimal(scenario['point'])==Decimal(d['latest']['close'])*Decimal('1.1') and '同比例' in scenario['assumption'],'HTTP scenario uses reviewed proportional-change assumptions')
    try:c.research_scenario(saved['id'],dict(body,accept_fixed_basket_assumption=False))
    except RuntimeError:check(True,'scenario refuses missing assumption acceptance')
    else:raise AssertionError('scenario assumed user acceptance')
    check(c.request('/api/settings/lixinger')['counts']==counts,'scenario consumes no supplier requests')
    versions=c.request('/api/research/indices/'+saved['id']+'/versions')['items'];v=versions[-1]
    check(c.request('/api/research/indices/'+saved['id']+'/versions/'+v['id'])['sha256']==v['sha256'],'immutable older snapshot read')
    job=c.refresh_index(saved['id'],'synthetic-ai-refresh');check(c.refresh_index(saved['id'],'synthetic-ai-refresh')['job_id']==job['job_id'],'update receipt idempotence')
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        value=c.request('/api/data-updates/jobs/'+job['job_id'])
        if value['state'] not in ('pending','running'):break
        time.sleep(.05)
    check(value['state']=='complete','explicit subscribed refresh completes')
    check(len(value['resources'])==6 and all(r['target']==saved['id'] for r in value['resources']),'refresh limited to exact subscribed index modules')
    check(c.connect()['revision']==before,'research does not change ledger revision')
    try:c.write('/api/settings/lixinger/token',{'token':'never-sent'},'PUT')
    except ValueError:check(True,'AI client does not accept credential mutation')
    else:raise AssertionError('AI credential mutation accepted')

    check(cap['research']['collection']['validate_new_catalog_overseas']=='official_identity_and_price',
          'AI discovers official verification for catalog overseas indices')
    counts = c.request('/api/settings/lixinger')['counts']
    modules = ['price', 'total_return', 'memberships']
    p = c.collection_preview('H01136', [*modules, 'financials'], '2026-10-01')
    check(p['profile']['code']=='H11136' and p['verified_price_source']=='中证指数' and p['verified_price_date']
          and p['modules']==modules and p['unsupported_modules']==['financials']
          and not p['endpoint_budget'], 'official return-code alias previews only supported modules')
    check(all(row['code']!='H11136' for row in c.request('/api/research/catalog')['subscriptions']),
          'official preview leaves subscriptions unchanged')
    body = dict(code='H11136', modules=p['modules'], history_start=p['history_start'], enabled=True,
                expected_research_revision=p['research_revision'])
    saved = c.write('/api/research/collection', body, 'PUT')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = c.request('/api/data-updates/jobs/' + saved['job_id'])
        if job['state'] not in ('pending', 'running'):
            break
        time.sleep(.05)
    check(job['state']=='complete' and job['trigger']=='index_create' and
          [r['module'] for r in job['resources'] if r['full'] and r['target']==saved['id']]==modules,
          'official subscription completes its first full sync')
    d = c.research_index(saved['id'])
    tr = c.research_index(saved['id'], basis='total_return')
    check(d['series_code']=='H11136' and tr['series_code']=='H01136' and d['latest'] and tr['latest'] and
          {row['market'] for row in d['companies']}=={'HK', 'US'} and
          d['module_status']['valuations']['status']=='missing', 'official snapshots read back with distinct series and cross-market constituents')
    check(c.request('/api/settings/lixinger')['counts']==counts and c.connect()['revision']==before,
          'official collection leaves Lixinger counts and ledger revision unchanged')
    official_heads = {s['module']: s['snapshot_id'] for s in d['sources']}
    p = c.collection_preview('H11136', [*modules, 'valuations', 'historical_memberships'], None)
    check(p['modules']==[*modules, 'valuations'] and p['unsupported_modules']==['historical_memberships'] and
          p['endpoint_budget']==[dict(endpoint='cn/index/fundamental', calls=2, remaining=None)] and
          c.request('/api/settings/lixinger')['counts']==counts, 'existing mixed subscription preview plans only PE/PB and stays offline')
    body.update(modules=p['modules'], history_start=p['history_start'], expected_research_revision=p['research_revision'])
    saved = c.write('/api/research/collection', body, 'PUT')
    check(saved['job_id'] is None and 'valuations' in saved['subscription']['modules'],
          'editing adds an independently updatable valuation subscription')
    d = c.research_index(saved['id'])
    resource = next(r for r in d['collection']['resources'] if r['module']=='valuations')
    accepted = c.write('/api/data-updates/run',
                       dict(scopes=['valuations'], resources=[resource], request_id='synthetic-h11136-pe-pb'), 'POST')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = c.request('/api/data-updates/jobs/' + accepted['job_id'])
        if job['state'] not in ('pending', 'running'):
            break
        time.sleep(.05)
    check(job['state']=='complete' and job['resources']==[resource],
          'exact valuation update never queues A-share prerequisites or official modules')
    d = c.research_index(saved['id'])
    check({k for k,v in d['valuation'].items() if v.get('current') is not None}=={'pe','pb'} and
          d['valuation']['pe']['source']==d['valuation']['pb']['source']=='理杏仁' and
          not any(d['scenario_metrics'].values()) and '分母映射尚未核验' in d['scenario_unavailable_reason'],
          'valuation reads show only Lixinger PE/PB and explain the disabled cross-source scenario')
    try:
        c.research_scenario(saved['id'], dict(analysis_id=d['analysis_id'], basis='price', method='mcw',
                            metric='pe', target_multiple=d['valuation']['pe']['current'], change_ratio='0',
                            accept_fixed_basket_assumption=True))
    except RuntimeError as exc:
        check('分母映射尚未核验' in str(exc), 'scenario API returns the same cross-source restriction')
    else:
        raise AssertionError('cross-source scenario was accepted')
    check({s['module']:s['snapshot_id'] for s in d['sources'] if s['module'] in official_heads}==official_heads and
          c.request('/api/settings/lixinger')['counts']==dict(counts, **{'cn/index/fundamental':counts.get('cn/index/fundamental',0)+2}),
          'valuation sync preserves official heads and only calls the fundamental endpoint')

if __name__=='__main__':main()
