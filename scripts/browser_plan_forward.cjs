/* Only explicitly marked synthetic test data; no requests may mutate a real ledger. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright'),root=path.resolve(__dirname,'..');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',out=path.join(process.env.PORTFOLIO_TEST_OUTPUT||path.join(root,'../local/work/browser'),'plan-forward-results');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1050}});page.setDefaultTimeout(20000);
 let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(value,message)=>{assert.ok(value,message);console.log('PASS',++checks,message);};
 const api=async(url,data,method=data===undefined?'GET':'POST')=>{const r=await page.request.fetch(base+'/api'+url,{method,data,headers:{'x-csrf-token':csrf,origin:base}});const value=await r.json();assert.ok(r.ok(),url+' '+JSON.stringify(value));return value;};
 const nav=async name=>{await page.getByRole('navigation',{name:name==='全局设置'?'全局页面':'主要页面'}).getByRole('button',{name,exact:true}).click();await page.locator('.loading').waitFor({state:'hidden'});};
 const dialog=()=>page.getByRole('dialog'),target=()=>dialog().getByLabel('计划占总预算比例 · %',{exact:true});
 const waitText=async(text)=>{await dialog().locator('.plan-funding>strong').filter({hasText:text}).waitFor();};
 const row=name=>page.locator('.holding-row').filter({hasText:name});
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR||path.join(root,'../local/test/browser-test')));assert.equal(health.version,'0.8.5');
  const boot=await api('/bootstrap');csrf=boot.csrf_token;
  const p=await api('/portfolios',{name:'计划与行情专项组合',planned_amount:'500000',start_date:'2024-01-01'});
  const buy=(code,name,qty,total)=>api('/events',{portfolio_id:p.id,kind:'BUY',date:'2024-01-31',instrument:{market:'CN',code,name},quantity:qty,gross_amount:total,fx_rate:'1'});
  const a=await buy('510050','计划已投入标的','35000','350000'),b=await buy('512000','计划待投入标的','5000','50000');
  await api('/portfolios/'+p.id+'/instruments/'+a.instrument_id,{target_pct:'70'},'PUT');
  // Insert provider-shaped quotes only in the isolated test database, never expose a manual-price API.
  const seeded=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import json,sys;from pathlib import Path;from backend.service import Service;from backend.store import Store;p=json.load(sys.stdin);assert Path(p['dir']).resolve()==Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();Service(Store(Path(p['dir']))).cache_prices(p['id'],p['rows'])"],{cwd:root,encoding:'utf8',input:JSON.stringify({dir:health.data_directory,id:a.instrument_id,rows:[{date:'2024-01-31',price:'10',source:'合成行情'},{date:'2024-02-29',price:'12',source:'合成行情'},{date:'2024-04-30',price:'9',source:'合成行情'}]})});assert.equal(seeded.status,0,seeded.stderr);
  const original=(await api('/portfolios/'+p.id+'/dashboard')).events;
  await page.goto(base);await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await nav('持仓标的');
  await row('计划待投入标的').getByRole('button',{name:'设置计划',exact:true}).click();await target().fill('40');await waitText('计划资金缺口 50,000.00 CNY');
  check((await dialog().innerText()).includes('150,000.00')&&(await dialog().innerText()).includes('100,000.00'),'preview compares remaining need 150000 with available 100000');
  check(await dialog().getByRole('button',{name:'保存计划',exact:true}).isEnabled(),'shortfall does not disable save');
  await page.screenshot({path:path.join(out,'plan-warning.png'),fullPage:true});
  await dialog().getByRole('button',{name:'保存计划',exact:true}).click();await dialog().waitFor({state:'hidden'});await nav('组合收益');await page.locator('.summary-facts .plan-funding').getByText('计划资金缺口 50,000.00 CNY',{exact:true}).waitFor();
  const saved=await api('/portfolios/'+p.id+'/dashboard');check(saved.plan_funding.total_target_pct==='110'&&saved.plan_funding.shortfall==='50000','combined 110 percent plan persists with exact shortage');
  for(const name of ['组合收益','交易记录','持仓标的']){await nav(name);if(name==='组合收益')check((await page.locator('.summary-facts').innerText()).includes('计划资金缺口 50,000.00 CNY'),'overview summary shows the canonical plan gap');else check(await page.locator('.overview-summary').count()===0,'list page keeps its statistics card removed: '+name);}
  // Overinvested A cannot fund the still-unfilled B, even with total plans below 100%.
  await row('计划已投入标的').getByRole('button',{name:'设置计划',exact:true}).click();await target().fill('50');await waitText('计划资金缺口 50,000.00 CNY');check((await dialog().innerText()).includes('90.00%'),'overinvestment does not cancel shortage at 90 percent total plans');await dialog().getByRole('button',{name:'取消',exact:true}).click();
  await row('计划待投入标的').getByRole('button',{name:'设置计划',exact:true}).click();await target().fill('30');await waitText('剩余计划与可投金额匹配');check(true,'balanced plan is identified');await target().fill('20');await waitText('计划后余量 50,000.00 CNY');check(true,'surplus plan is identified');
  let release,started;const gate=new Promise(r=>release=r),began=new Promise(r=>started=r);
  const delayed='**/api/portfolios/'+p.id+'/instruments/'+b.instrument_id+'/plan-check?target_pct=50';
  await page.route(delayed,async route=>{const response=await route.fetch();started();await gate;await route.fulfill({response});});
  await target().fill('50');await began;check(await dialog().locator('.plan-funding').count()===0,'stale proposal is hidden while checking new percentage');
  await target().fill('20');await waitText('计划后余量 50,000.00 CNY');release();await page.waitForTimeout(250);check((await dialog().locator('.plan-funding>strong').innerText()).includes('计划后余量 50,000.00'),'late check cannot overwrite latest input');await page.unroute(delayed);
  const failed='**/api/portfolios/'+p.id+'/instruments/'+b.instrument_id+'/plan-check?target_pct=35';await page.route(failed,route=>route.abort());await target().fill('35');await dialog().getByRole('button',{name:'重试额度校验',exact:true}).waitFor();check(await dialog().getByRole('button',{name:'保存计划',exact:true}).isEnabled(),'failed advisory check does not disable saving');await page.unroute(failed);await dialog().getByRole('button',{name:'重试额度校验',exact:true}).click();await waitText('计划资金缺口 25,000.00 CNY');check(true,'failed check is retryable');await dialog().getByRole('button',{name:'取消',exact:true}).click();
  await nav('交易记录');await page.locator('[data-ledger-event="'+a.id+'"] td').first().click();await dialog().getByRole('heading',{name:'交易后行情表现',exact:true}).waitFor();
  const windows=dialog().locator('.forward-window');check(await windows.count()===2,'only one- and three-month windows appear');
  check((await windows.nth(0).innerText()).includes('+20.00%')&&(await windows.nth(1).innerText()).includes('-10.00%'),'reference returns display positive and negative values');
  check((await windows.nth(0).innerText()).includes('2024-02-29')&&(await windows.nth(1).innerText()).includes('2024-04-30'),'calendar-month targets and quote dates visible');
  check((await dialog().innerText()).includes('价格涨跌（未含分红）')&&await dialog().getByRole('heading',{name:'修订记录',exact:true}).count()===1&&!await dialog().getByRole('button',{name:'保存复盘',exact:true}).count(),'market basis and history remain separate without review editor');
  const fromLedger=await dialog().locator('.forward-performance').innerText();await page.screenshot({path:path.join(out,'trade-reference.png'),fullPage:true});await dialog().getByRole('button',{name:'关闭侧栏',exact:true}).click();
  await nav('持仓标的');await row('计划已投入标的').getByRole('button',{name:'计划已投入标的',exact:true}).click();await page.locator('[data-instrument-event="'+a.id+'"] td').first().click();await dialog().getByRole('heading',{name:'交易后行情表现',exact:true}).waitFor();check(await dialog().locator('.forward-performance').innerText()===fromLedger,'instrument and ledger share identical reference data');
  for(const width of [390,320]){await page.setViewportSize({width,height:1000});check(await dialog().evaluate(el=>el.scrollWidth<=el.clientWidth+1),'trade reference fits '+width);await page.screenshot({path:path.join(out,'trade-'+width+'.png'),fullPage:true});}await dialog().getByRole('button',{name:'关闭侧栏',exact:true}).click();
  await page.setViewportSize({width:1600,height:1050});await nav('交易记录');await page.locator('[data-ledger-event="'+b.id+'"] td').first().click();await dialog().getByText('缺交易日收盘价 / 净值',{exact:true}).first().waitFor();check(await dialog().getByText('缺交易日收盘价 / 净值',{exact:true}).count()===2,'missing data is explicit instead of fabricated zero');await dialog().getByRole('button',{name:'关闭侧栏',exact:true}).click();
  check(JSON.stringify((await api('/portfolios/'+p.id+'/dashboard')).events)===JSON.stringify(original),'plan checks and changes preserve financial transactions');
  for(const width of [390,320]){await page.setViewportSize({width,height:1000});await nav('持仓标的');check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'gap summary fits '+width);await row('计划待投入标的').getByRole('button',{name:'设置计划',exact:true}).click();await waitText('计划资金缺口 50,000.00 CNY');check(await dialog().evaluate(el=>el.scrollWidth<=el.clientWidth+1),'plan warning fits '+width);await page.screenshot({path:path.join(out,'plan-'+width+'.png'),fullPage:true});await dialog().getByRole('button',{name:'取消',exact:true}).click();}
  check(!errors.length,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2));
 }catch(e){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
