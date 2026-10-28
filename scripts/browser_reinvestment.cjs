/* All fixtures and writes target the exact marked synthetic test ledger. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright'),root=path.resolve(__dirname,'..');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',out=process.env.PORTFOLIO_TEST_OUTPUT;
assert.ok(out&&process.env.PORTFOLIO_TEST_DATA_DIR);fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(20000);
 let csrf='',count=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(v,m)=>{assert.ok(v,m);console.log('PASS',++count,m);};
 const api=async(u,body,method=body===undefined?'GET':'POST')=>{const r=await page.request.fetch(base+'/api'+u,{method,data:body,headers:{'x-csrf-token':csrf,origin:base}});const v=await r.json();assert.ok(r.ok(),JSON.stringify(v));return v;};
 const dialog=()=>page.getByRole('dialog'),field=label=>dialog().getByLabel(new RegExp('^'+label));
 const waitValue=async(value)=>{await page.waitForFunction(value=>[...document.querySelectorAll('label')].some(el=>el.textContent.startsWith('再投实际确认份额')&&el.querySelector('input')?.value===value),value);};
 const close=async()=>{await dialog().getByRole('button',{name:'取消',exact:true}).click();};
 const previewButton=()=>dialog().getByRole('button',{name:'预览记账结果',exact:true});
 function fixture(id,rows,notice){
  const result=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',
   "import json,sys;from pathlib import Path;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;p=json.load(sys.stdin);d=Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'test');s=Service(Store(d));s.cache_prices(p['id'],p['rows']);s.cache_dividends(p['id'],[p['notice']]) if p.get('notice') else None"],
   {cwd:root,encoding:'utf8',input:JSON.stringify({id,rows,notice})});assert.equal(result.status,0,result.stderr);
 }
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  csrf=(await api('/bootstrap')).csrf_token;
  const p=await api('/portfolios',{name:'合成再投份额验收',start_date:'2024-01-01',planned_amount:'10000'});
  const fund=async(code)=>api('/events',{portfolio_id:p.id,kind:'BUY',date:'2024-01-02',instrument:{market:'FUND',code,name:'合成基金'+code},quantity:'100',gross_amount:'100',fx_rate:'1'});
  const a=await fund('000001'),b=await fund('000002'),quote=(date,price)=>({date,price,kind:'raw',source:'合成单位净值'});
  fixture(a.instrument_id,[quote('2024-06-03','1.25'),quote('2024-06-04','1.5')]);fixture(b.instrument_id,[quote('2024-06-03','2.5')]);
  await page.goto(base);await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await page.getByRole('button',{name:'记录一笔',exact:true}).click();
  await field('流水类型').selectOption('DIVIDEND');await field('当前组合内标的').selectOption(a.instrument_id);
  await field('除息日 / 暂定入账日').fill('2024-06-03');await field('到账日期').fill('2024-06-03');await field('分红总金额').fill('12.5');
  await field('流水类型').selectOption('REINVEST');await field('再投净值归属日').fill('2024-06-03');await waitValue('10');
  check(await field('证券 / 基金代码').inputValue()==='000001'&&await field('再投总价').inputValue()==='12.5','cash-to-reinvest keeps fund and defaults net amount');
  check((await dialog().innerText()).includes('2024-06-03 单位净值 1.25 CNY')&&(await dialog().innerText()).includes('四舍五入至两位小数'),'estimate shows exact NAV date, source and rounding');
  await field('再投总价').fill('10');await waitValue('8');await field('再投费用').fill('1');await field('需扣税费').fill('1');check(await field('再投实际确认份额').inputValue()==='8','tax and fee are not deducted twice');
  await field('再投净值归属日').fill('2024-06-04');await waitValue('6.67');check(true,'date change recomputes rounded shares');
  await field('再投净值归属日').fill('2024-06-05');await dialog().getByText(/缺少所选日单位净值/).waitFor();check(await field('再投实际确认份额').inputValue()===''&&await previewButton().isDisabled(),'missing day clears estimate and blocks stale preview');
  fixture(a.instrument_id,[quote('2024-06-05','2')]);await dialog().getByRole('button',{name:'重新读取净值',exact:true}).click();await waitValue('5');check(true,'retry reads newly synced quote');
  await field('再投实际确认份额').fill('4.98');await field('再投总价').fill('20');await field('再投净值归属日').fill('2024-06-03');check(await field('再投实际确认份额').inputValue()==='4.98','manual value survives amount and date changes');
  await dialog().getByRole('button',{name:'按净值重新估算',exact:true}).click();await waitValue('16');check(true,'explicit action restores automatic estimation');
  const pattern='**/reinvestment-estimate?date=2024-06-04&*';
  async function delayed(action){let release,started,finished;const gate=new Promise(r=>release=r),began=new Promise(r=>started=r),done=new Promise(r=>finished=r);
   await page.route(pattern,async route=>{const response=await route.fetch();started();await gate;await route.fulfill({response});finished();});
   await field('再投净值归属日').fill('2024-06-04');await began;check(await field('再投实际确认份额').inputValue()===''&&await previewButton().isDisabled(),'pending request cannot submit old estimate');
   await action();release();await done;await page.unroute(pattern);await page.waitForTimeout(100);
  }
  await delayed(async()=>{await field('再投净值归属日').fill('2024-06-03');await waitValue('16');});check(await field('再投实际确认份额').inputValue()==='16','late response cannot replace newer date');
  await delayed(async()=>{await field('再投实际确认份额').fill('13');});check(await field('再投实际确认份额').inputValue()==='13','late response cannot replace manual value');
  await dialog().getByRole('button',{name:'按净值重新估算',exact:true}).click();await waitValue('13.33');
  const failed='**/reinvestment-estimate?date=2024-06-05&*';await page.route(failed,route=>route.abort());await field('再投净值归属日').fill('2024-06-05');
  await page.waitForFunction(()=>[...document.querySelectorAll('label')].some(el=>el.textContent.startsWith('再投实际确认份额')&&el.querySelector('input')?.getAttribute('aria-busy')==='false'));
  check(await field('再投实际确认份额').inputValue()===''&&await previewButton().isDisabled(),'network failure leaves no stale value');await page.unroute(failed);
  await dialog().getByRole('button',{name:'重新读取净值',exact:true}).click();await waitValue('10');
  await field('再投净值归属日').fill('2024-06-03');await waitValue('16');await field('证券 / 基金代码').fill('000002');await waitValue('8');check(true,'new instrument uses its own NAV');
  await field('证券 / 基金代码').fill('000099');await waitValue('');check(await previewButton().isDisabled(),'unknown code cannot reuse previous instrument ID');
  await field('证券 / 基金代码').fill('000001');await waitValue('16');await field('分红总金额').fill('22');
  for(const width of [1440,390,320]){await page.setViewportSize({width,height:1100});await field('再投实际确认份额').scrollIntoViewIfNeeded();check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'form fits '+width);await page.screenshot({path:path.join(out,'reinvestment-'+width+'.png'),fullPage:true});}
  await page.setViewportSize({width:1440,height:1100});await previewButton().click();await dialog().getByRole('heading',{name:'确认前核对'}).waitFor();check((await dialog().locator('.preview-box').innerText()).includes('再投份额 16'),'preview includes estimated quantity');
  await dialog().getByRole('button',{name:'确认记账',exact:true}).click();await dialog().waitFor({state:'hidden'});
  const saved=(await api('/portfolios/'+p.id+'/dashboard')).events.find(e=>e.kind==='REINVEST');check(saved.reinvest_quantity==='16'&&saved.reinvest_gross_amount==='20'&&saved.fee==='1'&&saved.net_amount==='21','saved quantity agrees with preview and independent costs');
  await page.getByRole('navigation',{name:'主要页面'}).getByRole('button',{name:'资金与流水',exact:true}).click();await page.locator('[data-ledger-event="'+saved.id+'"]').getByRole('button',{name:'更正',exact:true}).click();await waitValue('16');
  await field('再投净值归属日').fill('2024-06-04');await field('再投总价').fill('30');check(await field('再投实际确认份额').inputValue()==='16','correction preserves recorded shares');await close();
  fixture(b.instrument_id,[quote('2024-07-02','2')],{source_ref:'synthetic-reinvestment',code:'000002',record_date:'2024-07-01',ex_date:'2024-07-02',pay_date:'2024-07-02',per_share:'.1',currency:'CNY',source:'合成分红公告',status:'published'});
  await page.reload();await page.getByRole('button',{name:'查看提醒',exact:true}).click();await page.getByRole('button',{name:'核对分红',exact:true}).click();await page.getByRole('button',{name:'核对现金分红 / 红利再投',exact:true}).click();
  await field('流水类型').selectOption('REINVEST');await waitValue('5');check(await field('再投总价').inputValue()==='10'&&await field('再投净值归属日').inputValue()==='2024-07-02','automatic-dividend proposal fills shares on reinvestment selection');
  await close();check(!errors.length,'no uncaught browser errors');fs.writeFileSync(path.join(out,'reinvestment-results.json'),JSON.stringify({checks:count,errors},null,2));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
