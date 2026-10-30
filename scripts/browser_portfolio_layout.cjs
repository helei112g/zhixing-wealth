/* Introductions and dense lists. All writes require an isolated synthetic test ledger. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876';
const out=path.join(process.env.PORTFOLIO_TEST_OUTPUT,'portfolio-layout-results');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1000}});
 page.setDefaultTimeout(20000);let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(value,message)=>{assert.ok(value,message);console.log('PASS',++checks,message);};
 const api=async(url,data,method=data===undefined?'GET':'POST')=>{const response=await page.request.fetch(base+'/api'+url,{method,data,headers:{'x-csrf-token':csrf,origin:base}});const body=await response.json();assert.ok(response.ok(),JSON.stringify(body));return body;};
 const command=async(operation,payload)=>{const boot=await api('/bootstrap');return (await api('/family/commands',{operation,payload,expected_revision:boot.revision})).result;};
 const nav=async name=>{await page.getByRole('navigation',{name:'主要页面'}).getByRole('button',{name,exact:true}).click();await page.locator('.loading').waitFor({state:'hidden'});};
 const shot=async name=>page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 const ready=()=>page.locator('.transaction-viewport[aria-busy="false"]').waitFor();
 const dialog=()=>page.getByRole('dialog');
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  const boot=await api('/bootstrap');csrf=boot.csrf_token;const start=boot.today.slice(0,4)+'-01-01',month=boot.today.slice(0,7);
  await page.goto(base);await page.getByRole('button',{name:'创建第一个组合',exact:true}).click();
  const description='背景：为长期目标积累资金。\n\n策略：分批投入，定期检查计划。\n<b>这是纯文字示例</b>';
  await dialog().getByLabel('组合名称',{exact:true}).fill('合成组合介绍与明细验收');
  await dialog().getByLabel(/^组合介绍/).fill(description);
  await dialog().getByLabel(/^总计划投资金额/).fill('100000');await dialog().getByLabel('记账起始日',{exact:true}).fill(start);
  await dialog().getByLabel(/^比较基准/).selectOption('');await dialog().getByRole('button',{name:'创建组合',exact:true}).click();
  await dialog().waitFor({state:'hidden'});await page.locator('.portfolio-introduction').waitFor();
  let p=(await api('/bootstrap')).portfolios.find(p=>p.name==='合成组合介绍与明细验收');
  check(p.description===description,'new portfolio persists the optional multiline introduction');
  await page.locator('.portfolio-introduction summary').click();
  check(await page.locator('.portfolio-introduction p').textContent()===description&&await page.locator('.portfolio-introduction p b').count()===0,'overview preserves line breaks and renders markup as plain text');
  await page.getByRole('button',{name:'编辑组合介绍',exact:true}).click();
  check(await dialog().getByLabel(/^组合介绍/).inputValue()===description,'edit form reloads the saved introduction');
  const updated='背景：用于长期目标的合成验收组合。\n策略：分批投入，按计划复核，保留流动资金。';
  await dialog().getByLabel(/^组合介绍/).fill(updated);await dialog().getByRole('button',{name:'保存组合',exact:true}).click();await dialog().waitFor({state:'hidden'});
  await page.reload();await page.locator('.portfolio-introduction summary').click();
  check(await page.locator('.portfolio-introduction p').textContent()===updated,'edited introduction survives reload');
  await shot('portfolio-introduction-desktop');
  await page.getByRole('button',{name:'编辑组合介绍',exact:true}).click();await dialog().getByLabel(/^组合介绍/).fill('尚未提交的草稿');
  let current=await api('/bootstrap');p=current.portfolios.find(v=>v.id===p.id);
  await api('/portfolios/'+p.id,{...p,description:'另一次已保存的介绍',expected_revision:current.revision},'PUT');
  await dialog().getByRole('button',{name:'保存组合',exact:true}).click();await dialog().getByRole('alert').waitFor();
  check(await dialog().getByLabel(/^组合介绍/).inputValue()==='尚未提交的草稿'&&(await api('/bootstrap')).portfolios.find(v=>v.id===p.id).description==='另一次已保存的介绍','stale edit preserves the draft and cannot overwrite a concurrent change');
  await dialog().getByRole('button',{name:'取消',exact:true}).click();await page.reload();await page.locator('.portfolio-introduction summary').click();
  await page.getByRole('button',{name:'编辑组合介绍',exact:true}).click();await dialog().getByLabel(/^组合介绍/).fill('');await dialog().getByRole('button',{name:'保存组合',exact:true}).click();await dialog().waitFor({state:'hidden'});
  await page.locator('.portfolio-introduction summary').getByText('暂未填写',{exact:true}).waitFor();
  check((await api('/bootstrap')).portfolios.find(v=>v.id===p.id).description==='','introduction can be explicitly cleared');
  current=await api('/bootstrap');p=current.portfolios.find(v=>v.id===p.id);await api('/portfolios/'+p.id,{...p,description:updated,expected_revision:current.revision},'PUT');
  const other=await api('/portfolios',{name:'合成空介绍组合',planned_amount:'1000',start_date:start});
  const events=[];for(let n=0;n<26;n++)events.push(await api('/events',{portfolio_id:p.id,kind:'BUY',date:start,instrument:{market:'CN',code:'510300',name:'合成宽基标的'},quantity:'1',gross_amount:String(10+n),fx_rate:'1'}));
  const fixture=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import sys;from pathlib import Path;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;d=Path(sys.argv[1]).resolve();assert d==Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'test');Service(Store(d)).cache_prices(sys.argv[2],[{'date':sys.argv[3],'price':'40','source':'synthetic'},{'date':sys.argv[4],'price':'40','source':'synthetic'}])",process.env.PORTFOLIO_TEST_DATA_DIR,events[0].instrument_id,start,boot.today],{cwd:root,encoding:'utf8'});assert.equal(fixture.status,0,fixture.stderr);
  await page.reload();await page.getByLabel('投资组合',{exact:true}).selectOption(other.id);await page.locator('.portfolio-introduction summary').getByText('暂未填写',{exact:true}).waitFor();
  check(!await page.locator('.portfolio-introduction').getAttribute('open'),'changing portfolio resets the introduction fold');
  await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await page.locator('.portfolio-introduction summary').getByText('查看背景与策略',{exact:true}).waitFor();
  for(const width of [1600,390,320]){
   await page.setViewportSize({width,height:1000});await nav('持仓标的');await page.locator('.holding-row').waitFor();
   check(await page.locator('.overview-summary').count()===0&&await page.locator('.holdings-list.list-section').count()===1,'holdings uses the flat layout without top statistics at '+width);
   check(await page.locator('.holding-row').locator('button').filter({hasText:'设置计划'}).count()===1,'investment plan editing remains available at '+width);
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'holdings fits viewport '+width);await shot('holdings-'+width);
   await nav('资金与流水');await ready();
   check(await page.locator('.overview-summary').count()===0&&await page.locator('.ledger-list.list-section').count()===1&&await page.locator('.ledger-list.panel').count()===0,'ledger opens into the same flat list style at '+width);
   check(await page.locator('[data-ledger-event]').count()===20,'ledger retains twenty-row default pagination at '+width);
   check(await page.locator('.transaction-viewport').evaluate(e=>e.clientHeight>=e.scrollHeight-1),'ledger expands to show all rows without vertical clipping at '+width);
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'ledger confines horizontal overflow to the table at '+width);await shot('ledger-'+width);
   await page.getByRole('navigation',{name:'流水分页'}).getByRole('button',{name:'下一页',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-ledger-event]').length===6);check(true,'ledger next page remains usable at '+width);
   await nav('组合总览');await page.locator('.overview-summary').waitFor();await page.locator('.portfolio-introduction summary').click();
   check(await page.locator('.portfolio-introduction p').textContent()===updated&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'overview keeps statistics and readable introduction at '+width);
   await shot('introduction-'+width);
  }
  await page.setViewportSize({width:1600,height:1000});
  const cash=await command('item',{category:'cash',name:'合成流动资金',currency:'CNY',effective_on:start,household_amount:'100000'});
  await command('item',{category:'debt',name:'合成负债',currency:'CNY',effective_on:start,household_amount:'25000'});
  await command('income',{kind:'salary',month,currency:'CNY',amount:'11800',date:month+'-01'});
  await page.goto(base+'/?family=overview');await page.locator('[data-summary-debt-ratio] strong').getByText('25.00%',{exact:true}).waitFor();
  let summary=await api('/family/summary?grain=month&period='+month);
  check(await page.locator('[data-summary-income] b').textContent()===summary.period.start+' 至 '+summary.period.end+' 已记录收入','month income label gives exact inclusive dates');
  check((await page.locator('[data-summary-debt-ratio]').innerText()).includes('25.00%')&&summary.metrics.debt_ratio.value==='0.25','debt ratio uses canonical end-of-period balances');await shot('asset-overview-desktop');
  for(const [button,query] of [['按年','grain=year&period='+boot.today.slice(0,4)],['自定义','grain=custom&start='+start+'&end='+boot.today]]){
   await page.locator('.family-period-controls').getByRole('button',{name:button,exact:true}).click();summary=await api('/family/summary?'+query);
   await page.waitForFunction(text=>document.querySelector('[data-summary-income] b')?.textContent===text,summary.period.start+' 至 '+summary.period.end+' 已记录收入');check(true,button+' income label follows actual returned dates');
  }
  for(const width of [390,320]){await page.setViewportSize({width,height:1000});check(await page.locator('[data-summary-debt-ratio] strong').isVisible()&&await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'dates and debt ratio remain readable at '+width);await shot('asset-overview-'+width);}
  await command('valuation',{item_id:cash.id,effective_on:boot.today,household_amount:'0'});await page.reload();await page.locator('[data-summary-debt-ratio] strong').getByText('暂无法计算',{exact:true}).waitFor();check(true,'zero assets display unavailable ratio instead of zero percent');
  check(errors.length===0,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2));
 }catch(error){await shot('failure').catch(()=>{});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
