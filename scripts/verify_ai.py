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
    income_response=client.request('/api/family/income?year=2024')
    records=income_response['records']
    check(income_response['summary']['total']=='120' and income_response['summary']['groups']['custom']['share']=='1', 'income cumulative and share use latest record through HTTP')
    history=client.request('/api/family/history?grain=month&comparison=previous&start=2024-02-01&end=2024-02-29')
    check(history['rows'][0]['comparison_date']=='2024-01-31', 'history previous compares prior month end through HTTP')
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
    dashboard='/api/portfolios/'+p['id']+'/dashboard'
    full=client.request(dashboard);summary=client.request(dashboard+'?view=summary')
    check(summary['capital_summary']==full['capital_summary'] and summary['holdings']==full['holdings'], 'AI summary view shares canonical financial values')
    check('curve' not in summary and 'calendar' not in summary and 'calendar' in full, 'AI summary omits historical projections and full stays compatible')
    investment=client.investment_summary(p['id'])
    check(investment==summary['capital_summary'] and investment['cost']=='100','AI cumulative input reads canonical cumulative buy cost')
    breakdown=investment['profit_breakdown']
    check(breakdown['cash_dividends_net']=='0' and breakdown['reinvested_dividends_net']=='0','AI discovers separate cash and reinvested dividend income without invented values')
    check(client.request('/api/capabilities')['investment_summary']['cumulative_input']=='cost','AI discovers cumulative input and dividend component semantics')
    income_portfolio=client.write('/api/portfolios',dict(name='AI合成分红拆分',planned_amount='10000',start_date='2024-01-01',expected_revision=revision()),'POST')
    acquisition=client.write('/api/events',dict(portfolio_id=income_portfolio['id'],kind='BUY',date='2024-01-02',
        instrument={'market':'CN','code':'600323','name':'AI合成分红拆分标的'},quantity='10',gross_amount='100',fx_rate='1',expected_revision=revision()),'POST')
    split_events=[]
    for item in [dict(kind='DIVIDEND',date='2024-02-01',net_amount='9',tax='0',pay_date='2024-02-03'),
                 dict(kind='REINVEST',date='2024-03-01',net_amount='10',tax='0',pay_date='2024-03-03',reinvest_date='2024-03-03',reinvest_quantity='1',reinvest_gross_amount='10')]:
        payload=dict(item,portfolio_id=income_portfolio['id'],instrument_id=acquisition['instrument_id'],fx_rate='1',expected_revision=revision())
        client.preview(payload);split_events.append(client.write('/api/events',payload,'POST'))
    for kind,parent,amount,at in [('TAX',split_events[0],'2','2024-04-01'),('FEE',split_events[1],'3','2024-04-02')]:
        payload=dict(portfolio_id=income_portfolio['id'],instrument_id=acquisition['instrument_id'],kind=kind,date=at,
                     related_event_id=parent['id'],amount=amount,fx_rate='1',expected_revision=revision())
        client.preview(payload);client.write('/api/events',payload,'POST')
    before_read=revision();investment=client.investment_summary(income_portfolio['id']);parts=investment['profit_breakdown']
    check(investment['cost']=='110' and investment['invested_amount']=='96','AI cumulative input includes reinvestment without replacing net input')
    check(parts['cash_dividends_net']=='7' and parts['reinvested_dividends_net']=='7' and parts['dividends_net']=='14','AI reads exact separate dividend income with each related cost deducted once')
    check(revision()==before_read,'AI metric read has no financial or historical writes')
    labels=client.holding_labels(p['id'],saved['instrument_id'])
    check(labels['labels']['resolved_scope']=='待确认','ETF exposure is not inferred from listing venue')
    label_path='/api/portfolios/'+p['id']+'/instruments/'+saved['instrument_id']+'/labels'
    label_payload=dict(scope='海外债券',direction='国债',detail='合成债券标签',expected_revision=labels['revision'])
    client.write(label_path,label_payload,'PUT')
    check(client.holding_labels(p['id'],saved['instrument_id'])['labels']['path']==['海外债券','国债','合成债券标签'],'label original revision write and scoped read-back through HTTP')
    check(client.request(transactions)['items']==rows['items'],'holding labels do not rewrite historical trades')
    report=client.holding_distribution(p['id'])
    check(report['nodes'][0]['name']=='海外债券' and report['nodes'][0]['weight']=='1','hierarchical distribution uses one path and the holdings denominator')
    try: client.write(label_path,label_payload,'PUT')
    except RuntimeError as error: check('HTTP 409' in str(error),'stale label revision rejected through HTTP')
    else: raise AssertionError('stale label write succeeded')
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
    before = client.request(transactions)
    deletion = client.deletion_preview(saved['id'])
    check(client.request(transactions)==before and deletion['event_ids']==[saved['id']], 'deletion preview is read-only and scoped')
    removed = client.write('/api/events/'+saved['id'],dict(reason='合成AI删除', cascade=True,
        event_ids=deletion['event_ids'], expected_revision=deletion['revision']),'DELETE')
    check(removed['deleted_event_ids']==deletion['event_ids'], 'AI submits reviewed deletion IDs and revision')
    check(client.request('/api/portfolios/'+p['id']+'/transactions/'+saved['id'])['cancelled'], 'AI deletion audit read back')
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
    check(cap['corporate_actions']['confirmation_kind']=='SPLIT' and not cap['corporate_actions']['automatic_ledger_write'],'AI discovers explicit share-action confirmation')
    check('shareholder classes' in cap['corporate_actions']['cash_only_plans'] and 'retain job history' in cap['corporate_actions']['sync_recovery'],'AI discovers cash-only classification and reminder recovery policy')
    stock=client.write('/api/events',dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument={'market':'CN','code':'600000','name':'AI合成送转股'},quantity='100',gross_amount='3000',fx_rate='1',expected_revision=revision()),'POST')
    fixture="from pathlib import Path;import sys;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;d=Path(sys.argv[1]).resolve();claim_environment(d,'test');s=Service(Store(d));s.cache_prices(sys.argv[2],[{'date':'2024-01-03','price':'10','source':'synthetic'}]);s.cache_actions(sys.argv[2],[{'kind':'split_notice','date':'2024-01-03','factor':'3','source':'synthetic'}])"
    subprocess.run([sys.executable,'-c',fixture,str(expected),stock['instrument_id']],cwd=Path(__file__).resolve().parents[1],check=True)
    endpoint='/api/portfolios/'+p['id']+'/instruments/'+stock['instrument_id']
    todo=client.request(endpoint)['todos'][0]
    split=dict(todo['prefill'],portfolio_id=p['id'],fx_rate='1',expected_revision=revision())
    preview=client.preview(split)
    check(revision()==split['expected_revision'] and preview['event']['factor']=='3','AI share-action preview preserves ledger')
    saved=client.write('/api/events',dict(split,expected_revision=preview['revision']),'POST')
    corrected=client.request(endpoint)
    check(corrected['holding']['quantity']=='300' and corrected['holding']['cost_including_fees_native']=='3000' and not corrected['todos'],'AI confirms shares and reads back preserved total cost')
    try:client.write('/api/events',dict(split,expected_revision=preview['revision']),'POST')
    except RuntimeError as error:check('HTTP 409' in str(error),'AI stale share-action confirmation cannot double shares')
    else:raise AssertionError('stale share-action write accepted')
    split_id=saved['id']
    split_saved=saved
    changed=client.write('/api/events/'+split_id,dict(split_saved,date='2024-01-02',fx_source_date='2024-01-02',change_reason='AI合成误填登记日',expected_revision=revision()),'PUT')
    review=client.request(endpoint)['todos'][0]
    check(review['type']=='verify_split' and review['event']['id']==split_id and changed['source_split_id']==split_saved['source_split_id'],'AI date correction preserves announcement identity and reviews original split')
    correction=dict(review['event'],date=review['notice']['date'],change_reason='AI合成核对除权日')
    preview=client.preview(correction)
    client.write('/api/events/'+split_id,dict(correction,expected_revision=preview['revision']),'PUT')
    check(not client.request(endpoint)['todos'],'AI source-date correction clears reminder under original event ID')
    original=client.request('/api/portfolios/'+p['id']+'/transactions/'+stock['id'])['event']
    backfill=client.write('/api/events',dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument_id=stock['instrument_id'],quantity='60',gross_amount='600',fx_rate='1',expected_revision=revision()),'POST')
    sale=dict(portfolio_id=p['id'],kind='SELL',date='2024-01-02',instrument_id=stock['instrument_id'],sell_lots=[dict(buy_event_id=stock['id'],quantity='100')],gross_amount='3200',fx_rate='1')
    before=revision();preview=client.preview(sale)
    change=preview['split_changes'][0]
    check(revision()==before and change['before']['quantity_after']=='480' and change['after']['quantity_after']=='180','AI backfilled sale previews split remainder without writing')
    check([r['buy_event_id'] for r in change['after']['lots']]==[backfill['id']],'AI split excludes sold batch and includes backfilled remaining batch')
    sale_saved=client.write('/api/events',dict(sale,expected_revision=preview['revision']),'POST')
    split_path='/api/portfolios/'+p['id']+'/transactions/'+split_id
    detail=client.request(split_path)
    check(detail['facts']['split']==change['after'] and detail['event']['factor']=='3','AI split effect matches preview under original event ID')
    check(client.request('/api/portfolios/'+p['id']+'/transactions/'+stock['id'])['event']==original,'AI backfill preserves original purchase fields')
    check(not client.request(endpoint)['todos'],'AI backfill does not reopen confirmed split reminder')
    try:client.write('/api/events',dict(split,allow_duplicate=True,expected_revision=revision()),'POST')
    except RuntimeError as error:check('HTTP 422' in str(error),'AI fresh revision still cannot confirm duplicate split')
    else:raise AssertionError('duplicate split accepted')
    client.write('/api/events/'+sale_saved['id'],dict(reason='合成取消补录',expected_revision=revision()),'DELETE')
    check(client.request(split_path)['facts']['split']['quantity_after']=='480','AI cancellation replays split without rewriting confirmation')
    check(cap['dividend_review']['mode']=='keep_recorded' and not cap['dividend_review']['event_write'],'AI discovers review without event writes')
    check('/api/dividends/{notice_id}/review' in client.request(cap['openapi'])['paths'],'AI discovers dividend review contract')
    holding=client.write('/api/events',dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument={'market':'HK','code':'02800','name':'AI合成分红核对'},quantity='100',gross_amount='1000',fx_rate='.9',expected_revision=revision()),'POST')
    dividend_source=dict(code='02800',source_ref='synthetic-review',record_date=None,ex_date='2024-06-03',pay_date=None,per_share='1',currency='HKD',source='Yahoo现金分派参考',split_restore_factor='1')
    fixture="from pathlib import Path;import sys,json;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;d=Path(sys.argv[1]).resolve();claim_environment(d,'test');Service(Store(d)).cache_dividends(sys.argv[2],[json.loads(sys.argv[3])])"
    def cache_dividend(rate):
        subprocess.run([sys.executable,'-c',fixture,str(expected),holding['instrument_id'],json.dumps(dict(dividend_source,per_share=rate))],cwd=Path(__file__).resolve().parents[1],check=True)
    candidates='/api/portfolios/'+p['id']+'/dividends?instrument_id='+holding['instrument_id']
    cache_dividend('1');candidate=client.request(candidates)[0]
    confirmed=client.write('/api/dividends/'+candidate['notice']['id']+'/confirm',dict(candidate['suggested_event'],gross_amount='99.9876',net_amount='98.7531',tax='1.2345',tax_known=True,pay_date='2024-06-05',fx_rate='.91',pay_fx_rate='.92',expected_revision=candidate['revision']),'POST')
    cache_dividend('1.01');candidate=client.request(candidates)[0]
    check(candidate['status']=='needs_review' and candidate['recorded_event']==confirmed,'AI reference correction exposes original receipt without rewriting it')
    original=client.request('/api/portfolios/'+p['id']+'/transactions/'+confirmed['id'])['event']
    body=dict(portfolio_id=p['id'],event_id=confirmed['id'],mode='keep_recorded',review_basis=candidate['review_basis'],expected_revision=candidate['revision'],reason='AI合成实际到账核对')
    check(revision()==candidate['revision'],'AI read-and-display review has no side effects')
    reviewed=client.write('/api/dividends/'+candidate['notice']['id']+'/review',body,'POST')
    check(reviewed['event']==original and client.request('/api/portfolios/'+p['id']+'/transactions/'+confirmed['id'])['event']==original,'AI completes review with exact receipt and FX preserved')
    check(client.request(candidates)[0]['status']=='confirmed' and not any(r.get('event_id')==confirmed['id'] for r in client.request('/api/reminders')['items']),'AI review clears candidate and shared reminder')
    check(client.request('/api/revisions/dividend_decision/'+reviewed['decision']['id'])[0]['after_data']['mode']=='keep_recorded','AI reads back review audit')
    try:client.write('/api/dividends/'+candidate['notice']['id']+'/review',body,'POST')
    except RuntimeError as error:check('HTTP 409' in str(error),'AI stale review cannot create duplicate event or decision')
    else:raise AssertionError('stale dividend review accepted')
    cache_dividend('1.01005')
    check(client.request(candidates)[0]['status']=='confirmed','AI Yahoo micro refresh leaves review complete')
    client.write('/api/events',dict(portfolio_id=p['id'],kind='BUY',date='2024-01-03',instrument_id=holding['instrument_id'],quantity='1',gross_amount='10',fx_rate='.9',expected_revision=revision()),'POST')
    check(client.request(candidates)[0]['status']=='needs_review','AI real entitlement change reopens review')
    (output/'ai-passed.json').write_text(json.dumps({'checks':len(checks),'passed':checks},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
