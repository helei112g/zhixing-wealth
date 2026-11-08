/* Synthetic market histories only; every external adapter is replaced by the harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || '../frontend/node_modules/playwright');
const base = process.env.PORTFOLIO_TEST_URL;
const output = process.env.PORTFOLIO_TEST_OUTPUT;
// Match tests.waterlevel_fixtures.ASOF; wall-clock time must not age synthetic data.
const asof = '2026-10-30';

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 1050}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let csrf = '';
  let checks = 0;
  const check = (condition, label) => { assert.ok(condition, label); console.log('PASS', ++checks, label); };
  const api = async (url, body, method = body === undefined ? 'GET' : 'POST') => {
    const response = await page.request.fetch(base + '/api' + url, {method, data: body, headers: {'X-CSRF-Token': csrf}});
    assert.ok(response.ok(), await response.text());
    return response.json();
  };
  const shot = name => page.screenshot({path: path.join(output, name + '.png'), fullPage: true, animations: 'disabled'});
  try {
    const health = await api('/health');
    assert.equal(health.environment,'dev');assert.equal(health.synthetic_fixture,true);
    assert.equal(health.data_directory, process.env.PORTFOLIO_TEST_DATA_DIR);
    const boot = await api('/bootstrap');
    csrf = boot.csrf_token;
    // Deterministic synthetic clock, while the product always follows bootstrap.today.
    for (const endpoint of ['bootstrap', 'family/revision']) {
      await page.route('**/api/' + endpoint, async route => {
        const response = await route.fetch();
        await route.fulfill({response, json: {...await response.json(), today: asof}});
      });
    }
    await page.goto(base);
    await page.getByRole('button', {name: '市场水位', exact: true}).click();
    await page.getByRole('heading', {name: '规则摘要', exact: true}).waitFor();
    await page.waitForFunction(value => document.querySelector('.waterlevel-view-options')?.textContent.includes('截至 ' + value), asof);
    check(await page.getByLabel('研究截止日', {exact: true}).count() === 0 && await page.locator('.waterlevel-toolbar').count() === 0, 'current-date page has no cutoff or snapshot toolbar');
    check(JSON.stringify(await page.getByLabel('全市场曲线窗口').getByRole('button').allTextContents()) === JSON.stringify(['5年','10年','全历史']), 'all three chart windows sit inside the overview valuation card');
    check(boot.portfolios.length === 0, 'market page works without a portfolio');
    check((await api('/data-updates/jobs')).jobs.length === 0, 'opening market page does not fetch data');
    check(await page.locator('.waterlevel-score').count() === 4, 'four waterlevel cards render');
    await shot('waterlevel-initial');

    const openManagement = () => page.locator('main').getByRole('button', {name: '数据管理', exact: true}).click();
    const openWaterlevel = () => page.getByRole('navigation', {name:'研究工具'}).getByRole('button', {name:'市场水位', exact:true}).click();
    const marketSelector = page.getByLabel('数据管理市场指标', {exact:true});
    const target = label => page.getByRole('region', {name:label+'数据来源', exact:true});
    const recentJob = async id => {
      for(let attempt=0;attempt<120;attempt++){
        const job = (await api('/data-updates/jobs')).jobs[0];
        if(job && job.id!==id && ['complete','partial','error'].includes(job.state)) return job;
        await page.waitForTimeout(500);
      }
      throw new Error('synthetic update timed out');
    };
    await openManagement();
    check(await page.getByLabel('启用市场数据自动更新').count() === 0, 'market updates expose only manual controls');
    check(await page.getByRole('button', {name:'增量更新',exact:true}).count() === 0, 'per-item controls appear after selecting an item');
    await marketSelector.selectOption('cpi_yoy');
    await target('CPI同比').getByRole('group', {name:'CPI同比全部来源'}).getByRole('button', {name:'增量更新'}).click();
    const cpi = await recentJob();
    check(cpi.state==='complete' && cpi.resources.length===1 && cpi.resources[0].series==='cpi_yoy',
      'CPI update requests only CPI');
    await target('CPI同比').getByText('2026-10', {exact:true}).waitFor();
    const sourceTable=target('CPI同比').getByRole('table');
    check(await sourceTable.getByRole('button').count()===0 && await sourceTable.getByRole('columnheader',{name:'操作'}).count()===0,
      'market source table only displays data and status');
    await shot('market-cpi-management');
    await marketSelector.selectOption('ppi_yoy');
    await target('PPI同比').getByText('待采集', {exact:true}).waitFor();
    await marketSelector.selectOption('');
    const marketSources=(await api('/market-waterlevel/sources')).items.filter(item=>item.updatable);
    await page.getByRole('button',{name:'更新全部市场指标',exact:true}).click();
    const marketBatch=await recentJob(cpi.id);
    check(marketBatch.state==='complete' && marketBatch.resources.length===marketSources.length &&
      marketBatch.resources.every(resource=>resource.scope==='market' && !resource.full) &&
      marketBatch.resources.some(resource=>resource.series==='ppi_yoy'),
      'market batch updates every available indicator without requiring a selection');

    const pause=await api('/research/collection/preview',{code:'930050',modules:['valuations'],history_start:null});
    await api('/research/collection',{code:'930050',modules:pause.modules,history_start:null,enabled:false,
      expected_research_revision:pause.research_revision},'PUT');
    await page.getByRole('tab',{name:'指数资料',exact:true}).click();
    check(await page.getByLabel('数据管理指数',{exact:true}).inputValue()==='', 'bulk index action needs no selection');
    await page.getByRole('button',{name:'更新全部指数',exact:true}).click();
    const indexBatch=await recentJob(marketBatch.id);
    const enabled=(await api('/research/catalog')).subscriptions.filter(profile=>profile.subscription.enabled).map(profile=>profile.id).sort();
    const targets=[...new Set(indexBatch.resources.map(resource=>resource.target))].sort();
    check(indexBatch.state==='complete' && targets.length>1 && JSON.stringify(targets)===JSON.stringify(enabled) &&
      indexBatch.resources.every(resource=>resource.scope==='valuations' && !resource.full),
      'index batch updates every enabled subscription and skips paused indices');
    await openWaterlevel();
    await page.getByRole('tab', {name: '水位总览', exact: true}).click();
    await page.getByText('有效大类 3/3', {exact: true}).waitFor({timeout: 30000});
    const first = await api('/market-waterlevel?asof=' + asof);
    check(first.scores.categories.equity.included === 7, 'fixed index mcw and full-market median factors enter the score');
    const comparison = page.locator('.waterlevel-comparison').first();
    const comparisonCharts = page.locator('.waterlevel-comparison-chart .recharts-responsive-container');
    await comparisonCharts.first().waitFor({state:'visible'});
    check(await comparisonCharts.count() === 1, 'overview renders one shared valuation chart');
    await page.getByLabel('全市场曲线窗口').getByRole('button', {name:'全历史'}).click();
    await page.waitForFunction(() => [...document.querySelectorAll('.waterlevel-comparison text[orientation="bottom"]')].some(node=>node.textContent.includes('2005')));
    check((await comparison.locator('text[orientation="bottom"]').allTextContents()).join(' ').includes('2005'), 'all-history curve extends back to the available start');
    const curves = await comparison.locator('.recharts-line-curve').count();
    const historicalMean = comparison.getByText('历史平均', {exact:true});
    await historicalMean.click();
    await page.waitForFunction(count => document.querySelector('.waterlevel-comparison')?.querySelectorAll('.recharts-line-curve').length === count, curves - 1);
    await historicalMean.click();
    await page.waitForFunction(count => document.querySelector('.waterlevel-comparison')?.querySelectorAll('.recharts-line-curve').length === count, curves);
    check(true, 'historical mean is plotted and can be toggled in the legend');
    await comparison.getByText('10Y国债收益率倒数', {exact:true}).click();
    await page.waitForFunction(count => document.querySelector('.waterlevel-comparison')?.querySelectorAll('.recharts-line-curve').length === count, curves - 1);
    check(await comparison.locator('.recharts-line-curve').count() === curves - 1, 'legend hides the bond reference curve');
    await comparison.getByText('10Y国债收益率倒数', {exact:true}).click();
    await page.getByLabel('全市场曲线窗口').getByRole('button', {name:'5年',exact:true}).click();
    await page.waitForFunction(() => [...document.querySelectorAll('.waterlevel-comparison text[orientation="bottom"]')].some(node=>node.textContent.includes('2021')));
    check(!(await comparison.locator('text[orientation="bottom"]').allTextContents()).join(' ').includes('2005') &&
          await comparison.locator('.recharts-line-curve').count() === curves, 'window clips display while retaining long-history moving means');
    await page.getByLabel('全市场曲线窗口').getByRole('button', {name:'10年',exact:true}).click();
    const metricControls = comparison.getByRole('button', {name:'PB', exact:true});
    await metricControls.click();
    await comparison.getByText('PB · 正数等权', {exact:true}).waitFor();
    check(await historicalMean.isVisible(), 'PB has its own historical mean');
    check(await page.locator('.waterlevel-comparison-chart .recharts-responsive-container').count() === 1, 'PB switches the existing chart');
    const ewRequest = url => url.pathname === '/api/market-waterlevel' && url.searchParams.get('method') === 'ew';
    let release;
    const responseGate = new Promise(resolve => { release = resolve; });
    await page.route(ewRequest, async route => {
      await responseGate;
      await route.continue();
    });
    const ewButton = comparison.getByRole('button', {name:'等权', exact:true});
    const valuationCurve = comparison.locator('.recharts-line-curve[stroke="#365f91"]');
    const previousPath = await valuationCurve.getAttribute('d');
    try {
      await ewButton.click();
      check(await ewButton.getAttribute('aria-pressed') === 'true', 'requested method highlights before the API responds');
      await comparison.getByText('正在读取等权，当前显示正数等权。', {exact:true}).waitFor();
      check(await comparison.getByText('PB · 正数等权', {exact:true}).isVisible() &&
            await valuationCurve.getAttribute('d') === previousPath, 'pending method preserves the label and curve of the loaded result');
    } finally {
      release();
    }
    await comparison.getByText('PB · 等权', {exact:true}).waitFor();
    await page.waitForFunction(path => document.querySelector('.waterlevel-comparison .recharts-line-curve[stroke="#365f91"]')?.getAttribute('d') !== path, previousPath);
    await page.unroute(ewRequest);
    check(await comparison.getByText('正在读取等权，当前显示正数等权。', {exact:true}).count() === 0, 'successful switch replaces the plotted series and clears the loading status');
    const ew = await api('/market-waterlevel?method=ew&asof=' + asof);
    check(ew.series.find(x=>x.id==='full_market_pb').statistics.current !== first.series.find(x=>x.id==='full_market_pb').statistics.current,
      'overview switches to separately sourced EW data');
    await comparison.getByRole('button', {name:'中位数', exact:true}).click();
    await comparison.getByText('PB · 中位数', {exact:true}).waitFor();
    await comparison.getByRole('button', {name:'总市值加权', exact:true}).click();
    await comparison.getByText('PB · 总市值加权', {exact:true}).waitFor();
    await comparison.getByRole('button', {name:'PE TTM', exact:true}).click();
    await shot('waterlevel-overview');

    await openManagement();
    await marketSelector.selectOption('credit');
    await target('AAA 3Y中票与3Y国债').getByRole('group',{name:'AAA 3Y中票与3Y国债全部来源'}).getByRole('button', {name:'全量校准',exact:true}).click();
    await target('AAA 3Y中票与3Y国债').getByText('合成信用来源失败，用于验收保留旧值与重试', {exact: true}).waitFor();
    const failed = await api('/market-waterlevel?asof=' + asof);
    check(failed.research_revision === first.research_revision, 'failed collection preserves data revision');
    check(failed.scores.categories.credit.included === 0, 'failed source is excluded from score while old observations remain');
    await openWaterlevel();
    await page.getByText('有效大类 2/3', {exact: true}).waitFor();
    check(true, 'fast failure refreshes the rendered score without a data revision change');
    await shot('waterlevel-source-failure');
    await openManagement();
    await marketSelector.selectOption('credit');
    const failedRecord = page.locator('.job-record').filter({hasText: '合成信用来源失败'}).first();
    if (await failedRecord.getAttribute('open') === null) await failedRecord.locator('summary').click();
    await failedRecord.getByRole('button', {name: '重试此项', exact: true}).click();
    await target('AAA 3Y中票与3Y国债').getByText('已取得', {exact: true}).waitFor();
    const retried = (await api('/data-updates/jobs')).jobs[0];
    check(retried.resources.length === 1 && retried.resources[0].group === 'credit' && retried.resources[0].full, 'retry retains the exact failed source group');

    await openWaterlevel();
    await page.getByRole('tab', {name: '股票估值', exact: true}).click();
    check(await page.locator('.waterlevel-comparison-chart').count() === 0, 'stock tab has no duplicate full-market chart');
    await page.getByRole('img', {name:'境内宽基指数 PE 对照',exact:true}).waitFor();
    check(await page.getByLabel('股票估值曲线窗口').getByRole('button',{name:'全历史'}).getAttribute('aria-pressed') === 'true',
      'stock comparison starts at its independent full-history window');
    const mainIndices = page.getByRole('region', {name:'主要指数估值',exact:true});
    const selector = mainIndices.getByLabel('估值方法', {exact:true});
    check(await selector.locator('button[aria-pressed]').count() === 4, 'main index section exposes all four valuation methods');
    await selector.getByRole('button', {name:'等权',exact:true}).click();
    await mainIndices.getByText('当前显示 等权（ew）。', {exact:false}).waitFor();
    const equal = await api('/market-waterlevel?method=ew&asof=' + asof);
    check(JSON.stringify(first.scores) === JSON.stringify(equal.scores), 'method switch leaves fixed scores unchanged');
    check(await mainIndices.getByText('当前值无效', {exact:false}).count() > 0, 'negative ew is shown as invalid rather than replaced');
    check(await page.getByRole('button', {name:'等权',exact:true,pressed:true}).count() === 2,
      'the comparison and main index selectors stay synchronized');
    await selector.getByRole('button', {name:'中位数',exact:true}).click();
    await mainIndices.getByText('当前显示 中位数（median）。', {exact:false}).waitFor();
    check(await selector.evaluate(element =>
      getComputedStyle(element.querySelector('[aria-pressed="true"]')).backgroundColor !==
      getComputedStyle(element.querySelector('[aria-pressed="false"]')).backgroundColor), 'selected method has visible feedback');
    await mainIndices.getByLabel('对照指标', {exact:true}).selectOption('pb');
    await page.getByRole('heading', {name: '沪深300 · PB', exact: true}).waitFor();
    check(await page.locator('.waterlevel-chart svg').count() >= 3, 'valuation charts render as actual SVG');
    await shot('waterlevel-valuations');

    await page.getByRole('tab', {name: '宏观背景', exact: true}).click();
    await page.getByRole('heading', {name: 'M2−M1同比剪刀差', exact: true}).waitFor();
    check(await page.getByLabel('股票估值曲线窗口').count() === 0, 'macro has no preset windows');
    const macroChart = page.getByRole('img', {name:'制造业PMI历史曲线',exact:true});
    const grid = await macroChart.locator('.recharts-cartesian-grid').boundingBox();
    await page.mouse.move(grid.x+grid.width*.30,grid.y+grid.height*.5);
    await page.mouse.down();
    await page.mouse.move(grid.x+grid.width*.65,grid.y+grid.height*.5,{steps:12});
    await page.mouse.up();
    const reset = macroChart.locator('..').getByRole('button',{name:'恢复全历史',exact:true});
    await reset.waitFor();
    await reset.click();
    check(await reset.count() === 0, 'drag selects a date range and reset restores all history');
    const gap = first.series.find(item => item.id === 'money_gap');
    check(gap.statistics.current === '3' && gap.unit === '百分点', 'money gap uses M2 minus M1 percentage points');
    await shot('waterlevel-macro');
    await page.getByRole('tab', {name: '报告存档', exact: true}).click();
    await page.getByText('研究报告存档与下载', {exact: true}).click();
    await page.getByRole('button', {name: '冻结研究快照', exact: true}).click();
    await page.getByRole('link', {name: '下载离线报告', exact: true}).waitFor();
    const snapshotId = await page.getByLabel('研究快照', {exact: true}).inputValue();
    const frozen = await api('/market-waterlevel/snapshots/' + snapshotId);
    check(frozen.data.method === 'median', 'frozen snapshot keeps the viewed method');
    const response = await page.request.get(base + '/api/market-waterlevel/snapshots/' + snapshotId + '/export');
    const html = await response.text();
    fs.writeFileSync(path.join(output, 'synthetic-market-report.html'), html);
    const offline = await browser.newPage();
    let external = 0;
    await offline.route('**/*', route => { external++; return route.abort(); });
    await offline.setContent(html);
    check(await offline.locator('svg').count() > 0 && external === 0, 'offline report renders without external assets or scripts');
    check(await offline.getByText('历史平均', {exact:true}).count() === 2, 'both frozen comparisons include the historical mean');
    check(await offline.getByRole('heading', {name:'全市场 PE TTM · 历史均值与两市综指', exact:true}).count() === 1,
      'offline snapshot includes the full-market comparison');
    await offline.screenshot({path: path.join(output, 'waterlevel-offline.png'), fullPage: false});
    await offline.close();

    await page.getByLabel('研究快照', {exact: true}).selectOption('');
    await openManagement();
    await page.getByRole('tab', {name:'指数资料', exact:true}).click();
    await page.getByLabel('数据管理指数', {exact:true}).selectOption('csi:000510');
    await page.getByRole('group',{name:'中证A500全部已订阅数据',exact:true})
      .getByRole('button', {name:'全量校准', exact:true}).click();
    const full = await recentJob(retried.id);
    check(full.state === 'complete' && full.resources.length === 1 && full.resources[0].full &&
      full.resources[0].target === 'csi:000510' && full.resources[0].module === 'valuations',
      'full reconciliation stays within A500 and its subscribed data');
    const marketA500 = (await api('/market-waterlevel?asof=' + asof)).sources.find(source => source.index_id === 'csi:000510');
    const libraryA500 = (await api('/research/indices/csi%3A000510')).sources.find(source => source.module === 'valuations');
    check(marketA500.snapshot_id === libraryA500.snapshot_id &&
      marketA500.snapshot_id === full.items.find(item => item.target === 'csi:000510').snapshot_id,
      'market update publishes the exact valuation snapshot read by both pages');
    await shot('waterlevel-manual-updates');
    await openWaterlevel();
    await page.setViewportSize({width: 390, height: 844});
    await page.getByRole('tab', {name: '水位总览', exact: true}).click();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), 'mobile layout has no page overflow');
    await shot('waterlevel-mobile');
    check((await api('/bootstrap')).revision === boot.revision, 'market data and snapshots preserve ledger revision');
    check(errors.length === 0, 'no browser runtime errors');
    fs.writeFileSync(path.join(output, 'waterlevel-checks.json'), JSON.stringify({checks, errors}, null, 2));
  } catch (error) {
    await shot('waterlevel-error');
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
