#!/usr/bin/env python3
"""Local HTTP research contract using a guarded synthetic test server; no credential reads."""
import os
import time
from ai_client import Client


def main():
    c=Client(os.environ['PORTFOLIO_TEST_URL'],environment='test',expected_data_dir=os.environ['PORTFOLIO_TEST_DATA_DIR'])
    count=0
    def check(value,label):
        nonlocal count
        if not value:raise AssertionError(label)
        count+=1;print('PASS research AI',count,label,flush=True)
    cap=c.request('/api/capabilities');check(cap['research']['windows']==[0,3,5,10],'discover complete windows')
    before=c.connect()['revision'];counts=c.request('/api/settings/lixinger')['counts']
    p=c.collection_preview('000905',['index_info','price','total_return','valuations','memberships'],'2026-10-01')
    check(c.request('/api/settings/lixinger')['counts']==counts,'preview uses no supplier calls')
    body=dict(code='000905',modules=p['modules'],history_start=p['history_start'],enabled=True,expected_research_revision=p['research_revision'])
    saved=c.write('/api/research/collection',body,'PUT');check(c.request('/api/research/catalog')['subscriptions'][0]['subscription']['enabled'],'subscription read back')
    try:c.write('/api/research/collection',body,'PUT')
    except RuntimeError as e:check('409' in str(e),'stale subscription rejected')
    else:raise AssertionError('stale subscription accepted')
    d=c.research_index(saved['id'],window=0);check(d['latest'] and d['valuation']['pe']['source']=='理杏仁','reads synthetic cached Lixinger sequence')
    a=c.research_studio(saved['id'],method='mcw',window=3,metric='pe',holding=1)
    check(a['analysis_id']==c.research_index(saved['id'])['analysis_id'],'UI and AI share analysis snapshot')
    check(c.request('/api/settings/lixinger')['counts']==counts,'analysis reads are offline')
    for part in ('series','constituents','financials','sources'):
        check(c.research_part(saved['id'],part,analysis_id=d['analysis_id'],window=0)['analysis_id']==d['analysis_id'],'versioned '+part)
    versions=c.request('/api/research/indices/'+saved['id']+'/versions')['items'];v=versions[-1]
    check(c.request('/api/research/indices/'+saved['id']+'/versions/'+v['id'])['sha256']==v['sha256'],'immutable older snapshot read')
    job=c.refresh_index(saved['id'],'synthetic-ai-refresh');check(c.refresh_index(saved['id'],'synthetic-ai-refresh')['job_id']==job['job_id'],'update receipt idempotence')
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        value=c.request('/api/data-updates/jobs/'+job['job_id'])
        if value['state'] not in ('pending','running'):break
        time.sleep(.05)
    check(value['state']=='complete','explicit subscribed refresh completes')
    check(len(value['resources'])==5 and all(r['target']==saved['id'] for r in value['resources']),'refresh limited to exact subscribed index modules')
    check(c.connect()['revision']==before,'research does not change ledger revision')
    try:c.write('/api/settings/lixinger/token',{'token':'never-sent'},'PUT')
    except ValueError:check(True,'AI client does not accept credential mutation')
    else:raise AssertionError('AI credential mutation accepted')

if __name__=='__main__':main()
