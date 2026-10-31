/* Only synthetic ledgers; native notification responses are explicitly simulated. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876',out=process.env.PORTFOLIO_TEST_OUTPUT;
(async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1480,height:1050}});
 let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(v,label)=>{assert.ok(v,label);console.log('PASS',++checks,label);};
 const api=async(url,body,method=body===undefined?'GET':'POST')=>{const r=await page.request.fetch(base+'/api'+url,{method,data:body,headers:{'X-CSRF-Token':csrf}});assert.ok(r.ok(),await r.text());return r.json();};
 const nav=async(name,group='主要页面')=>{if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('navigation',{name:group,exact:true}).getByRole('button',{name,exact:true}).click();};
 const field=label=>page.getByRole('dialog').locator('label.field').filter({has:page.locator('span').filter({hasText:new RegExp('^'+label)})}).locator('input,select').first();
 const shot=name=>page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 try{
  const health=await api('/health');assert.equal(health.environment,'test');assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);
  const boot=await api('/bootstrap');csrf=boot.csrf_token;
  await api('/fx',{currency:'HKD',date:boot.today,rate:'0.9',mode:'daily'});await api('/fx',{currency:'USD',date:boot.today,rate:'7',mode:'daily'});
  const p=await api('/portfolios',{name:'合成界面验收',planned_amount:'10000',start_date:'2026-01-01'}),events=[];
  for(const [market,code,name,scope,fx] of [['CN','600201','合成权益','A股','1'],['HK','00700','合成港股','港股','0.9'],['US','MSFT','合成美股','美股','7']]){
   const e=await api('/events',{portfolio_id:p.id,kind:'BUY',date:'2026-01-02',instrument:{market,code,name},quantity:'10',gross_amount:'100',fx_rate:fx,holding_labels:{scope,direction:'红利',detail:''},expected_revision:(await api('/bootstrap')).revision});events.push(e);
  }
  const fixture=cp.spawnSync(path.resolve(__dirname,'../.venv/bin/python'),['-c',"import json,os;from pathlib import Path;from backend.environment import claim_environment;from backend.store import Store;from backend.service import Service;d=Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'test');s=Service(Store(d));p=json.load(__import__('sys').stdin);[s.cache_prices(e['instrument_id'],[{'date':'2026-01-02','price':'10','source':'synthetic'},{'date':p['today'],'price':'12','source':'synthetic'}]) for e in p['events']]"],{cwd:path.resolve(__dirname,'..'),encoding:'utf8',input:JSON.stringify({events,today:boot.today})});assert.equal(fixture.status,0,fixture.stderr);
  await api('/events',{portfolio_id:p.id,instrument_id:events[0].instrument_id,kind:'DIVIDEND',date:'2026-03-01',ex_date:'2026-03-01',pay_date:'2026-03-01',gross_amount:'5',tax:'0',tax_known:true,fx_rate:'1'});
  await page.goto(base);await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);
  await page.locator('.summary-cumulative [data-profit-equation]').waitFor();
  check(await page.locator('.summary-facts').getByText(/计划后余量/).count()===0,'overview suppresses redundant plan surplus');
  const caption=page.locator('.page-header .page-caption');await caption.waitFor();
  check(await caption.evaluate(e=>getComputedStyle(e).color==='rgb(146, 153, 163)'),'portfolio caption uses shared light gray');
  await nav('持仓标的');await page.locator('.holding-row').first().waitFor();
  const row=page.locator('.holding-row').filter({hasText:'合成权益'});
  check(!(await page.locator('.holdings-table th').allTextContents()).includes('累计净分红 · CNY')&&(await row.locator('.holding-return').innerText()).includes('累计净分红 5.00 CNY'),'net dividend is merged with return performance');
  check(await row.locator('.holding-actions .button').count()===0,'holding actions are text links');
  await row.getByRole('button',{name:'设置计划',exact:true}).click();await page.getByRole('dialog',{name:'设置计划',exact:true}).waitFor();
  check(await page.getByRole('dialog').getByText('风格 / 主题 · 多选',{exact:true}).count()===0&&await page.getByRole('dialog').getByText('自定义标签',{exact:true}).count()===0,'old style/theme and custom inputs are removed');
  const classes=await field('资产类别').locator('option').allTextContents();
  check(classes.includes('大宗商品')&&await page.getByRole('dialog').getByText('地区',{exact:true}).count()===0,'commodity class exists and mixed region is absent');
  await field('资产类别').selectOption('大宗商品');await page.getByRole('button',{name:'保存计划',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  check((await api('/bootstrap')).allocations.find(a=>a.instrument_id===events[0].instrument_id).asset_class==='大宗商品','classification change saves through the guarded API');
  await row.getByRole('button',{name:'设置计划',exact:true}).click();const drawer=page.getByRole('dialog',{name:'设置计划',exact:true});await drawer.getByLabel('投资范围',{exact:true}).waitFor();
  const box=await drawer.boundingBox();check(Math.abs(box.x+box.width-1480)<2&&await page.locator('.holding-label-edit-row').count()===0,'label editor is a right-side drawer outside the table');
  await shot('holding-label-drawer');await page.keyboard.press('Escape');await drawer.waitFor({state:'hidden'});
  check(await row.getByRole('button',{name:'设置计划',exact:true}).evaluate(e=>document.activeElement===e),'drawer restores focus to its trigger');
  await row.getByRole('button',{name:'合成权益',exact:true}).click();await page.locator('.instrument-profit [data-profit-equation]').waitFor();
  check(await page.locator('.instrument-profit-breakdown').count()===0&&(await page.locator('.instrument-profit').innerText()).includes('现金分红净收益'),'profit calculation is integrated below the single headline');
  check(await page.locator('.instrument-profit .equation-part b').evaluateAll(es=>es.every(e=>getComputedStyle(e).color==='rgb(146, 153, 163)')),'formula values are small neutral gray');await shot('instrument-profit');
  await nav('持仓分析');await page.locator('.holding-sunburst-node').first().waitFor();
  check(await page.locator('.holding-sunburst-node path').evaluateAll(es=>new Set(es.map(e=>e.getAttribute('fill'))).size>=3),'sunburst distinguishes three markets with the muted palette');await shot('sunburst');
  for(const name of ['总览','趋势分析','保险','收入']){await nav(name,'资产分析');await page.locator('.family-intro').waitFor();check(await caption.count()===1&&(await caption.innerText()).includes('资产 ·')&&await page.locator('.family-intro>span').count()===0,name+' has one caption in the title');}
  await nav('保险','资产分析');await page.getByRole('button',{name:'新增记录',exact:true}).click();await field('保单名称').fill('合成终身保单');await field('保单现金价值').fill('100');await field('保障期限').selectOption('lifetime');
  check(await page.getByRole('dialog').locator('input[type=date]').count()===0,'lifetime term does not use an artificial expiry date');
  await shot('insurance-lifetime');await page.getByRole('button',{name:'检查并继续',exact:true}).click();await page.getByRole('button',{name:'确认保存',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByText('保障期限：终身',{exact:true}).waitFor();const family=await api('/family/overview'),policy=family.items.find(r=>r.name==='合成终身保单');
  check(policy.policy_lifetime===true&&policy.policy_end===null,'lifetime insurance saves and reads back explicitly');
  const failure={enabled:true,supported:true,authorization:'authorized',pending_count:1,missing_count:1,environment:'test',error:'1 条提醒未成功排入系统通知队列',missing_requests:[{id:'synthetic-notification',item_id:policy.id,name:policy.name,kind:'premium',due_date:'2026-11-08',at:'2026-11-01T09:00:00+08:00',reason:'合成系统排程失败',error_domain:'SyntheticError',error_code:7}]};let status=failure,retried=false;
  await page.route('**/api/notifications',async route=>{if(route.request().method()==='POST'){assert.equal(route.request().postDataJSON().action,'refresh');retried=true;status={...failure,error:null,missing_count:0,missing_requests:[]};}await route.fulfill({json:status});});
  await nav('全局设置','全局页面');await page.locator('.notification-issues').waitFor();
  check((await page.locator('.notification-issues').innerText()).includes(policy.name)&&(await page.locator('.notification-issues').innerText()).includes('2026-11-01 09:00')&&(await page.locator('.notification-issues').innerText()).includes('SyntheticError / 7'),'notice identifies the plan, exact time and OS reason');await shot('notification-details');
  await page.getByRole('button',{name:'查看相关计划',exact:true}).click();await page.getByRole('heading',{name:policy.name,exact:true}).waitFor();check(true,'notice opens its exact asset plan');
  await nav('全局设置','全局页面');await page.getByRole('button',{name:'重试排程',exact:true}).click();await page.locator('.notification-issues').waitFor({state:'hidden'});check(retried,'retry clears resolved notification issues using simulated queue evidence');
  for(const width of [390,320]){await page.setViewportSize({width,height:1000});await nav('持仓标的');await page.locator('.holding-row').first().waitFor();await row.getByRole('button',{name:'设置计划',exact:true}).click();await drawer.getByLabel('投资范围',{exact:true}).waitFor();check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'drawer fits '+width);await shot('drawer-'+width);await page.keyboard.press('Escape');await nav('持仓分析');await page.locator('.holding-sunburst-node').first().waitFor();check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'sunburst fits '+width);await shot('sunburst-'+width);}
  check(errors.length===0,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors,notification_evidence:'simulated',author:'Leo',date:boot.today},null,2));
 }catch(e){await shot('failure').catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
