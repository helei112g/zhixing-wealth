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
