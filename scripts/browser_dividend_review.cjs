/* Every fixture and write is synthetic and guarded by test environment + exact directory. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright'),root=path.resolve(__dirname,'..');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',out=process.env.PORTFOLIO_TEST_OUTPUT;
assert.ok(out&&process.env.PORTFOLIO_TEST_DATA_DIR);fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(20000);
 let csrf='',count=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(value,label)=>{assert.ok(value,label);console.log('PASS',++count,label);};
 const api=async(url,body)=>{const response=await page.request.fetch(base+'/api'+url,{method:body===undefined?'GET':'POST',data:body,headers:{'x-csrf-token':csrf,origin:base}});const value=await response.json();assert.ok(response.ok(),JSON.stringify(value));return value;};
 const dialog=()=>page.getByRole('dialog',{name:'分红确认与历史重算'});
 const keep=()=>dialog().getByRole('button',{name:'保留实际到账金额并完成核对',exact:true});
 function fixture(id,per_share){
  const result=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',
   "import json,sys;from pathlib import Path;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;d=Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'test');p=json.load(sys.stdin);Service(Store(d)).cache_dividends(p['id'],[dict(code='00700',source_ref='synthetic-browser-review',record_date=None,ex_date='2024-01-11',pay_date=None,per_share=p['per_share'],currency='HKD',source='Yahoo现金分派参考',split_restore_factor='1')])"],
   {cwd:root,encoding:'utf8',input:JSON.stringify({id,per_share})});assert.equal(result.status,0,result.stderr);
 }
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  csrf=(await api('/bootstrap')).csrf_token;
  const p=await api('/portfolios',{name:'合成分红保留验收',start_date:'2024-01-01',planned_amount:'10000'});
  const buy=await api('/events',{portfolio_id:p.id,kind:'BUY',date:'2024-01-02',instrument:{market:'HK',code:'00700',name:'合成分红标的'},quantity:'100',gross_amount:'1000',fx_rate:'.91'});
  const candidates=()=>api('/portfolios/'+p.id+'/dividends'),events=()=>api('/portfolios/'+p.id+'/transactions');
  fixture(buy.instrument_id,'1');let candidate=(await candidates())[0];
  const saved=await api('/dividends/'+candidate.notice.id+'/confirm',{...candidate.suggested_event,gross_amount:'99.9876',tax:'1.2345',net_amount:'98.7531',tax_known:true,pay_date:'2024-01-18',fx_rate:'.9134',fx_source_date:'2024-01-10',pay_fx_rate:'.9234',pay_fx_source_date:'2024-01-17',expected_revision:candidate.revision});
  const original=await events();fixture(buy.instrument_id,'1.00005');
  check((await candidates())[0].status==='confirmed'&&!(await api('/reminders')).items.some(r=>r.kind==='dividend'),'Yahoo micro adjustment does not reopen confirmed receipt');
  fixture(buy.instrument_id,'1.01');candidate=(await candidates())[0];check(candidate.status==='needs_review','substantive reference correction opens review');
  await page.goto(base+'/?reminders=1');await page.getByRole('button',{name:'核对分红',exact:true}).click();await keep().click();
  const preview=dialog().getByLabel('保留实际到账核对');await preview.waitFor();
  check((await preview.innerText()).includes('99.9876 − 税费 1.2345 = 税后 98.7531 HKD')&&(await preview.innerText()).includes('2024-01-18')&&(await preview.innerText()).includes('0.9234'),'review displays exact receipt, tax, payment date and locked FX');
  check(JSON.stringify(await events())===JSON.stringify(original),'opening review leaves ledger unchanged');
  for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:1100});await preview.scrollIntoViewIfNeeded();
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)&&await preview.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'review fits '+width+'px');
   await page.screenshot({path:path.join(out,'dividend-review-'+width+'.png'),fullPage:true});
  }
  await preview.getByRole('button',{name:'返回继续核对',exact:true}).click();await dialog().getByRole('button',{name:'关闭',exact:true}).click();
  check(JSON.stringify(await events())===JSON.stringify(original)&&(await candidates())[0].status==='needs_review','return and close do not complete review or rewrite receipt');
  await page.reload();await page.getByRole('button',{name:'核对分红',exact:true}).click();await keep().click();
  await dialog().getByRole('button',{name:'确认保留并完成核对',exact:true}).click();await dialog().getByText(/暂无待确认或待重算/).waitFor();
  candidate=(await candidates())[0];
  check(candidate.status==='confirmed'&&candidate.recorded_event.id===saved.id&&JSON.stringify(await events())===JSON.stringify(original),'confirm completes review under original event with every event field preserved');
  check(!(await api('/reminders')).items.some(r=>r.kind==='dividend'),'completed review disappears from shared reminder center');
  fixture(buy.instrument_id,'1.01005');check((await candidates())[0].status==='confirmed','refresh after explicit review keeps actual amount confirmed');
  fixture(buy.instrument_id,'1.02');await dialog().getByRole('button',{name:'关闭',exact:true}).click();await page.reload();
  await page.getByRole('button',{name:'核对分红',exact:true}).click();await keep().click();
  const clock=(await api('/bootstrap')).revision;
  await page.route('**/api/family/revision',route=>route.fulfill({json:{revision:clock}}));
  fixture(buy.instrument_id,'1.02001');await dialog().getByRole('button',{name:'确认保留并完成核对',exact:true}).click();
  await dialog().getByRole('alert').getByText(/账本已变化/).waitFor();
  check((await candidates())[0].status==='needs_review'&&JSON.stringify(await events())===JSON.stringify(original),'stale UI submission is rejected and leaves receipt unchanged');
  await dialog().getByRole('button',{name:'重新读取待核对分红',exact:true}).click();await keep().click();
  await dialog().getByRole('button',{name:'确认保留并完成核对',exact:true}).click();await dialog().getByText(/暂无待确认或待重算/).waitFor();
  check(JSON.stringify(await events())===JSON.stringify(original),'explicit reload and resubmission completes review without duplicate ledger event');
  await page.unroute('**/api/family/revision');
  await api('/events',{portfolio_id:p.id,instrument_id:buy.instrument_id,kind:'BUY',date:'2024-01-03',quantity:'.00001',gross_amount:'.01',fx_rate:'.91'});
  candidate=(await candidates())[0];
  check(candidate.status==='needs_review'&&candidate.review_changes.includes('权益份额变化'),'even a tiny real share change still requires review');
  await dialog().getByRole('button',{name:'关闭',exact:true}).click();await page.goto(base+'/?reminders=1');
  await page.getByRole('button',{name:'核对分红',exact:true}).waitFor();await page.screenshot({path:path.join(out,'dividend-review-reminder.png'),fullPage:true});
  check(!errors.length,'no uncaught browser errors');
  fs.writeFileSync(path.join(out,'dividend-review-results.json'),JSON.stringify({checks:count,errors},null,2));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
