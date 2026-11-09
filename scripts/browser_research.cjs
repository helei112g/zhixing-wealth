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
 const navigate = async (name, area) => {
  if(page.viewportSize().width<=760)await page.getByRole('button',{name:'打开导航',exact:true}).click();
  const root=area?page.getByRole('navigation',{name:area,exact:true}):page;
  await root.getByRole('button',{name,exact:true}).click();
 };

 try {
  const health = await api('/health');
  assert.equal(health.environment,'dev');assert.equal(health.synthetic_fixture,true);
  assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);
  csrf = (await api('/bootstrap')).csrf_token;
  await page.goto(base);
  await navigate('指数研究');
  await page.getByRole('heading',{name:'还没有指数档案',exact:true}).waitFor();
  check((await api('/research/catalog')).subscriptions.length===0,'catalog does not subscribe automatically');
  check(await page.getByRole('button',{name:'添加采集',exact:true}).count()===1,'index library has one add entry');
  check(await page.getByRole('button',{name:'本机Token与预算',exact:true}).count()===0,'credential controls removed from index page');

  await navigate('数据管理','研究工具');
  check(await page.getByRole('button',{name:'添加采集',exact:true}).count()===0,'research data management has no add entry');
  check(await page.getByRole('button',{name:'更新全部指数',exact:true}).isDisabled(),'batch update is disabled with no enabled indices');
  await page.getByLabel('理杏仁Token',{exact:true}).fill('synthetic-local-browser-credential');
  await page.getByRole('button',{name:'保存到本机文件',exact:true}).click();
  await page.getByText('Token已保存到本机文件；尚未发起采集。',{exact:true}).waitFor();
  check(await page.getByLabel('理杏仁Token',{exact:true}).inputValue()==='','global token entry clears after save');
  check(await page.getByLabel('每端点本机累计上限',{exact:true}).count()===0,'global UI has no budget or enable controls');
  const access = await api('/settings/lixinger');
  check(Object.keys(access.counts).length===0&&!access.automatic,'saving token does not collect or enable automation');
  check(access.limit===null&&access.endpoints.every(row=>row.limit===null&&row.remaining===null),'local request counts have no cap');

  await navigate('指数研究');
  await page.getByRole('button',{name:'添加采集',exact:true}).first().click();
  const dialog = page.getByRole('dialog',{name:'添加指数采集'});
  await dialog.getByLabel('采集指数代码',{exact:true}).fill('INVALID');
  await dialog.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await dialog.getByText('理杏仁未返回该代码的唯一指数档案').waitFor();
  check(await dialog.getByRole('button',{name:'确认保存采集设置',exact:true}).count()===0,'unrecognized index cannot be saved');
  await dialog.getByLabel('采集指数代码',{exact:true}).fill('SYNIDX1');
  await dialog.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await dialog.getByRole('heading',{name:'合成自定义指数 · SYNIDX1'}).waitFor();
  check((await dialog.locator('.collection-preview').innerText()).includes('已核验理杏仁价格日线'),'custom index shows supplier-verified price date');
  const countsBeforeKnown=(await api('/settings/lixinger')).counts;
  await dialog.getByLabel('采集指数代码',{exact:true}).fill('000905');
  await dialog.getByRole('button',{name:'全历史',exact:true}).click();
  check(await dialog.getByLabel('采集起始日',{exact:true}).count()===0,'full history removes the custom start field');
  check(await dialog.locator('.collection-modules small').count()===(await api('/research/catalog')).modules.length,'each offered module names its source');
  check(await dialog.locator('.collection-modules input').evaluateAll(inputs=>inputs.every(input=>input.checked)),'every collection module is selected by default');
  await dialog.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await dialog.getByRole('button',{name:'确认保存采集设置',exact:true}).waitFor();
  const previewCounts=(await api('/settings/lixinger')).counts;
  check(previewCounts['cn/index']===countsBeforeKnown['cn/index']+1&&
    previewCounts['cn/index/candlestick']===countsBeforeKnown['cn/index/candlestick']+1,
    'new preview checks identity and price with the supplier');
  check((await api('/research/catalog')).subscriptions.length===0,'preview leaves subscriptions unchanged');
  const previewHeaders=await dialog.locator('.collection-preview th').allTextContents();
  check(JSON.stringify(previewHeaders)===JSON.stringify(['端点','预计首次调用']),'preview shows estimates without local quota');
  check(!(await dialog.innerText()).includes('完整回填超过当前预算'),'preview has no obsolete budget warning');
  await dialog.locator('.collection-preview').scrollIntoViewIfNeeded();
  await shot('studio-collection-preview');
  await dialog.getByRole('button',{name:'确认保存采集设置',exact:true}).click();
  await dialog.waitFor({state:'hidden'});
  check(!(await api('/research/catalog')).modules.includes('tracking_funds'),'catalog omits fund-code collection');
  const backToList=page.getByRole('button',{name:'返回指数列表',exact:true});
  await page.getByRole('heading',{name:/^中证500\s+000905$/}).waitFor();
  check(await backToList.evaluate(button=>button.matches('.text-button.back-link'))&&
   await page.getByRole('combobox',{name:'观察指数',exact:true}).count()===0,
   'index detail uses the shared text back link and has no index selector');
  check(await page.getByRole('button',{name:'添加采集',exact:true}).count()===1,'saving the subscription stays in the index library');
  await page.getByText('首次全量任务已受理。',{exact:false}).waitFor();
  await page.locator('main').getByRole('button',{name:'数据管理',exact:true}).click();
  let job;
  for(let attempt=0;attempt<180;attempt++){
   job=(await api('/data-updates/jobs')).jobs[0];
   if(job&&['complete','partial','error','interrupted'].includes(job.state))break;
   await page.waitForTimeout(500);
  }
  if(job.state!=='complete')console.error(JSON.stringify({state:job.state,items:job.items}));
  check(job.state==='complete'&&job.trigger==='index_create'&&job.resources.every(resource=>resource.full),
    'saving starts one full sync with the configured token');
  const detail=await api('/research/indices/csi%3A000905?method=mcw&window=3');
  check(detail.index_financials.length>0&&detail.historical_memberships.length===40,'source financials and legacy historical records preserved');
  const counts=(await api('/settings/lixinger')).counts;
  check(Object.values(counts).some(value=>value>20),'full history collection continues past the former default cap');

  await navigate('指数研究');
  const listing=page.getByRole('table',{name:'指数列表',exact:true});
  const indexLink=listing.getByRole('button',{name:'中证500',exact:true});
  await indexLink.waitFor();
  const indexListData=await api('/research/indices?method=mcw');
  const year=indexListData.calendar_year;
  check(JSON.stringify(await listing.getByRole('columnheader').allTextContents())===JSON.stringify([
   '指数','PE TTM','PE 分位','PB','PB 分位',(year-1)+'年',(year-2)+'年',(year-3)+'年','今年','本月','上一个交易日','操作',
  ]),'index library opens on the list with the requested periods');
  check(indexListData.items[0].valuation.pe.date===detail.valuation.pe.date&&
   await page.getByRole('heading',{name:'点位走势',exact:true}).count()===0,'index list and detail share the latest valuation snapshot');
  const indexRow = listing.getByRole('row').filter({has:page.getByRole('button',{name:'中证500',exact:true})});
  const valuationCells = await indexRow.getByRole('cell').allTextContents();
  for (const [i, metric] of ['pe','pb'].entries()) {
   const valuation = detail.valuation[metric];
   const expected = [5,10].map(years=>Math.round(Number(valuation.windows[years].rank)*100)+'%').join(' / ');
   check(valuation.field.endsWith('.mcw') && [5,10].every(years=>valuation.windows[years].rank!=null) &&
    valuationCells[2*i+1]===expected, metric+' renders the 5/10-year market-cap weighted ranks');
  }
  await shot('index-library-list');
  await indexLink.click();
  await ready();
  check(await page.getByRole('heading',{name:'点位走势',exact:true}).count()===1&&await page.getByRole('heading',{name:'估值走势',exact:true}).count()===1,'price and valuation use separate cards');
  check(await page.locator('.studio-valuation-panel').evaluate(node=>Boolean(node.compareDocumentPosition(document.querySelector('.studio-price-panel'))&Node.DOCUMENT_POSITION_FOLLOWING)),'valuation card appears before price card');
  const historyPanel=page.locator('.studio-price-panel');
  const volatility=historyPanel.getByRole('img',{name:'250日年化波动率图',exact:true});
  await volatility.locator('.recharts-line-curve').waitFor();
  check(detail.sources.some(source=>source.module==='volatility') &&
    detail.daily_series.some(row=>row.volatility!=null) && await volatility.isVisible(),
    'Lixinger volatility renders in the linked price history with its own source');
  check(await historyPanel.getByRole('img',{name:'水下回撤图',exact:true}).count()===1 &&
    await volatility.evaluate(node=>Boolean(node.compareDocumentPosition(
      node.closest('.linked-history').querySelector('[aria-label="水下回撤图"]'))&Node.DOCUMENT_POSITION_PRECEDING)),
    'volatility appears below drawdown in the same history panel');
  const currentRanks=page.getByLabel('当前估值历史分位',{exact:true});
  const valuation=await api('/research/indices/csi%3A000905/studio?method=mcw&window=3&metric=pe');
  for(const years of [3,5,10]){
   const row=currentRanks.locator(':scope > div').filter({has:page.getByText(years+'年分位',{exact:true})});
   const expected=(Number(valuation.ranks[years].rank)*100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})+'%';
   check(await row.isVisible()&&(await row.locator('strong').innerText()).startsWith(expected),years+' year current percentile is visible without expanding details');
  }

  const valuationPanel=page.locator('.studio-valuation-panel');
  const valuationChart=page.locator('.studio-valuation-chart');
  const chartNode=await valuationChart.locator('svg.recharts-surface[role=application]').elementHandle();
  const previousHeight=(await valuationPanel.boundingBox()).height;
  let releaseWindow;
  const delayedWindow=new Promise(resolve=>{releaseWindow=resolve;});
  const studioRoute='**/api/research/indices/*/studio?*';
  await page.route(studioRoute,async route=>{
   if(new URL(route.request().url()).searchParams.get('window')==='5')await delayedWindow;
   await route.continue();
  });
  await page.getByLabel('估值历史窗口',{exact:true}).getByRole('button',{name:'5年',exact:true}).click();
  await page.getByText('正在读取5年，暂显示3年曲线…',{exact:true}).waitFor();
  check(await chartNode.evaluate(node=>node.isConnected)&&Math.abs((await valuationPanel.boundingBox()).height-previousHeight)<1,'delayed window update keeps the same chart mounted and card height stable');
  await page.getByLabel('估值历史窗口',{exact:true}).getByRole('button',{name:'10年',exact:true}).click();
  await ready();
  const lateResponse=page.waitForResponse(response=>response.url().includes('/studio?')&&new URL(response.url()).searchParams.get('window')==='5');
  releaseWindow();
  await lateResponse;
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  check(await valuationChart.getAttribute('data-valuation-window')==='10','late response cannot replace the latest selected window');
  await page.unroute(studioRoute);

  await page.route(studioRoute,route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'合成读取失败'})}));
  await page.getByLabel('估值历史窗口',{exact:true}).getByRole('button',{name:'5年',exact:true}).click();
  await page.getByText('未完成切换，仍显示10年曲线。',{exact:true}).waitFor();
  check(await valuationChart.getAttribute('data-valuation-window')==='10'&&await chartNode.evaluate(node=>node.isConnected),'failed window request keeps the previous curve and identifies its window');
  await page.unroute(studioRoute);
  await page.getByLabel('估值历史窗口',{exact:true}).getByRole('button',{name:'3年',exact:true}).click();
  await ready();
  check(await page.getByRole('img',{name:'日收盘与51、120、250、850日均线',exact:true}).locator('.recharts-line').count()===5,'close and all four MAs render');
  const summary=await page.locator('.studio-overview-summary').innerText();
  check(['最近交易日','本周','本月','本年','前十大股票权重'].every(s=>summary.includes(s)),'summary includes all calendar changes and top ten weight');
  check(await page.locator('.studio-change-grid .positive').count()>0,'positive market changes use rise styling');
  check(await page.getByText('同区间收益与成分重叠',{exact:true}).count()===0&&await page.getByRole('button',{name:'历史回测',exact:true}).count()===0,'return backtest and cross-index comparison removed');
  const methods=page.getByLabel('估值方法',{exact:true});
  const initialOrder=await methods.getByRole('button').allTextContents();
  const selectedBeforeHelp=await methods.locator('button[aria-pressed=true]').innerText();
  const chartBeforeHelp=await valuationChart.boundingBox();
  let helpRequests=0;
  const countHelpRequests=request=>{
   const pathname=new URL(request.url()).pathname;
   // The page already polls its local revision; help must not request analysis or collection.
   if(pathname.startsWith('/api/research/')&&pathname!=='/api/research/revision')helpRequests++;
  };
  page.on('request',countHelpRequests);
  for(const [label,purpose] of [['总市值加权','看经济规模较大的资产如何定价。'],['等权','看等额组合与规模加权组合有什么差异。'],['中位数','看估值是否扩散到典型公司。']]){
   await methods.getByRole('button',{name:label+'计算说明',exact:true}).hover();
   const tooltip=methods.getByRole('tooltip');
   await tooltip.waitFor();
   check((await tooltip.innerText()).includes(purpose),label+' explains its valuation perspective on hover');
   await tooltip.hover();
   assert.ok(await tooltip.isVisible(),'pointer can move into the explanation');
   await page.getByRole('heading',{name:'估值走势',exact:true}).hover();
   await tooltip.waitFor({state:'hidden'});
  }
  const equalWeightHelp=methods.getByRole('button',{name:'等权计算说明',exact:true});
  await equalWeightHelp.focus();
  await methods.getByRole('tooltip').waitFor();
  await equalWeightHelp.press('Escape');
  check(await methods.getByRole('tooltip').count()===0,'keyboard focus opens method help and Escape dismisses it');
  await equalWeightHelp.click();
  await methods.getByRole('tooltip').waitFor();
  assert.deepEqual(await valuationChart.boundingBox(),chartBeforeHelp);
  check(await methods.locator('button[aria-pressed=true]').innerText()===selectedBeforeHelp&&helpRequests===0,'method explanations preserve selection and chart layout without analysis or collection requests');
  page.off('request',countHelpRequests);
  await equalWeightHelp.press('Escape');
  await methods.getByRole('button',{name:'中位数',exact:true}).click();
  await methods.locator('button[aria-pressed=true]').filter({hasText:'中位数'}).waitFor();
  await ready();
  assert.deepEqual(await methods.getByRole('button').allTextContents(),initialOrder);
  check(true,'selected valuation method stays in its original position');
  let releaseMetric;
  const delayedMetric=new Promise(resolve=>{releaseMetric=resolve;});
  await page.route(studioRoute,async route=>{await delayedMetric;await route.continue();});
  await page.getByLabel('观察指标',{exact:true}).getByRole('button',{name:'PB',exact:true}).click();
  await page.getByText('正在读取估值分析…',{exact:true}).waitFor();
  check(await valuationChart.locator('svg.recharts-surface[role=application]').count()===0&&(await valuationChart.boundingBox()).height>=350,'metric change never relabels the previous metric curve and preserves chart space');
  releaseMetric();
  await ready();
  await page.unroute(studioRoute);
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
  const identity=page.locator('.research-index-identity');
  const info=identity.getByRole('button',{name:'ⓘ 基本资料',exact:true});
  const infoDialog=page.getByRole('dialog',{name:'指数基本资料',exact:true});
  await backToList.scrollIntoViewIfNeeded();
  await info.hover();
  await infoDialog.waitFor();
  check(await identity.getByRole('heading',{name:/^中证500\s+000905$/}).isVisible()&&
   await infoDialog.getByText('价格 / 全收益代码',{exact:true}).isVisible(),
   'basic information opens beside the index name and code');
  await page.screenshot({path:path.join(out,'index-detail-header.png'),animations:'disabled'});
  await page.mouse.move(1400,900);

  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await page.getByRole('img',{name:'指数财务金额趋势',exact:true}).waitFor();
  check(await page.locator('.research-weight').evaluateAll(rows=>rows.length>0&&rows.every(row=>row.clientWidth>300&&row.querySelector('.industry-name').scrollWidth<=row.querySelector('.industry-name').clientWidth+1)),'industry rows leave readable space for full names and weights');
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
  const fundFixture=await api('/research/indices/csi%3A000905/studio?method=mcw&window=3');
  fundFixture.funds.etf=[
   {date:'2024-01-31',stock:null,flow:null,change:null,complete:false},
   {date:'2024-02-29',stock:'0',flow:'0',change:null,complete:true},
   {date:'2024-03-29',stock:'100000000',flow:null,change:null,complete:false},
   {date:'2024-04-30',stock:'100000000',flow:'0',change:'0',complete:true},
   {date:'2024-05-31',stock:null,flow:null,change:null,complete:false},
   {date:'2024-06-28',stock:'200000000',flow:'-10000',change:null,complete:true},
   {date:'2024-07-31',stock:null,flow:null,change:null,complete:false},
  ];
  await page.route(studioRoute,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fundFixture)}));
  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await page.getByRole('tab',{name:'资金与持仓',exact:true}).click();
  await page.locator('.fund-zero-prefix').waitFor();
  const stockChart=page.getByRole('img',{name:'A股场内基金资产规模',exact:true});
  const flowChart=page.getByRole('img',{name:'场内基金认购净流入',exact:true});
  check(await stockChart.getAttribute('data-start')==='2024-03-29'&&await flowChart.getAttribute('data-start')==='2024-04-30','fund charts trim the zero prefix and their own missing boundaries');
  check(await flowChart.getAttribute('data-end')==='2024-06-28'&&(await flowChart.innerText()).includes('2024-05-31'),'fund charts preserve later zero observations and interior missing periods');
  await page.locator('.fund-observations summary').click();
  const originalRows=page.locator('.fund-observations tbody tr');
  check(await originalRows.count()===5&&(await originalRows.filter({hasText:'2024-02-29'}).innerText()).includes('0.00'),'original zero records remain in the detail table');
  await page.getByRole('button',{name:'融资',exact:true}).click();
  await page.getByRole('button',{name:'融资偿还',exact:true}).click();
  check(await page.getByRole('img',{name:'融资偿还',exact:true}).isVisible(),'financing repayment is selectable');
  await page.getByRole('button',{name:'融券',exact:true}).click();
  check(await page.getByRole('img',{name:'融券余额占流通市值比',exact:true}).isVisible(),'securities balance and ratio are visible');
  await page.getByRole('button',{name:'北向持仓',exact:true}).click();
  check(await page.getByRole('img',{name:'北向持仓占市值比',exact:true}).isVisible(),'northbound holding ratio is visible');
  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await page.getByRole('button',{name:'ROE',exact:true}).click();
  const ratioChart=page.getByRole('img',{name:'指数财务比率趋势',exact:true});
  await ratioChart.locator('.recharts-line-curve').waitFor({state:'attached'});
  check(await page.locator('.studio-finance-summary').innerText().then(text=>text.includes('百分点')),
   'ROE uses a percentage scale and year-over-year percentage points');
  await page.getByRole('button',{name:'商誉',exact:true}).click();
  check(await page.getByRole('img',{name:'指数财务金额趋势',exact:true}).isVisible(),'goodwill uses the amount scale');
  await page.unroute(studioRoute);
  assert.deepEqual((await api('/settings/lixinger')).counts,counts);
  check(true,'all reading and display switches consume no supplier quota');

  await page.locator('main').getByRole('button',{name:'数据管理',exact:true}).click();
  await page.getByText('来源证据、采集进度与历史版本',{exact:true}).click();
  await page.getByRole('button',{name:'联网核验官方收盘',exact:true}).click();
  await page.getByText('收盘仅核对同日价格身份，容差0.01点。官方PE与理杏仁mcw口径尚未证实可比，仅并列，不据此开启条件试算；PB参考缺项。',{exact:true}).waitFor();
  assert.deepEqual((await api('/settings/lixinger')).counts,counts);
  check(true,'official reference remains separate from paid quota');
  await page.getByLabel('历史来源版本',{exact:true}).selectOption({index:1});
  await page.getByText('查看冻结数据（每组最多显示最后100条）',{exact:true}).waitFor();
  check(true,'immutable older snapshot remains readable');
  check(await page.getByRole('columnheader',{name:'最新数据日期',exact:true}).isVisible(),'index source table names observation dates');
  check(await page.getByRole('row').filter({hasText:'指数基本信息'}).getByText(/^核对日 /).isVisible(),
   'basic index profile displays the verification date explicitly');
  const sourceTable=page.locator('.data-sources-table');
  check(await sourceTable.getByRole('button').count()===0 && await sourceTable.getByRole('columnheader',{name:'操作'}).count()===0,
   'index source table only displays data and leaves update controls above it');
  await page.getByText('来源证据、采集进度与历史版本',{exact:true}).click();
  const allIndex=page.getByRole('group',{name:'中证500全部已订阅数据',exact:true});
  await allIndex.getByRole('button',{name:'全量校准',exact:true}).click();
  let full;
  for(let attempt=0;attempt<180;attempt++){
   full=(await api('/data-updates/jobs')).jobs[0];
   if(full?.id!==job.id&&full?.state==='complete')break;
   await page.waitForTimeout(500);
  }
  check(full?.state==='complete' && full.resources.every(resource=>resource.target==='csi:000905'&&resource.full),
   'index full reconciliation rereads the selected subscription only');
  const shared=(await api('/market-waterlevel')).sources.find(source=>source.index_id==='csi:000905');
  check(shared.snapshot_id===full.items.find(item=>item.module==='valuations').snapshot_id,
   'market reads the exact snapshot published from the index library');
  await shot('index-manual-updates');
  await navigate('指数研究');
  await indexLink.click();
  await ready();
  await backToList.click();
  const update=indexLink.locator('xpath=ancestor::tr').getByRole('button',{name:'更新数据',exact:true});
  const submitted=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/data-updates/run'));
  await update.click();
  const rowJob=await (await submitted).json();
  await listing.getByRole('status').filter({hasText:/已完成|部分失败/}).waitFor({timeout:45000});
  const rowResult=await api('/data-updates/jobs/'+rowJob.job_id);
  check(rowResult.resources.length>0&&rowResult.resources.every(item=>item.target==='csi:000905'&&!item.full),
   'row update refreshes only the selected index and its subscribed resources');
  await indexLink.click();
  await ready();
  await backToList.click();
  await listing.getByRole('status').filter({hasText:/已完成|部分失败/}).waitFor();
  check(true,'returning from detail preserves the row update result');
  if(rowResult.state==='partial'){
   check(await listing.getByRole('button',{name:'查看结果',exact:true}).count()===1,
    'partial refresh exposes the detailed results');
  }
  await navigate('数据管理','研究工具');
  await page.getByLabel('数据管理指数',{exact:true}).selectOption('csi:000905');
  await page.getByRole('button',{name:'管理采集',exact:true}).click();
  const manage=page.getByRole('dialog',{name:'管理指数采集'});
  await manage.getByRole('checkbox',{name:'启用此指数；关闭即暂停并保留历史',exact:true}).uncheck();
  await manage.getByRole('button',{name:'预览范围与调用量',exact:true}).click();
  await manage.getByRole('button',{name:'确认保存采集设置',exact:true}).click();
  await manage.waitFor({state:'hidden'});
  check(!(await api('/research/catalog')).subscriptions[0].subscription.enabled,'pause retains history');
  await page.setViewportSize({width:390,height:844});
  await navigate('指数研究');
  await indexLink.waitFor();
  check(await listing.getByRole('button',{name:'更新数据',exact:true}).isDisabled(),'paused index update remains disabled');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile list scrolls within the table');
  await shot('index-library-mobile');
  await indexLink.click();
  await ready();
  await shot('studio-mobile');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overview has no horizontal overflow');
  await info.click();
  await infoDialog.waitFor();
  const infoBounds=await infoDialog.boundingBox();
  check(infoBounds.x>=0&&infoBounds.x+infoBounds.width<=390,'basic information fits the mobile viewport');
  await page.screenshot({path:path.join(out,'index-detail-header-mobile.png'),animations:'disabled'});
  await info.press('Escape');
  await infoDialog.waitFor({state:'hidden'});
  for(const label of ['总市值加权','等权','中位数']){
   const helpButton=page.getByRole('button',{name:label+'计算说明',exact:true});
   await helpButton.click();
   const tooltip=page.getByRole('tooltip');
   await tooltip.waitFor();
   const bounds=await tooltip.boundingBox();
   assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390,'method explanation fits the mobile viewport');
   await helpButton.press('Escape');
  }
  check(true,'all method explanations open by click and fit a narrow screen');
  await page.getByRole('tab',{name:'成分与财务',exact:true}).click();
  await shot('studio-finance-mobile');
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile financial layout fits viewport');
  await navigate('数据管理','研究工具');
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
