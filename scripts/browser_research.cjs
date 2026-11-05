/* All requests run against the guarded synthetic provider in the test harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || '../frontend/node_modules/playwright');
const base = process.env.PORTFOLIO_TEST_URL;
const out = process.env.PORTFOLIO_TEST_OUTPUT;

(async () => {
 const browser = await chromium.launch({headless:true});
 const page = await browser.newPage({viewport:{width:1500,height:1050}});
 let csrf = '', checks = 0;
 const errors = [];
 page.on('pageerror', error => errors.push(error.message));
 const check = (value,label) => {assert.ok(value,label);console.log('PASS',++checks,label);};
 const api = async (url,body,method=body===undefined?'GET':'POST') => {
  const response = await page.request.fetch(base+'/api'+url,{method,data:body,headers:{'X-CSRF-Token':csrf}});
  assert.ok(response.ok(),await response.text());
  return response.json();
 };
 const ready = () => page.locator('[data-studio-ready=true]').waitFor({timeout:45000});
 const shot = name => page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});
 const navigate = async name => {
  if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();
  await page.getByRole('button',{name,exact:true}).click();
 };

 try {
  const health = await api('/health');
  assert.equal(health.environment,'test');
  assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);
  csrf = (await api('/bootstrap')).csrf_token;
  await page.goto(base);
  await navigate('指数资料库');
  await page.getByRole('heading',{name:'还没有指数档案',exact:true}).waitFor();
  check((await api('/research/catalog')).subscriptions.length===0,'catalog does not subscribe automatically');
  check(await page.getByRole('button',{name:'本机Token与预算',exact:true}).count()===0,'credential controls removed from index page');

  await navigate('全局设置');
  await page.getByLabel('理杏仁Token',{exact:true}).fill('synthetic-local-browser-credential');
  await page.getByRole('button',{name:'保存到本机文件',exact:true}).click();
  await page.getByText('Token已保存到本机文件；尚未发起采集。',{exact:true}).waitFor();
  check(await page.getByLabel('理杏仁Token',{exact:true}).inputValue()==='','global token entry clears after save');
  check(await page.getByLabel('每端点本机累计上限',{exact:true}).count()===0,'global UI has no budget or enable controls');
  const access = await api('/settings/lixinger');
  check(Object.keys(access.counts).length===0&&!access.automatic,'saving token does not collect or enable automation');
  // Larger synthetic-only test budget; no real credential or supplier is used.
  await api('/settings/lixinger',{expected_revision:access.revision,enabled:false,limit:200,automatic:false},'PUT');

  await navigate('指数资料库');
  await page.getByRole('button',{name:'添加采集',exact:true}).first().click();
  const dialog = page.getByRole('dialog',{name:'添加指数采集'});
  await dialog.getByLabel('采集指数代码',{exact:true}).fill('000905');
  await dialog.getByRole('button',{name:'全历史',exact:true}).click();
  check(await dialog.getByLabel('采集起始日',{exact:true}).count()===0,'full history removes the custom start field');
  check(await dialog.locator('.collection-modules small').count()===(await api('/research/catalog')).modules.length-1,'each offered module names its source');
  for(const name of ['十年历史成分','指数季度/TTM财务','融资与北向持仓','公司估值','公司财务','历史财务备用汇总']){
   await dialog.getByRole('checkbox',{name,exact:true}).check();
  }
  await dialog.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await dialog.getByRole('button',{name:'确认保存采集设置',exact:true}).waitFor();
  check(Object.keys((await api('/settings/lixinger')).counts).length===0,'preview stays offline');
  await shot('studio-collection-preview');
  await dialog.getByRole('button',{name:'确认保存采集设置',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  await page.getByRole('tab',{name:'数据来源',exact:true}).click();
  await page.getByRole('button',{name:'更新当前指数已订阅数据',exact:true}).click();
  await page.getByText('更新任务已受理，完成状态及实际数据日期见下方。',{exact:true}).waitFor();
  let job;
  for(let attempt=0;attempt<180;attempt++){
   job=(await api('/data-updates/jobs')).jobs[0];
   if(job&&['complete','partial','error','interrupted'].includes(job.state))break;
   await page.waitForTimeout(500);
  }
  if(job.state!=='complete')console.error(JSON.stringify({state:job.state,items:job.items}));
  check(job.state==='complete','explicit refresh works with configured token and automation disabled');
  const detail=await api('/research/indices/csi%3A000905?method=mcw&window=3');
  check(detail.index_financials.length>0&&detail.historical_memberships.length===40,'source financials and legacy historical records preserved');
  const counts=(await api('/settings/lixinger')).counts;

  await page.getByRole('tab',{name:'观察台',exact:true}).click();
  await ready();
  check(await page.getByRole('heading',{name:'点位走势',exact:true}).count()===1&&await page.getByRole('heading',{name:'估值走势',exact:true}).count()===1,'price and valuation use separate cards');
  check(await page.getByRole('img',{name:'日收盘与51、120、250、850日均线',exact:true}).locator('.recharts-line').count()===5,'close and all four MAs render');
  const summary=await page.locator('.studio-overview-summary').innerText();
  check(['最近交易日','本周','本月','本年','前十大股票权重'].every(s=>summary.includes(s)),'summary includes all calendar changes and top ten weight');
  check(await page.locator('.studio-change-grid .positive').count()>0,'positive market changes use rise styling');
  check(await page.getByText('同区间收益与成分重叠',{exact:true}).count()===0&&await page.getByRole('button',{name:'历史回测',exact:true}).count()===0,'return backtest and cross-index comparison removed');
  const methods=page.getByLabel('估值方法',{exact:true});
  const initialOrder=await methods.getByRole('button').allTextContents();
  await methods.getByRole('button',{name:'中位数',exact:true}).click();
  await methods.locator('button[aria-pressed=true]').filter({hasText:'中位数'}).waitFor();
  await ready();
  assert.deepEqual(await methods.getByRole('button').allTextContents(),initialOrder);
  check(true,'selected valuation method stays in its original position');
  await page.getByLabel('观察指标',{exact:true}).getByRole('button',{name:'PB',exact:true}).click();
  await page.getByLabel('估值历史窗口',{exact:true}).getByRole('button',{name:'全历史',exact:true}).click();
  await ready();
  check(await page.getByLabel('点位历史窗口',{exact:true}).locator('button[aria-pressed=true]').innerText()==='3年','valuation history window does not change price window');
  const alternate=await api('/research/indices/csi%3A000905?method=median&window=3');
  assert.deepEqual(alternate.daily_series,detail.daily_series);
  check(true,'valuation method cannot change price MA sequence');
  await methods.getByRole('button',{name:'总市值加权',exact:true}).click();
  await methods.locator('button[aria-pressed=true]').filter({hasText:'总市值加权'}).waitFor();
  await ready();
  await page.getByRole('checkbox',{name:'接受固定成分、股数、权重、除数及各成分同比例变化的试算假设',exact:true}).check();
  await page.getByRole('img',{name:'盈利估值条件区间示意图',exact:true}).waitFor();
  check(true,'conditional valuation remains available');
  await shot('studio-overview');
  await page.getByRole('button',{name:'ⓘ 基本资料',exact:true}).hover();
  await page.getByRole('dialog',{name:'指数基本资料',exact:true}).waitFor();
  check(await page.getByRole('tab',{name:'基本资料',exact:true}).count()===0,'basic information lives beside selector without a separate tab');
  await page.mouse.move(1400,900);

  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await page.getByRole('img',{name:'指数财务金额趋势',exact:true}).waitFor();
  check(await page.getByRole('button',{name:'历史调样',exact:true}).count()===0&&await page.getByRole('button',{name:'篮子对照',exact:true}).count()===0,'financial pages are unified');
  for(const [label,suffix] of [['年度历史','12-31'],['一季度历史','03-31'],['半年历史','06-30'],['三季度历史','09-30']]){
   await page.getByRole('button',{name:label,exact:true}).click();
   const table=page.locator('.studio-finance-summary').locator('..').locator('details');
   await table.locator('summary').click();
   const dates=await table.locator('tbody tr td:first-child').allTextContents();
   check(dates.length>0&&dates.every(d=>d.endsWith(suffix)),label+' only compares matching report periods');
   await table.locator('summary').click();
  }
  await shot('studio-finance');
  await page.getByRole('button',{name:'成分公司',exact:true}).click();
  await page.locator('.research-companies').waitFor();
  check(await page.locator('.research-companies tbody tr').count()===2,'company-level detail remains available');
  await page.getByRole('tab',{name:'资金与持仓',exact:true}).click();
  await page.getByText(/最近完整披露期/).first().waitFor();
  check(await page.locator('svg path[fill="#bf4b50"]').count()>0,'positive money flow bars are red');
  await shot('studio-funds');
  assert.deepEqual((await api('/settings/lixinger')).counts,counts);
  check(true,'all reading and display switches consume no supplier quota');

  await page.getByRole('tab',{name:'数据来源',exact:true}).click();
  await page.getByRole('button',{name:'联网核验官方收盘',exact:true}).click();
  await page.getByText('收盘仅核对同日价格身份，容差0.01点。官方PE与理杏仁mcw口径尚未证实可比，仅并列，不据此开启条件试算；PB参考缺项。',{exact:true}).waitFor();
  assert.deepEqual((await api('/settings/lixinger')).counts,counts);
  check(true,'official reference remains separate from paid quota');
  await page.getByLabel('历史来源版本',{exact:true}).selectOption({index:1});
  await page.getByText('查看冻结数据（每组最多显示最后100条）',{exact:true}).waitFor();
  check(true,'immutable older snapshot remains readable');
  await page.getByRole('button',{name:'管理采集',exact:true}).click();
  const manage=page.getByRole('dialog',{name:'管理指数采集'});
  await manage.getByRole('checkbox',{name:'启用此指数；关闭即暂停并保留历史',exact:true}).uncheck();
  await manage.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await manage.getByRole('button',{name:'确认保存采集设置',exact:true}).click();
  await manage.waitFor({state:'hidden'});
  check(!(await api('/research/catalog')).subscriptions[0].subscription.enabled,'pause retains history');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'观察台',exact:true}).click();
  await ready();
  await shot('studio-mobile');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overview has no horizontal overflow');
  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await shot('studio-finance-mobile');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile financial layout fits viewport');
  await navigate('全局设置');
  await shot('studio-token-mobile');
  check(await page.getByLabel('理杏仁Token',{exact:true}).inputValue()==='','token is never refilled');
  check(errors.length===0,'no browser exceptions');
  fs.writeFileSync(path.join(out,'checks.json'),JSON.stringify({checks,errors,synthetic:true},null,2));
 } catch(error) {
  await shot('failure');
  console.error(errors);
  console.error((await page.locator('body').innerText()).slice(-3500));
  throw error;
 } finally {
  await browser.close();
 }
})().catch(error=>{console.error(error);process.exit(1);});
