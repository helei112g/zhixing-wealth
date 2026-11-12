const {openPortfolio}=require('./browser_portfolio_nav.cjs');
/* Status, price-change and allocation UI: fresh synthetic test ledger only. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base=process.env.PORTFOLIO_TEST_URL,out=process.env.PORTFOLIO_TEST_OUTPUT;
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}});page.setDefaultTimeout(20000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));let csrf='',checks=0;
 const check=(value,label)=>{assert.ok(value,label);console.log('PASS',++checks,label);};
 const api=async(url,data,method=data===undefined?'GET':'POST')=>{const r=await page.request.fetch(base+'/api'+url,{method,data,headers:{origin:base,'x-csrf-token':csrf}});assert.ok(r.ok(),url+' '+r.status());return r.json();};
 const nav=async name=>{if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('navigation',{name:'主要页面'}).getByRole('button',{name,exact:true}).click();await page.locator('.loading').waitFor({state:'hidden'});if(page.viewportSize().width<=760)await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);};
 const row=name=>page.locator('.holding-row').filter({hasText:name});
 const shot=async name=>page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 try{
  const h=await api('/health');assert.equal(h.environment,'dev');assert.equal(h.synthetic_fixture,true);assert.equal(path.resolve(h.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  const boot=await api('/bootstrap');csrf=boot.csrf_token;
  const p=await api('/portfolios',{name:'合成状态与行情验收',planned_amount:'10000',start_date:'2024-01-01'});
  const buy=(code,name)=>api('/events',{portfolio_id:p.id,kind:'BUY',date:'2024-01-02',instrument:{market:'FUND',code,name},quantity:'100',gross_amount:'1000',fx_rate:'1'});
  const a=await buy('000001','合成稳健基金'),b=await buy('000002','合成成长基金'),closed=await buy('000003','合成已清仓基金');
  await api('/events',{portfolio_id:p.id,instrument_id:closed.instrument_id,kind:'SELL',date:'2024-02-01',sell_all:true,gross_amount:'1050',fx_rate:'1'});
  // The old B lot is closed, but a later lot keeps the instrument held.
  await api('/events',{portfolio_id:p.id,instrument_id:b.instrument_id,kind:'SELL',date:'2024-02-01',sell_all:true,gross_amount:'950',fx_rate:'1'});
  const reopen=await api('/events',{portfolio_id:p.id,instrument_id:b.instrument_id,kind:'BUY',date:'2024-03-01',quantity:'100',gross_amount:'1000',fx_rate:'1'});
  const unused=await api('/instruments',{market:'FUND',code:'000004',name:'合成尚未买入基金'});
  await api('/portfolios/'+p.id+'/instruments/'+unused.id,{target_pct:'0'},'PUT');
  await api('/portfolios/'+p.id+'/instruments/'+a.instrument_id,{target_pct:'70'},'PUT');
  await api('/portfolios/'+p.id+'/instruments/'+b.instrument_id,{target_pct:'20'},'PUT');
  const fixture=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',
   "import json,sys;from pathlib import Path;from datetime import date,timedelta;from backend.service import Service;from backend.store import Store;from backend.environment import claim_environment;p=json.load(sys.stdin);d=Path(p['directory']).resolve();assert d==Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'dev');s=Service(Store(d));at=date.fromisoformat(p['today']);monday=at-timedelta(days=at.weekday());latest=at-timedelta(days=max(0,at.weekday()-4));previous=latest-timedelta(days=3 if latest.weekday()==0 else 1);anchor=monday-timedelta(days=3);[(s.cache_prices(i,[{'date':str(anchor),'price':'10','source':'合成净值'},{'date':str(previous),'price':'10','source':'合成净值'},{'date':str(latest),'price':v,'source':'合成净值'}])) for i,v in p['instruments']]"],
   {cwd:root,encoding:'utf8',input:JSON.stringify({directory:h.data_directory,today:boot.today,instruments:[[a.instrument_id,'12'],[b.instrument_id,'9'],[closed.instrument_id,'11']]})});assert.equal(fixture.status,0,fixture.stderr);
  await page.goto(base);await nav('组合收益');await openPortfolio(page,p.id);
  for(const width of [1600,390,320]){
   await page.setViewportSize({width,height:1000});await nav('持仓标的');await row('合成稳健基金').waitFor();
   check(await row('合成稳健基金').locator('.holding-identity .holding-status').innerText()==='持有中','holding badge in identity '+width);
   check(await row('合成已清仓基金').locator('.holding-status').innerText()==='已清仓'&&await row('合成尚未买入基金').locator('.holding-status').innerText()==='尚未买入','closed and unbought distinct '+width);
   check(await page.locator('.holding-return').evaluateAll(es=>es.every(e=>!e.textContent.includes('持有中')&&!e.textContent.includes('已清仓'))),'return column omits holding state '+width);
   for(const [name,color] of [['合成稳健基金','rgb(181, 83, 78)'],['合成成长基金','rgb(41, 123, 110)']]){
    const style=await row(name).locator('.transaction-return-amount').evaluate(e=>({color:getComputedStyle(e).color,weight:Number(getComputedStyle(e).fontWeight)}));
    check(style.color===color&&style.weight>=700,'profit amount colored and bold '+name+' '+width);
   }
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'holding table contains horizontal overflow '+width);await shot('holdings-'+width);
   await row('合成稳健基金').getByRole('button',{name:'合成稳健基金',exact:true}).click();await page.locator('[data-market-change="daily"]').waitFor();
   const detail=await api('/portfolios/'+p.id+'/instruments/'+a.instrument_id);
   for(const key of ['daily','weekly']){
    const value=detail.market_changes[key],metric=page.locator('[data-market-change="'+key+'"]');
    check(await metric.locator('strong').innerText()==='+20.00%'&&(await metric.innerText()).includes(value.from_date+' → '+value.to_date),'quote value and actual dates '+key+' '+width);
   }
   check(await page.locator('[data-instrument-event="'+a.id+'"] .transaction-identity .holding-status').innerText()==='持有中','detail transaction status is in identity '+width);
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'instrument metrics fit '+width);await page.locator('.instrument-summary').screenshot({path:path.join(out,'instrument-'+width+'.png')});
   await nav('交易记录');await page.locator('[data-ledger-event="'+b.id+'"]').waitFor();
   check(await page.locator('[data-ledger-event="'+b.id+'"] .holding-status').innerText()==='已清仓'&&await page.locator('[data-ledger-event="'+reopen.id+'"] .holding-status').innerText()==='持有中','old and new lots keep independent states '+width);await shot('transactions-'+width);
   await nav('持仓标的');await row('合成成长基金').getByRole('button',{name:'设置计划',exact:true}).click();const dialog=page.getByRole('dialog'),target=dialog.getByLabel('计划占总预算比例',{exact:true});
   for(const [pct,label,rate,amount] of [['20','尚可分配','10.00%','1,000.00 CNY'],['30','总预算已分配完成','0.00%','0.00 CNY'],['40','超出总预算','10.00%','1,000.00 CNY']]){
    await target.fill(pct);await dialog.locator('.plan-allocation').getByText(label,{exact:true}).waitFor();
    const text=await dialog.locator('.plan-allocation').innerText();check(text.includes(rate)&&text.includes(amount),'plan allocation '+pct+' percent '+width);
   }
   check(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1),'plan balance fits '+width);await shot('plan-overallocated-'+width);
   await target.fill('20');await dialog.locator('.plan-allocation').getByText('尚可分配',{exact:true}).waitFor();await shot('plan-remaining-'+width);await dialog.getByRole('button',{name:'取消',exact:true}).click();
  }
  await row('合成尚未买入基金').getByRole('button',{name:'合成尚未买入基金',exact:true}).click();await page.locator('[data-market-change="daily"]').getByText('暂无有效行情，请更新此标的',{exact:true}).waitFor();
  check(await page.locator('[data-market-change="daily"] strong').innerText()==='—','missing quote is unknown, not zero');
  check(errors.length===0,'no browser errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2));
 }catch(e){await shot('failure').catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
