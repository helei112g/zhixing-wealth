/* Settings pagination uses a fresh synthetic test ledger; sync is intercepted. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',output=process.env.PORTFOLIO_TEST_OUTPUT;
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1480,height:1050}});
 let csrf='',checks=0;const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(value,label)=>{assert.ok(value,label);console.log('PASS',++checks,label);};
 const api=async(url,data)=>{const r=await page.request.fetch(base+'/api'+url,{method:data?'POST':'GET',data,headers:{'X-CSRF-Token':csrf}});assert.ok(r.ok());return r.json();};
 const rows=()=>page.locator('[data-cache-instrument]');
 const ids=()=>rows().evaluateAll(es=>es.map(e=>e.dataset.cacheInstrument));
 const pager=page.getByRole('navigation',{name:'行情缓存分页'});
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);assert.equal(health.version,'0.9.0');
  csrf=(await api('/bootstrap')).csrf_token;
  for(let n=0;n<23;n++)await api('/instruments',{market:'SH',code:String(510000+n),name:'合成缓存标的'+n});
  const boot=await api('/bootstrap'),expected=boot.instruments.map(i=>i.id);
  await page.route('**/api/sync',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:{queued:false}});});
  await page.route('**/api/jobs',route=>route.fulfill({json:{jobs:[],syncing:false}}));
  boot.price_status[0].latest={date:'2026-01-06',source:'合成净值源',price:'1.25'};
  const bootstrap='**/api/bootstrap';
  await page.route(bootstrap,route=>route.fulfill({json:boot}));
  await page.goto(new URL('/?page=overview',base).href);
  await page.getByRole('navigation',{name:'全局页面'}).getByRole('button',{name:'全局设置',exact:true}).click();
  await page.getByRole('heading',{name:'每日估值汇率',exact:true}).waitFor();
  check(await page.getByRole('heading',{name:'行情与净值缓存',exact:true}).count()===0,'global settings no longer embeds the quote cache');
  await page.getByRole('navigation',{name:'主要页面'}).getByRole('button',{name:'数据管理',exact:true}).click();
  await rows().first().waitFor();
  check(await page.getByRole('columnheader',{name:'最新数据日期',exact:true}).isVisible()&&
   await rows().first().locator('time').getAttribute('datetime')==='2026-01-06','cache shows the observation date in its own column');
  check(await rows().count()===10&&await pager.getByRole('button',{name:'上一页'}).isDisabled(),'first page has ten rows and no previous page');
  const seen=await ids();
  await pager.getByRole('button',{name:'下一页'}).click();check(await rows().count()===10,'second page has ten rows');seen.push(...await ids());
  const target=(await ids())[0],refreshed=page.waitForResponse(r=>r.url()===base+'/api/bootstrap'&&r.ok());await rows().first().getByRole('button',{name:'重新同步'}).click();await refreshed;
  await page.waitForFunction(()=>!document.querySelector('[data-cache-instrument] button').disabled);
  check(requests.length===1&&requests[0].instrument_id===target&&requests[0].full&&requests[0].trigger==='manual_instrument','second-page sync sends the selected instrument');
  check((await ids())[0]===target&&(await pager.innerText()).includes('第 2 /'),'refresh retains current page');
  await pager.getByRole('button',{name:'下一页'}).click();seen.push(...await ids());
  check(await pager.getByRole('button',{name:'下一页'}).isDisabled()&&await rows().count()===expected.length-20,'last page has remaining rows and no next page');
  assert.deepEqual(seen,expected);check(new Set(seen).size===expected.length,'pagination visits all instruments once in original order');
  const allRefreshed=page.waitForResponse(r=>r.url()===base+'/api/bootstrap'&&r.ok());await page.getByRole('button',{name:'同步全部历史',exact:true}).click();await allRefreshed;await page.waitForFunction(()=>!document.querySelector('[data-cache-instrument] button').disabled);
  check(requests[1].full&&!requests[1].instrument_id,'sync all remains independent of visible page');
  check(await page.getByRole('heading',{name:'macOS 系统通知',exact:true}).count()===0,'native notifications moved out of settings');
  await page.route('**/api/data-updates/history?area=portfolio',route=>route.fulfill({json:{limit:5,jobs:Array.from({length:5},(_,n)=>({id:'synthetic-history-'+n,kind:n%2?'legacy':'public',state:'complete',started_at:'2026-01-01T10:00:0'+n+'+08:00',scopes:['quotes'],items:[],trigger:'manual'}))}}));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await page.locator('[data-update-job]').nth(4).waitFor();
  check(await page.getByRole('heading',{name:'更新记录',exact:true}).count()===1&&await page.locator('[data-update-job]').count()===5,'single combined history shows five total records');
  check(await page.getByRole('heading',{name:'公共数据更新任务',exact:true}).count()===0,'no second task panel');
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,'settings-pagination.png'),fullPage:true});
  // Simulate refreshed list sizes to verify clamping without deleting ledger rows.
  let visible=7;
  await page.route('**/api/bootstrap',route=>route.fulfill({json:{...boot,instruments:boot.instruments.slice(0,visible),jobs:[],syncing:false}}));
  await page.getByRole('button',{name:'同步全部历史',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-cache-instrument]').length===7);
  check((await pager.innerText()).includes('第 1 / 1 页'),'shorter refresh clamps page');
  for(const width of [390,320]){await page.setViewportSize({width,height:844});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'settings fits '+width);await page.screenshot({path:path.join(output,'settings-'+width+'.png'),fullPage:true});}
  visible=0;await page.getByRole('button',{name:'同步全部历史',exact:true}).click();await pager.waitFor({state:'hidden'});
  check(await rows().count()===0&&await page.getByText('录入交易后，按已启用的全局策略补齐行情。',{exact:true}).isVisible(),'empty list shows guidance without pager');
  check(!errors.length,'no browser runtime errors');fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({checks,errors},null,2));
 }catch(e){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
