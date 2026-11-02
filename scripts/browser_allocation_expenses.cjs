/* Real browser acceptance; all writes use a fresh, explicitly marked synthetic ledger. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base=process.env.PORTFOLIO_TEST_URL;
const out=path.join(process.env.PORTFOLIO_TEST_OUTPUT,'allocation-expenses-results');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await pw.chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1100}});
 page.setDefaultTimeout(20000);let csrf='',checks=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=(v,m)=>{assert.ok(v,m);console.log('PASS',++checks,m);};
 async function api(url,body,method=body===undefined?'GET':'POST',status=200){const r=await page.request.fetch(base+'/api'+url,{method,data:body,headers:{origin:base,'x-csrf-token':csrf}}),v=await r.json();assert.equal(r.status(),status,JSON.stringify(v));return v;}
 const revision=async()=>(await api('/bootstrap')).revision;
 const command=async(operation,payload,record_id=null)=>api('/family/commands',{operation,payload,record_id,expected_revision:await revision()});
 const dialog=page.getByRole('dialog'),field=name=>dialog.locator('label.field').filter({has:page.locator('span').filter({hasText:name})}).locator('input,select,textarea').first();
 const nav=async name=>{if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('navigation',{name:'主要页面',exact:true}).getByRole('button',{name,exact:true}).click();};
 const cashNav=async()=>{if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('navigation',{name:'资产分析',exact:true}).getByRole('button',{name:'收入/开支',exact:true}).click();await page.locator('.cashflow-months tbody tr').first().waitFor();};
 const shot=async name=>{await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1&&Array.from(document.querySelectorAll('.recharts-wrapper')).every(e=>e.getBoundingClientRect().right<=innerWidth+1)&&(innerWidth>760||document.querySelector('.sidebar').getBoundingClientRect().right<=0));return page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});};
 const ready=async()=>{await page.locator('.allocation-table').waitFor();await page.locator('.allocation-analysis .table-wrap[aria-busy="false"]').waitFor();};
 try{
  const h=await api('/health');assert.equal(h.environment,'test');assert.equal(path.resolve(h.data_directory),path.resolve(process.env.PORTFOLIO_TEST_DATA_DIR));
  const boot=await api('/bootstrap');csrf=boot.csrf_token;const year=Number(boot.today.slice(0,4)),at=year+'-01';
  const p=await api('/portfolios',{name:'合成多视角配置',planned_amount:'100000',start_date:year+'-01-01'});
  const plans=[];
  for(const [code,name,pct,scope,direction,detail,amount] of [
   ['000501','合成中证500基金','10','A股','宽基','中证500','6000'],
   ['000502','合成消费基金','12','A股','行业','消费','14000'],
   ['000503','合成港股基金','15','港股','宽基','港股宽基','8000'],
   ['000504','合成美股基金','20','美股','宽基','美股宽基','0'],
   ['000505','合成债券基金','15','国内债券','纯债','国债','4000']]){
    const body={instrument:{market:'FUND',code,name},target_pct:pct,asset_class:scope==='国内债券'?'债券':'权益',holding_labels:{scope,direction,detail},expected_revision:await revision()};
    const saved=await api('/portfolios/'+p.id+'/purchase-plans',body);plans.push(saved);
    if(Number(amount))await api('/events',{portfolio_id:p.id,instrument_id:saved.instrument.id,kind:'BUY',date:year+'-01-02',quantity:'100',gross_amount:amount,fx_rate:'1'});
  }
  const fixture=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import json,os;from pathlib import Path;from backend.environment import claim_environment;from backend.service import Service;from backend.store import Store;p=json.load(__import__('sys').stdin);d=Path(os.environ['PORTFOLIO_TEST_DATA_DIR']).resolve();claim_environment(d,'test');s=Service(Store(d));[s.cache_prices(i['instrument']['id'],[dict(date=p['today'],price=str(v),source='synthetic')]) for i,v in zip(p['plans'],[66,126,88,50,42])]"],{cwd:root,encoding:'utf8',input:JSON.stringify({plans,today:boot.today})});assert.equal(fixture.status,0,fixture.stderr);
  const before=await api('/portfolios/'+p.id+'/dashboard?view=summary');
  await page.goto(base+'/?page=overview');await page.getByLabel('投资组合',{exact:true}).selectOption(p.id);await page.locator('.holding-return-rate').waitFor();
  check((await page.locator('.holding-return-rate').innerText()).includes('%'),'current holding profit includes return on remaining cost');
  await shot('overview-return-desktop');await nav('持仓标的');await page.getByRole('button',{name:'添加计划买入',exact:true}).click();
  await field('代码').fill('000506');await field('名称').fill('合成科创50计划基金');await field('计划占总预算').fill('5');
  await dialog.getByLabel('投资范围',{exact:true}).selectOption('A股');await dialog.getByRole('button',{name:'宽基',exact:true}).click();await dialog.getByLabel('细分标签',{exact:true}).fill('科创50');
  await dialog.getByRole('button',{name:'预览计划',exact:true}).click();await dialog.locator('.purchase-plan-preview').waitFor();
  check((await api('/portfolios/'+p.id+'/dashboard?view=summary')).holdings.length===5,'UI preview does not persist plan');
  await shot('purchase-plan-preview');await dialog.getByRole('button',{name:'确认保存计划',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const plannedRow=page.locator('.holding-row').filter({hasText:'合成科创50计划基金'});await plannedRow.waitFor();
  check((await plannedRow.innerText()).includes('计划买入'),'new plan has lightweight planned-buy status');
  check(await plannedRow.locator('.holding-status').evaluate(e=>getComputedStyle(e).borderTopWidth==='0px'&&getComputedStyle(e).fontWeight==='500'),'status badge uses a light label without heavy border');
  const after=await api('/portfolios/'+p.id+'/dashboard?view=summary');
  check(after.cash_total===before.cash_total&&after.total_assets===before.total_assets&&after.capital_summary.pnl===before.capital_summary.pnl,'plan save leaves real cash, asset value and profit unchanged');
  await shot('holdings-plans-desktop');await nav('持仓分析');await page.locator('.holding-sunburst-node').first().waitFor();
  await page.locator('.holding-sunburst-node').first().dispatchEvent('mouseover');
  await page.getByRole('tooltip').waitFor({timeout:500});
  check((await page.getByRole('tooltip').innerText()).includes('计划占预算')&&(await page.getByRole('tooltip').innerText()).includes('已投占预算'),'sunburst tooltip appears without native hover delay and shows plan vs actual');
  check(await page.locator('.holding-sunburst-node title').count()===0,'no delayed native SVG title tooltip');await shot('sunburst-immediate-tooltip');
  await page.getByRole('tab',{name:'配置进度',exact:true}).click();await ready();
  check(await page.locator('[data-allocation-row]').count()===5-1,'first-level groups cover all synthetic exposures');
  const a=page.locator('[data-allocation-row="A股"]');check((await a.innerText()).includes('9,000.00')&&(await a.innerText()).includes('超投 2,000.00'),'group preserves positive gaps and overshoot separately');
  await shot('allocation-scope-desktop');
  for(const [level,count] of [['direction',5],['detail',6],['instrument',6]]){await page.getByLabel('配置查看维度',{exact:true}).selectOption(level);await ready();check(await page.locator('[data-allocation-row]').count()===count,'configuration view '+level+' has correct groups');}
  await page.getByLabel('配置具体标的',{exact:true}).selectOption(plans[0].instrument.id);await ready();check(await page.locator('[data-allocation-row]').count()===1&&(await page.locator('[data-allocation-row]').innerText()).includes('6.00%'),'specific instrument keeps whole-portfolio denominator');
  await page.getByLabel('配置具体标的',{exact:true}).selectOption('');await page.getByLabel('配置投资范围',{exact:true}).selectOption('A股');await ready();check(await page.locator('[data-allocation-row]').count()===3,'scope filter includes held and planned instruments');
  await page.getByLabel('配置进度筛选',{exact:true}).selectOption('over');await ready();check((await page.locator('[data-allocation-row]').innerText()).includes('合成消费基金'),'overshoot filter finds the exact instrument');
  await page.getByLabel('配置进度筛选',{exact:true}).selectOption('planned');await ready();check((await page.locator('[data-allocation-row]').innerText()).includes('合成科创50计划基金'),'planned-only filter includes no-position targets');
  await page.getByLabel('配置进度筛选',{exact:true}).selectOption('all');await page.getByLabel('配置投资范围',{exact:true}).selectOption('');await page.getByLabel('配置查看维度',{exact:true}).selectOption('scope');await ready();
  await page.locator('[data-allocation-row="A股"]').getByRole('button',{name:'查看下一级',exact:true}).click();await ready();check(await page.getByLabel('配置查看维度',{exact:true}).inputValue()==='direction','row drilldown moves to next hierarchy');
  await page.getByRole('navigation',{name:'配置标签路径'}).getByRole('button',{name:'全部配置',exact:true}).click();await ready();
  for(const width of [390,768]){await page.setViewportSize({width,height:950});await shot('allocation-'+width);check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'allocation layout fits viewport '+width);}
  await page.setViewportSize({width:1600,height:1100});
  await command('income',{kind:'salary',month:at,amount:'10000',currency:'CNY'});
  await command('income',{kind:'fund',month:at,amount:'2000',currency:'CNY'});
  await command('income',{kind:'salary',month:year+'-02',amount:'20000',currency:'CNY'});
  await cashNav();await page.getByRole('button',{name:'设置固定开支',exact:true}).click();await field('开支名称').fill('合成固定生活开支');await field('每月金额').fill('3000');await field('生效月份').fill(at);
  await dialog.getByRole('button',{name:'预览计划',exact:true}).click();await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'设置固定开支',exact:true}).click();await dialog.getByRole('button',{name:'编辑',exact:true}).click();await field('每月金额').fill('3500');await field('生效月份').fill(at);await dialog.getByRole('button',{name:'预览计划',exact:true}).click();await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
  check((await api('/family/cashflow?year='+year)).rows[0].planned==='3500','editing a fixed plan appends valid version and refreshes budget');
  await page.locator('.cashflow-months').getByRole('button',{name:at,exact:true}).click();await field('当月额外计划').fill('500');await field('实际开支总额').fill('4000');await dialog.getByRole('checkbox',{name:/本月收入与实际开支已完整核对/}).check();
  await dialog.getByRole('button',{name:'预览月度收支',exact:true}).click();await shot('monthly-expense-preview');await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
  await page.locator('.cashflow-months').getByRole('button',{name:year+'-02',exact:true}).click();await field('实际开支总额').fill('2000');await dialog.getByRole('checkbox',{name:/本月收入与实际开支已完整核对/}).check();await dialog.getByRole('button',{name:'预览月度收支',exact:true}).click();await dialog.getByRole('button',{name:'确认保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
  const cf=await api('/family/cashflow?year='+year);check(cf.rows[0].surplus==='6000'&&cf.summary.expense==='6000'&&cf.summary.savings_rate==='0.8','actual replaces monthly budget; annual rate is aggregate 80%, not average 75%');
  check((await page.locator('.cashflow-summary').innerText()).includes('80.00%'),'rendered annual savings rate matches API');
  await page.getByRole('checkbox',{name:'储蓄率包含公积金收入',exact:true}).check();await page.locator('.cashflow-summary').getByText('81.25%',{exact:true}).waitFor();check(true,'provident-fund switch recalculates same confirmed month set');
  await page.getByRole('checkbox',{name:'储蓄率包含公积金收入',exact:true}).uncheck();await page.locator('.cashflow-summary').getByText('80.00%',{exact:true}).waitFor();
  await shot('cashflow-desktop');for(const width of [390,768]){await page.setViewportSize({width,height:950});await shot('cashflow-'+width);check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'cashflow layout fits viewport '+width);}
  check((await api('/portfolios/'+p.id+'/dashboard?view=summary')).cash_total===before.cash_total,'income and expense records never alter portfolio cash');
  for(const [code,paired] of [['000688','000688CNY01'],['H11136','H01136']]){const r=await api('/research/indices/resolve',{code});check(JSON.stringify(r).includes(paired),'official price/return pair resolves '+code);}
  check(errors.length===0,'no browser runtime errors');fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors,synthetic:true},null,2));
 }catch(e){await shot('failure').catch(()=>{});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
