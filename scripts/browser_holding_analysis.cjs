/* End-to-end acceptance. Every write is confined to a fresh synthetic test ledger. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876';
const out=path.join(process.env.PORTFOLIO_TEST_OUTPUT||path.join(root,'../local/work/browser'),'holding-analysis-results');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1050}});
 page.setDefaultTimeout(20000);let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(v,m)=>{assert.ok(v,m);console.log('PASS',++checks,m);};
 async function api(url,body,method=body===undefined?'GET':'POST'){
  const r=await page.request.fetch(base+'/api'+url,{method,data:body,headers:{origin:base,'x-csrf-token':csrf}}),value=await r.json();
  assert.ok(r.ok(),JSON.stringify(value));return value;
 }
 const nav=async name=>{if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('navigation',{name:'主要页面',exact:true}).getByRole('button',{name,exact:true}).click();await page.locator('.loading').waitFor({state:'hidden'});if(page.viewportSize().width<=760)await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);};
 const shot=name=>page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.ok(process.env.PORTFOLIO_TEST_DATA_DIR);
  assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  const boot=await api('/bootstrap');csrf=boot.csrf_token;
  const p=await api('/portfolios',{name:'合成持仓分析',planned_amount:'100000',start_date:'2026-01-01'});
  const first={portfolio_id:p.id,kind:'BUY',date:'2026-01-02',instrument:{market:'CN',code:'600501',name:'合成红利标的'},quantity:'100',gross_amount:'1000',fx_rate:'1',holding_labels:{scope:'A股',direction:'红利',detail:'央企红利'}};
  const preview=await api('/events/preview',first);
  check(preview.holding_labels.path.join('/')==='A股/红利/央企红利','first-buy labels participate in read-only preview');
  const events=[await api('/events',{...first,expected_revision:preview.revision})];
  for(const [code,name] of [['600502','合成价值标的'],['510901','合成海外债券基金']])events.push(await api('/events',{portfolio_id:p.id,kind:'BUY',date:'2026-01-02',instrument:{market:'CN',code,name},quantity:'100',gross_amount:'1000',fx_rate:'1'}));
  const fixture=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import json,os,sys;from pathlib import Path;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;d=Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve();assert d==Path(sys.argv[1]).resolve();claim_environment(d,'test');s=Service(Store(d));p=json.load(sys.stdin);[s.cache_prices(e['instrument_id'],[{'date':'2026-01-02','price':str(v),'source':'synthetic'},{'date':p['today'],'price':str(v),'source':'synthetic'}]) for e,v in zip(p['events'],[12,8,11])]",health.data_directory],{cwd:root,encoding:'utf8',input:JSON.stringify({events,today:boot.today})});assert.equal(fixture.status,0,fixture.stderr);
  const finance=await api('/portfolios/'+p.id+'/dashboard?view=summary');
  await page.goto(base);await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await nav('持仓标的');await page.locator('.holding-row').first().waitFor();
  check((await page.locator('.holdings-table th').allTextContents()).filter(x=>x==='占比').length===1,'one combined weights column');
  check(await page.locator('[data-ranking-id]').count()===0&&await page.getByRole('button',{name:'标签分析',exact:true}).count()===0,'holdings has neither ranking nor legacy tag analysis');
  const row=page.locator('.holding-row').filter({hasText:'合成海外债券基金'});
  const failedLabels='**/api/portfolios/'+p.id+'/instruments/'+events[2].instrument_id+'/labels';
  await page.route(failedLabels,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'合成首次读取失败'})}),{times:1});
  await row.getByRole('button',{name:'设置计划',exact:true}).click();const editor=page.locator('.holding-label-drawer');
  await editor.getByRole('button',{name:'重新读取当前计划',exact:true}).waitFor();
  const freshPlanBoot=await api('/bootstrap');await api('/portfolios/'+p.id+'/instruments/'+events[2].instrument_id,{target_pct:'17',asset_class:'债券',expected_revision:freshPlanBoot.revision},'PUT');
  await editor.getByRole('button',{name:'重新读取当前计划',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.holding-label-drawer input[aria-label="计划占总预算比例"]').value==='17');
  check(await editor.getByLabel('资产类别',{exact:true}).inputValue()==='债券','first successful context retry initializes the latest saved plan before editing');
  await editor.getByLabel('投资范围',{exact:true}).waitFor();check((await editor.innerText()).includes('待确认'),'fund exposure is not guessed from Shanghai listing');
  await editor.getByLabel('投资范围',{exact:true}).selectOption('海外债券');await editor.getByRole('button',{name:'国债',exact:true}).click();await editor.getByLabel('细分标签',{exact:true}).fill('美元国债');await editor.getByRole('button',{name:'保存计划',exact:true}).click();await editor.waitFor({state:'hidden'});
  await page.locator('.holding-row').filter({hasText:'合成海外债券基金'}).getByText('海外债券 / 国债 / 美元国债',{exact:true}).waitFor();
  check(true,'labels save in a right-hand drawer and appear under its name');
  const red=page.locator('.holding-row').filter({hasText:'合成红利标的'});await red.getByRole('button',{name:'设置计划',exact:true}).click();
  await editor.getByLabel('投资范围',{exact:true}).selectOption('大宗商品');await editor.getByRole('button',{name:'贵金属',exact:true}).click();await editor.getByLabel('细分标签',{exact:true}).fill('黄金');await editor.getByRole('button',{name:'保存计划',exact:true}).click();await editor.waitFor({state:'hidden'});
  const after=await api('/portfolios/'+p.id+'/dashboard?view=summary');
  check(['total_assets','securities_total','cash_total','pnl'].every(k=>finance[k]===after[k]),'label changes leave canonical financial amounts unchanged');
  await red.getByRole('button',{name:'设置计划',exact:true}).click();await editor.getByLabel('细分标签',{exact:true}).waitFor();
  await api('/portfolios',{name:'合成冲突修订',planned_amount:'10000',start_date:'2026-01-01'});
  await editor.getByLabel('细分标签',{exact:true}).fill('黄金增强');await editor.getByRole('button',{name:'保存计划',exact:true}).click();await editor.getByRole('alert').waitFor();
  check(await editor.getByLabel('细分标签',{exact:true}).inputValue()==='黄金增强','revision conflict preserves label draft');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/labels')&&r.request().method()==='GET'),editor.getByRole('button',{name:'重新读取并核对',exact:true}).click()]);await editor.getByRole('button',{name:'已核对，继续保存草稿',exact:true}).click();await editor.getByRole('button',{name:'保存计划',exact:true}).waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('.holding-label-drawer fieldset').disabled);
  await editor.getByRole('button',{name:'保存计划',exact:true}).click();await editor.waitFor({state:'hidden'});
  // Saving keeps the drawer stable and disables closing until its response settles.
  await row.getByRole('button',{name:'设置计划',exact:true}).click();await editor.getByLabel('细分标签',{exact:true}).fill('美元国债调整');
  let releaseSave,saveStored;const saveGate=new Promise(resolve=>{releaseSave=resolve;}),stored=new Promise(resolve=>{saveStored=resolve;});
  const labelsRoute='**/api/portfolios/*/instruments/*';
  await page.route(labelsRoute,async route=>{if(route.request().method()!=='PUT')return route.continue();const response=await route.fetch();saveStored();await saveGate;await route.fulfill({response});});
  await editor.getByRole('button',{name:'保存计划',exact:true}).click();await stored;
  check(await editor.getByRole('button',{name:'关闭',exact:true}).isDisabled()&&await editor.getByRole('button',{name:'取消',exact:true}).isDisabled(),'pending save keeps the active drawer stable');
  releaseSave();await editor.waitFor({state:'hidden'});await page.unroute(labelsRoute);
  await row.getByText('海外债券 / 国债 / 美元国债调整',{exact:true}).waitFor();
  await nav('持仓分析');await page.locator('.holding-sunburst-node').first().waitFor();
  check(await page.locator('.tag-layout,.tag-pie-chart,.holding-tree-sidebar').count()===0,'distribution has no duplicate sidebar or legacy pie');
  const svg=page.locator('.holding-sunburst svg'),box=await svg.boundingBox();check(box.width>=560,'sunburst is centered and uses the freed width');
  const report=await api('/portfolios/'+p.id+'/holding-analysis'),n=report.nodes[0],angle=-Math.PI/2+Number(n.value)/Number(report.total)*Math.PI,rad=box.width*.19+((box.width/2-3-box.width*.19)/3)/2;
  await svg.click({position:{x:box.width/2+rad*Math.cos(angle),y:box.width/2+rad*Math.sin(angle)}});
  check((await page.locator('.holding-distribution-detail').innerText()).includes(n.name),'mouse click reveals selected-group details');
  const leaf=page.getByRole('button',{name:/^大宗商品 \/ 贵金属 \/ 黄金增强/});await leaf.focus();await leaf.press('Enter');
  check(await page.locator('.holding-distribution-detail tbody tr').count()===1,'keyboard selects a specific leaf without a permanent legend');
  await shot('distribution-selected-desktop');await page.getByRole('button',{name:'返回全部',exact:true}).click();
  check(await page.locator('.holding-distribution-detail').count()===0,'all view contains only the distribution chart');await shot('distribution-desktop');
  await page.getByLabel('持仓分布统计口径',{exact:true}).selectOption('assets');await page.getByRole('button',{name:/^剩余可投金额/}).focus();await page.getByRole('button',{name:/^剩余可投金额/}).press('Space');
  check((await page.locator('.holding-distribution-detail').innerText()).includes('系统项'),'total-asset mode retains separate system cash');
  await page.getByRole('tab',{name:'收益排行榜',exact:true}).click();await page.locator('.ranking-row').first().waitFor();
  await page.getByLabel('排行榜排序指标',{exact:true}).selectOption('profit');await page.getByLabel('隐藏金额',{exact:true}).check();
  check(await page.getByLabel('排行榜排序指标',{exact:true}).inputValue()==='return_on_cost'&&await page.getByLabel('排行榜排序指标',{exact:true}).locator('option[value=profit]').count()===0,'hidden amounts also remove amount ranking');
  check(!(await page.locator('.holdings-ranking').innerText()).includes('CNY')&&(await page.locator('.ranking-value b').allTextContents()).every(t=>t.includes('%')||t==='—'),'ranking hidden mode renders rates without monetary hints');
  await page.getByLabel('排行榜排序指标',{exact:true}).selectOption('xirr');await shot('ranking-rates-desktop');
  for(const width of [390,320]){
   await page.setViewportSize({width,height:1000});
   for(const name of ['组合收益','持仓标的','交易记录','持仓分析']){
    await nav(name);await page.locator('.page-header .portfolio-valuation-caption').waitFor();
    check(await page.locator('.portfolio-valuation-caption').count()===1,name+' has one valuation caption in the header at '+width);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name+' fits '+width);
   }
   await page.locator('.holding-sunburst-node').first().focus();await page.locator('.holding-sunburst-node').first().press('Enter');await shot('distribution-selected-'+width);
   await page.getByRole('tab',{name:'收益排行榜',exact:true}).click();await page.getByLabel('隐藏金额',{exact:true}).waitFor();
   check(await page.getByLabel('隐藏金额',{exact:true}).isChecked()&&!(await page.locator('.holdings-ranking').innerText()).includes('CNY'),'ranking privacy preference survives navigation at '+width);await shot('ranking-rates-'+width);
   await nav('持仓标的');await page.locator('.holding-row').filter({hasText:'合成海外债券基金'}).getByRole('button',{name:'设置计划',exact:true}).click();await editor.getByLabel('投资范围',{exact:true}).waitFor();
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'label drawer stays within the page at '+width);await shot('label-editor-'+width);await editor.getByRole('button',{name:'取消',exact:true}).click();
  }
  const command=async(operation,payload)=>api('/family/commands',{operation,payload,expected_revision:(await api('/bootstrap')).revision});
  await command('item',{category:'cash',name:'合成现金',currency:'CNY',effective_on:'2026-01-01',household_amount:'1000'});
  await command('item',{category:'fixed',name:'合成固定资产',currency:'CNY',effective_on:'2026-01-01',household_amount:'2000'});
  const policy=await command('item',{category:'insurance',name:'合成待补保单',currency:'CNY',effective_on:'2026-01-01',household_amount:null});
  await page.setViewportSize({width:1600,height:1050});await page.reload();await page.getByRole('navigation',{name:'资产分析',exact:true}).getByRole('button',{name:'总览',exact:true}).click();
  const total=page.locator('[data-summary-metric=total_assets]');await total.locator('.balance-partial-note').waitFor();
  check((await total.innerText()).includes('已知资产合计')&&(await total.innerText()).includes('保单现金价值')&&(await total.locator('.balance-value').textContent()).includes('3,000'),'missing policy value shows known subtotal and precise reason');
  const summary=await api('/family/summary?grain=month&period='+boot.today.slice(0,7));check(summary.metrics.total_assets.value===null&&summary.metrics.total_assets.known_value==='3000','partial display preserves unknown complete total');await shot('assets-partial-total');
  await command('valuation',{item_id:policy.result.id,effective_on:boot.today,household_amount:'300'});await page.reload();await page.getByRole('navigation',{name:'资产分析',exact:true}).getByRole('button',{name:'总览',exact:true}).click();await total.locator('.balance-value').waitFor();
  check(await total.locator('.balance-partial-note').count()===0&&(await total.locator('.balance-value').textContent()).includes('3,300'),'complete total returns when the missing amount is supplied');
  check(!errors.length,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors,author:'Leo',date:boot.today},null,2));
 }catch(e){await shot('failure').catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
