#!/usr/bin/env python3
"""Exercise project skill workflows over real HTTP on a marked synthetic server."""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from ai_client import Client


def main():
    base=os.environ['PORTFOLIO_TEST_URL']
    expected=Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve()
    output=Path(os.environ['PORTFOLIO_TEST_OUTPUT']).resolve()
    client=Client(base, environment='test', expected_data_dir=expected)
    health=client.request('/api/health')
    if health['environment']!='test' or Path(health['data_directory']).resolve()!=expected:
        raise RuntimeError('AI verification requires the exact synthetic test ledger')
    checks=[]
    def check(value,label):
        if not value: raise AssertionError(label)
        checks.append(label); print('PASS AI',len(checks),label,flush=True)
    def revision(): return client.request('/api/family/catalog')['revision']
    def command(operation,payload,record_id=None):
        body=dict(operation=operation,payload=payload,expected_revision=revision())
        if record_id: body['record_id']=record_id
        before=client.request('/api/family/catalog')
        preview=client.preview(body,'/api/family/preview')
        check(preview['revision']==body['expected_revision'] and client.request('/api/family/catalog')==before,operation+' preview is read-only')
        saved=client.write('/api/family/commands',body,'POST')
        check(saved['revision']==body['expected_revision']+1,operation+' uses reviewed revision')
        return saved['result'],body
    cap=client.request('/api/capabilities')
    schema=client.request(cap['family']['command_schema'])
    check(set(schema['operations'])=={'item','valuation','links','unlink','income'},'recommended asset commands discoverable')
    check('/api/family/command-schema' in client.request(cap['openapi'])['paths'],'OpenAPI discovers asset contract')
    check('csrf_token' not in client.connect(),'client does not expose bootstrap credential')
    asset,old=command('item',dict(category='cash',currency='CNY',name='AI合成快照验收',effective_on='2024-01-01',household_amount='123456.78'))
    catalog=client.request('/api/family/catalog')
    check(any(r['id']==asset['id'] for r in catalog['items']),'saved asset ID is readable')
    try: client.write('/api/family/commands',old,'POST')
    except RuntimeError as error: check('HTTP 409' in str(error),'stale revision rejected')
    else: raise AssertionError('stale write accepted')
    check(client.request('/api/family/catalog')==catalog,'stale write leaves ledger unchanged')
    command('valuation',dict(item_id=asset['id'],effective_on='2024-03-01',household_amount='200000'))
    command('valuation',dict(item_id=asset['id'],effective_on='2024-02-01',household_amount='150000'))
    values=[r for r in client.request('/api/family/catalog')['values'] if r['item_id']==asset['id']]
    check(max(values,key=lambda r:r['effective_on'])['amount']=='200000','backfill preserves later balance')
    check(len(values)==3,'all balance snapshots retained')
    before=client.request('/api/family/overview')['total_assets']
    income,_=command('income',dict(kind='custom',name='AI合成收入',month='2024-02',date='2024-02-01',currency='CNY',amount='100'))
    command('income',dict(kind='custom',name='AI合成收入',month='2024-02',date='2024-02-01',currency='CNY',amount='120'),income['id'])
    records=client.request('/api/family/income?year=2024')['records']
    check(len([r for r in records if r['id']==income['id']])==1 and next(r for r in records if r['id']==income['id'])['amount']=='120','income correction replaces current amount, not duplicate record')
    check(client.request('/api/family/overview')['total_assets']==before,'income does not double-count cash')
    p=client.write('/api/portfolios',dict(name='AI合成组合验收',planned_amount='10000',start_date='2024-01-01',expected_revision=revision()),'POST')
    q=client.write('/api/portfolios',dict(name='AI合成隔离组合',planned_amount='10000',start_date='2024-01-01',expected_revision=revision()),'POST')
    event=dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument={'market':'CN','code':'510300','name':'AI合成标的'},quantity='10',gross_amount='100',fx_rate='1',expected_revision=revision())
    client.preview(event)
    check(revision()==event['expected_revision'],'trade preview is read-only')
    saved=client.write('/api/events',event,'POST')
    transactions='/api/portfolios/'+p['id']+'/transactions?page=1&page_size=20'
    rows=client.request(transactions)
    check(any(r['event']['id']==saved['id'] for r in rows['items']),'trade ID and portfolio read back')
    boot=client.connect();original_portfolios=boot['portfolios']
    links,_=command('links',{'portfolio_ids':[p['id'],q['id']]})
    check(all(any(x['id']==link['id'] for x in client.request('/api/family/catalog')['links']) for link in links),'multiple associations read back')
    command('unlink',{'link_ids':[r['id'] for r in links]})
    check(client.connect()['portfolios']==original_portfolios and client.request(transactions)['items']==rows['items'],'associations never write portfolios or trades')
    path='/api/portfolios/'+p['id']+'/instruments/'+saved['instrument_id']+'/notes'
    note=client.write(path,dict(title='AI合成笔记',body='## 合成观察\n\n保留 **原文**。',expected_revision=client.request(path)['revision']),'POST')
    check(client.request(path+'/'+note['id'])['body']=='## 合成观察\n\n保留 **原文**。','note exact text and scope read back')
    updated=client.write(path+'/'+note['id'],dict(title='AI合成笔记',body='## 合成修订\n\n1. 只修改本笔记。',expected_revision=revision()),'PUT')
    check(updated['id']==note['id'] and client.request(path+'/'+note['id'])['body']==updated['body'],'note edit read back')
    client.write(path+'/'+note['id'],{'expected_revision':revision()},'DELETE')
    check(client.request(path)['total']==0,'note deletion scoped')
    client.write(path+'/'+note['id']+'/restore',{'expected_revision':revision()},'POST')
    check(client.request(path)['total']==1,'note restore read back')
    wrong='/api/portfolios/'+q['id']+'/instruments/'+saved['instrument_id']+'/notes/'+note['id']
    try: client.request(wrong)
    except RuntimeError as error: check('HTTP 422' in str(error),'cross-portfolio note access refused')
    else: raise AssertionError('note escaped portfolio scope')
    check(client.request(transactions)['items']==rows['items'],'notes never modify transactions')
    debt,_=command('item',dict(category='debt',currency='CNY',name='AI合成还款规则',effective_on=str(date.today()),household_amount='12000',payment_plan={'mode':'annuity','first_due':str(date.today()),'periods':12,'annual_rate':'0'}))
    check(debt['payment_preview']['periods']==12 and debt['payment_preview']['total']=='12000','debt rule calculates installments')
    policy,_=command('item',dict(category='insurance',currency='CNY',name='AI合成保费规则',effective_on=str(date.today()),household_amount='0',insured='合成被保人',coverage_amount='1000000',payment_plan={'mode':'annual','first_due':str(date.today()),'periods':3,'amount':'100'}))
    check(policy['payment_preview']['periods']==3 and policy['payment_preview']['total']=='300','policy rule calculates annual premiums')
    check('items' in client.request('/api/reminders'),'all reminders use shared system endpoint')
    # Exercise CLI parsing and explicit confirmation, not only the imported client.
    request_file=output/'synthetic-ai-request.json'
    request_file.write_text(json.dumps(dict(operation='valuation',payload=dict(item_id=asset['id'],effective_on=str(date.today()),household_amount='210000'),expected_revision=revision())))
    cli=[sys.executable,str(Path(__file__).with_name('ai_client.py')),'--environment','test','--base-url',base,
         '--expected-data-dir',str(expected)]
    try:
        target=subprocess.run(cli+['target'],capture_output=True,text=True,check=True)
        check(json.loads(target.stdout)['environment']=='test','CLI explicitly targets synthetic environment even inside a release')
        wrong=subprocess.run(cli+['--expected-data-dir',str(expected/'wrong-ledger'),'read','/api/family/catalog'],capture_output=True,text=True)
        check(wrong.returncode!=0 and 'ledger directory' in wrong.stderr,'CLI refuses a different ledger before reading records')
        preview=subprocess.run(cli+['preview',str(request_file),'--path','/api/family/preview'],capture_output=True,text=True,check=True)
        check(json.loads(preview.stdout)['preview'],'CLI family preview runs')
        denied=subprocess.run(cli+['write','POST','/api/family/commands',str(request_file)],capture_output=True,text=True)
        check(denied.returncode!=0,'CLI requires explicit confirmation flag')
        done=subprocess.run(cli+['write','POST','/api/family/commands',str(request_file),'--confirm-write'],capture_output=True,text=True,check=True)
        check(json.loads(done.stdout)['result']['amount']=='210000','CLI approved synthetic write read back')
    finally: request_file.unlink(missing_ok=True)
    # NAV is a synthetic provider fixture; all business writes still go through HTTP.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.environment import claim_environment
    from backend.service import Service
    from backend.store import Store
    claim_environment(expected, 'test')
    fund=dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument={'market':'FUND','code':'000003','name':'AI合成再投基金'},quantity='100',gross_amount='100',fx_rate='1')
    preview=client.preview(fund)
    fund_saved=client.write('/api/events',dict(fund,expected_revision=preview['revision']),'POST')
    Service(Store(expected)).cache_prices(fund_saved['instrument_id'],[{'date':'2024-06-03','price':'1.25','kind':'raw','source':'AI合成单位净值'}])
    before=client.request(transactions);rev=revision()
    estimate=client.reinvestment_estimate(p['id'],fund_saved['instrument_id'],'2024-06-03','12.5')
    check(estimate['quantity']=='10' and estimate['reference']['date']=='2024-06-03','AI uses exact-day shared reinvestment estimate')
    check(revision()==rev and client.request(transactions)==before,'reinvestment estimate has no ledger writes')
    missing=client.reinvestment_estimate(p['id'],fund_saved['instrument_id'],'2024-06-04','12.5')
    check(missing['status']=='missing_nav' and missing['quantity'] is None,'AI never borrows neighboring NAV')
    check(cap['reinvestment_estimate']['endpoint'] in client.request(cap['openapi'])['paths'],'AI discovers reinvestment query contract')
    reinvest=dict(portfolio_id=p['id'],instrument_id=fund_saved['instrument_id'],kind='REINVEST',date='2024-06-03',pay_date='2024-06-03',reinvest_date='2024-06-03',gross_amount='12.5',tax='0',reinvest_gross_amount='12.5',reinvest_quantity=estimate['quantity'],fx_rate='1')
    preview=client.preview(reinvest)
    check(revision()==rev and preview['event']['reinvest_quantity']=='10','AI previews estimated shares without mutation')
    saved=client.write('/api/events',dict(reinvest,expected_revision=preview['revision']),'POST')
    detail=client.request('/api/portfolios/'+p['id']+'/transactions/'+saved['id'])
    check(detail['event']['reinvest_quantity']=='10' and detail['event']['reinvest_price']=='1.25','AI explicit reinvestment commit reads back exact quantity')
    try: client.write('/api/events',dict(reinvest,expected_revision=preview['revision']),'POST')
    except RuntimeError as error: check('HTTP 409' in str(error),'AI stale reinvestment write is rejected')
    else: raise AssertionError('stale reinvestment write accepted')
    (output/'ai-passed.json').write_text(json.dumps({'checks':len(checks),'passed':checks},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
