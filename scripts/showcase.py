#!/usr/bin/env python3
"""Start a disposable synthetic test instance for product screenshots, never an installer."""
from __future__ import annotations
import argparse
import math
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.environment import claim_environment, workspace_for
from backend.family_service import FamilyService
from backend.service import Service
from backend.store import Store


def seed(directory):
    """Only accept a nonexistent directory; never open an existing ledger."""
    directory=Path(directory)
    directory.mkdir()  # fails even for an existing empty directory or a symlink
    claim_environment(directory,'test')
    service=Service(Store(directory));family=FamilyService(service.store)
    end=date.today();start=date(end.year-1,1,1)
    months=(end.year-start.year)*12+end.month
    def command(operation,payload):
        return family.command(operation,payload,service.store.state()['revision'])['result']
    p=service.save_portfolio({'name':'长期投资 · 示例','planned_amount':'600000','start_date':str(start),'target_annual_pct':'6'})
    instruments=[]
    for n,(market,code,name,quantity,price,fx,category,region,target) in enumerate([
        ('CN','510300','宽基 ETF · 示例',20000,4.5,1,'权益','A股','30'),
        ('CN','515180','红利 ETF · 示例',40000,2,1,'权益','A股','25'),
        ('US','VTI','海外 ETF · 示例',40,250,7.1,'权益','美股','20'),
        ('FUND','006848','稳健债基 · 示例',50000,1.3,1,'债券','A股','15'),
    ]):
        event=service.save_event({'portfolio_id':p['id'],'kind':'BUY','date':str(start),
            'instrument':{'market':market,'code':code,'name':name},'quantity':str(quantity),
            'gross_amount':str(round(quantity*price,2)),'fee':'5','fx_rate':str(fx),'reason':'合成展示交易，不是真实投资记录'})
        iid=event['instrument_id'];instruments.append(iid)
        service.attach_instrument(p['id'],iid,{'target_pct':target,'asset_class':category,'region':region,'tags':['长期配置' if n!=3 else '稳健配置']})
        rows=[];length=max(1,(end-start).days)
        for offset in range(length+1):
            growth=[.125,.078,-.025,.032][n]*offset/length + math.sin(offset/16+n)*(.012 if n!=3 else .001)
            rows.append({'date':str(start+timedelta(days=offset)),'price':f'{price*(1+growth):.4f}','source':'synthetic-showcase'})
        service.cache_prices(iid,rows)
    service.cache_fx([{'currency':'USD','date':str(start+timedelta(days=i)),'rate':'7.1','source':'synthetic-showcase'} for i in range((end-start).days+1)])
    service.save_event({'portfolio_id':p['id'],'instrument_id':instruments[1],'kind':'DIVIDEND',
        'date':str(start+timedelta(days=min(100,(end-start).days))),'gross_amount':'1800','tax':'0','tax_known':True,'fx_rate':'1','reason':'合成展示分红'})
    assets=[('cash','日常账户 · 示例',85000,3500),('cash','应急储备 · 示例',60000,2000),
            ('fixed','自住住宅 · 示例',1800000,0),('investment','定期理财 · 示例',160000,2500),
            ('debt','住房贷款 · 示例',480000,-4200),('debt','汽车分期 · 示例',90000,-1500),('insurance','保障保单 · 示例',12000,120)]
    for category,name,amount,step in assets:
        extra={'payment_plan':{'mode':'fixed','first_due':str(start+timedelta(days=14)),'periods':120,'amount':'5200'}} if category=='debt' else {}
        item=command('item',{'category':category,'name':name,'currency':'CNY','effective_on':str(start),'amount':str(amount),'source':'synthetic-showcase',**extra})
        for month in range(2,months+1):
            command('valuation',{'item_id':item['id'],'effective_on':str(date(start.year+(month-1)//12,(month-1)%12+1,1)),
                'amount':str(amount+step*(month-1)),'source':'synthetic-showcase'})
        command('valuation',{'item_id':item['id'],'effective_on':str(end),'amount':str(amount+step*(months-1)),'source':'synthetic-showcase'})
    command('link',{'portfolio_id':p['id'],'effective_on':str(start),'share':'1'})
    command('settings',{'effective_on':str(start),'living_expense':'9000','reserve_months':'6','debt_plan_complete':True,'policy_plan_complete':True})
    for month in range(1,end.month+1):
        for kind,amount in [('salary',24000+month*200),('rent',3500),('fund',4800),('custom',1200+(month%3)*500)]:
            command('income',{'kind':kind,**({'name':'稿费 · 示例'} if kind=='custom' else {}),'month':f'{end.year}-{month:02d}','amount':str(amount),'currency':'CNY'})
    for title,body in [
        ('建立计划：先写下持有理由','## 这笔配置的角色\n\n作为长期组合的一部分，先确定投入计划，再持续核对记录。\n\n## 待观察\n\n1. 组合集中度是否符合计划。\n2. 剩余可投金额是否足够。\n3. 下一次调整有哪些依据。'),
        ('季度复盘：把判断留在当时','## 本次观察\n\n示例组合的持仓、投入计划和收益已经归到同一份账本。**先核对事实，再决定行动。**\n\n## 下一步\n\n1. 核对本季度交易与分红。\n2. 对照投入计划，确认资金安排。\n3. 保留本次记录，下一季度再回看。\n\n以上为产品截图使用的虚构笔记，不代表真实持仓或投资建议。'),
    ]:
        service.notes.save(p['id'],instruments[0],{'title':title,'body':body,'expected_revision':service.store.state()['revision']})
    return {'portfolio_id':p['id'],'instrument_id':instruments[0]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=workspace_for(ROOT)/'local/work/showcase')
    args=parser.parse_args();output=args.output.resolve()
    if output.is_relative_to(ROOT):raise SystemExit('截图临时账本必须位于源码仓库之外')
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='synthetic-showcase-',dir=output) as temp:
        data=Path(temp)/'data';seed(data)
        print('合成展示实例：http://127.0.0.1:8876/?family=overview；Ctrl+C 退出并删除本次临时账本。',flush=True)
        command=[sys.executable,str(ROOT/'run.py'),'--environment','test','--data-dir',str(data),
            '--runtime-dir',str(Path(temp)/'runtime'),'--port','8876','--no-open','--no-sync']
        with (output/'server.log').open('w') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            try:
                if process.wait():raise SystemExit('测试服务未正常退出，请检查本地 server.log；未切换其他端口或账本。')
            except KeyboardInterrupt:pass
            finally:
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
