/* Financial presentation regression. All writes require the synthetic QA ledger. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',out=path.join(process.env.PORTFOLIO_TEST_OUTPUT||path.join(root,'../local/work/browser'),'profit-results');
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}});page.setDefaultTimeout(25000);
 let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(v,m)=>{assert.ok(v,m);console.log('PASS',++checks,m);};
 const api=async(u,data,method=data===undefined?'GET':'POST')=>{const r=await page.request.fetch(base+'/api'+u,{method,data,headers:{'x-csrf-token':csrf,origin:base}});const value=await r.json();assert.ok(r.ok(),JSON.stringify(value));return value;};
 const nav=async name=>{await page.getByRole('navigation',{name:name==='全局设置'?'全局页面':'主要页面'}).getByRole('button',{name,exact:true}).click();await page.locator('.loading').waitFor({state:'hidden'});};
 const money=v=>Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
 const signed=v=>(Number(v)>0?'+':'')+money(v);
 function quote(iid,value,today){const r=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import json,sys;from pathlib import Path;from backend.store import Store;from backend.service import Service;p=json.load(sys.stdin);d=Path(p['directory']).resolve();assert d==Path(__import__('os').environ['PORTFOLIO_TEST_DATA_DIR']).resolve();Service(Store(d)).cache_prices(p['id'],[{'date':'2023-01-02','price':'10'},{'date':p['today'],'price':p['price']}])"],{cwd:root,encoding:'utf8',input:JSON.stringify({directory:path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR||path.join(root,'../local/test/browser-test')),id:iid,price:value,today})});assert.equal(r.status,0,r.stderr);}
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(path.resolve(health.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR||path.join(root,'../local/test/browser-test')));assert.equal(health.version,'0.8.5');
  const boot=await api('/bootstrap');csrf=boot.csrf_token;
  const p=await api('/portfolios',{name:'合成收益分解验收',planned_amount:'100000',start_date:'2023-01-01'});
  await api('/fx',{currency:'HKD',date:'2023-01-01',rate:'1',mode:'daily'});
  const buy=await api('/events',{portfolio_id:p.id,kind:'BUY',date:'2023-01-02',instrument:{market:'HK',code:'02800',name:'合成分红标的'},quantity:'100',gross_amount:'1000',transaction_costs:'10',fx_rate:'.9'});
  quote(buy.instrument_id,'12',boot.today);
  await page.goto(base);await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await page.locator('.page-header .eyebrow').filter({hasText:'合成收益分解验收'}).waitFor();await page.locator('.summary-cumulative').waitFor();
  const expected=['总资产 · CNY','持仓市值 · CNY','累计收益 · CNY','累计收益率','年化收益率（XIRR）'];
  check(JSON.stringify(await page.locator('.summary-main .summary-label').allTextContents())===JSON.stringify(expected),'profit amount is a separate top metric');
  await page.getByRole('button',{name:'记录一笔',exact:true}).click();const dialog=page.getByRole('dialog');
  await dialog.getByLabel(/^流水类型/).selectOption('DIVIDEND');await dialog.getByLabel('除息日 / 暂定入账日',{exact:true}).fill('2023-06-01');
  await dialog.getByLabel(/^当前组合内标的/).selectOption(buy.instrument_id);
  await dialog.getByLabel('分红总金额（税前） · HKD',{exact:true}).fill('8.6234');await dialog.getByLabel('需扣税费 · HKD',{exact:true}).fill('1.5');
  const gross=dialog.getByLabel(/^净分红收益（自动计算） · HKD/);
  check(await gross.inputValue()==='7.1234','net dividend subtracts deductions with exact decimals');
  check(await gross.getAttribute('readonly')!==null&&await gross.evaluate(e=>getComputedStyle(e).backgroundColor==='rgb(243, 245, 248)'),'net amount is read-only and grey');
  await dialog.getByLabel('分红总金额（税前） · HKD',{exact:true}).fill('10.8023');await dialog.getByLabel('需扣税费 · HKD',{exact:true}).fill('1.2345');
  check(await gross.inputValue()==='9.5678','changing gross or deductions recomputes net');
  await dialog.getByLabel('到账日期',{exact:true}).fill('2023-06-03');await dialog.getByLabel(/^到账折算汇率/).fill('.95');
  await dialog.getByLabel(/^除息日折算汇率/).fill('.9');await dialog.getByRole('button',{name:'预览记账结果',exact:true}).click();await dialog.getByRole('heading',{name:'确认前核对'}).waitFor();
  await dialog.getByRole('button',{name:'确认记账',exact:true}).click();await dialog.waitFor({state:'hidden'});await page.locator('.refresh-indicator').waitFor({state:'hidden'});
  let dash=await api('/portfolios/'+p.id+'/dashboard');const div=dash.events.find(e=>e.kind==='DIVIDEND');
  check(div.gross_amount==='10.8023'&&div.tax==='1.2345'&&div.net_amount==='9.5678','preview/save uses the same auto-computed gross');
  // Saving closes the dialog before the refreshed dashboard finishes rendering.
  await page.waitForFunction(value=>document.querySelector('.summary-cumulative>strong')?.textContent===value,signed(dash.capital_summary.profit));
  check(await page.locator('.summary-cumulative>strong').innerText()===signed(dash.capital_summary.profit),'standalone profit matches backend');
  check(await page.locator('.summary-cumulative>strong').evaluate(e=>getComputedStyle(e).color==='rgb(181, 83, 78)'),'positive total profit is red');
  check((await page.locator('.summary-cumulative').innerText()).includes('累计净分红')&&(await page.locator('.summary-cumulative').innerText()).includes('价格收益'),'summary decomposes profit into price and net dividends');
  await page.screenshot({path:path.join(out,'overview.png'),fullPage:true});
  await nav('交易记录');await page.locator('.transaction-table').waitFor();const row=page.locator('[data-ledger-event="'+div.id+'"]');
  const taxText=await row.locator('.dividend-tax-equation').innerText();check(taxText.includes('税前 10.8023 HKD')&&taxText.includes('税额 1.2345 HKD')&&taxText.includes('税后 9.5678 HKD'),'dividend row exposes exact gross tax net and currency');
  const headers=await page.locator('.transaction-table th').allTextContents();
  check(headers.length===8&&headers[5]==='收益表现'&&!headers.includes('年化收益率（XIRR）'),'transaction returns occupy one shared column');
  const actual=(await api('/portfolios/'+p.id+'/transactions')).find(r=>r.event.id===buy.id).actual;
  const pct=v=>v==null?'—':(Number(v)>0?'+':'')+(Number(v)*100).toFixed(2)+'%';
  const buyReturn=page.locator('[data-ledger-event="'+buy.id+'"] .transaction-return');
  check(await buyReturn.locator('.transaction-return-main b').innerText()==='累计 '+pct(actual.return_on_cost)&&(await buyReturn.locator('.transaction-return-main').innerText()).includes(signed(actual.profit)+' CNY'),'first line preserves backend cumulative rate and CNY profit');
  check((await buyReturn.locator('.transaction-return-secondary').innerText()).includes('年化（XIRR） '+pct(actual.xirr)+' · 持有中')&&await buyReturn.locator('.transaction-return-secondary').getAttribute('title')===actual.xirr_status,'second line preserves annualization, status and calculation explanation');
  check((await row.locator('.transaction-return').innerText()).includes('累计 —')&&!(await row.locator('.transaction-return-secondary').innerText()).includes('持有中'),'non-investment records keep unknown returns without an invented holding state');
  const returnText=await buyReturn.innerText();
  await nav('持仓标的');await page.getByRole('button',{name:'合成分红标的',exact:true}).click();await page.locator('.instrument-return-grid').waitFor();
  check(await page.locator('.instrument-return-grid>div').count()===4,'detail groups market value profit return rate and XIRR together');
  const perf=dash.holdings[0].performance,b=perf.profit_breakdown;
  check(await page.locator('.instrument-profit>strong').innerText()===signed(perf.profit),'detail prominently shows total profit');
  const formula=await page.locator('.instrument-profit-breakdown').innerText();check([signed(b.total),signed(b.price_net),signed(b.dividends_net)].every(s=>formula.includes(s)),'detail equation includes actual component numbers');
  check((await page.locator('.instrument-summary-facts').innerText()).includes('累计买入成本')&&(await page.locator('.instrument-summary-facts').innerText()).includes('净投入'),'cost and cash recovery remain distinct');
  check(await page.locator('.price-profit-details').getAttribute('open')!==null,'price arithmetic is visible by default');
  await page.locator('.transaction-table').waitFor();check(JSON.stringify(await page.locator('.transaction-table th').allTextContents())===JSON.stringify(headers),'shared tables retain identical headers');
  check(await page.locator('[data-instrument-event="'+div.id+'"] .dividend-tax-equation').innerText()===taxText,'detail and ledger show identical dividend tax equations');
  check(await page.locator('[data-instrument-event="'+buy.id+'"] .transaction-return').innerText()===returnText,'detail and ledger show identical two-line return values');
  await page.screenshot({path:path.join(out,'instrument.png'),fullPage:true});
  for(const width of [1024,390,320]){await page.setViewportSize({width,height:1000});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'detail profit layout fits '+width);await page.screenshot({path:path.join(out,'detail-'+width+'.png'),fullPage:true});}
  quote(buy.instrument_id,'8',boot.today);await page.reload();await nav('组合收益');await page.locator('.summary-cumulative>strong').waitFor();
  check(await page.locator('.summary-cumulative>strong').evaluate(e=>getComputedStyle(e).color==='rgb(41, 123, 110)'),'negative total profit is green');
  await nav('交易记录');await page.locator('[data-ledger-event="'+div.id+'"]').getByRole('button',{name:'更正',exact:true}).click();
  check(await page.getByRole('dialog').getByLabel(/^净分红收益（自动计算） · HKD/).inputValue()==='9.5678','editing preserves original tax and net precision');
  check(!errors.length,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2));
 }catch(e){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
