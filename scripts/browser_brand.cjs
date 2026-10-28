/* The approved logo and environment labels must render on desktop and mobile. */
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const base=process.env.PORTFOLIO_TEST_URL||'http://127.0.0.1:8876';
(async()=>{const browser=await pw.chromium.launch({executablePath:process.env.CHROME_PATH||undefined,headless:true});
try{for(const [environment,label] of [['test','测试环境'],['dev','开发环境'],['production','正式环境']]){
const page=await browser.newPage();const health=await (await page.request.get(base+'/api/health')).json();assert.equal(health.environment,'test');assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);
// Only display metadata is varied; every scenario still uses the guarded synthetic backend.
await page.route('**/api/bootstrap',async route=>{const response=await route.fetch();const data=await response.json();await route.fulfill({response,json:{...data,environment,environment_label:label}});});
await page.goto(base);await page.getByRole('button',{name:'创建第一个组合',exact:true}).waitFor();
await page.waitForFunction(title=>document.title===title,environment==='production'?'知行财富':'知行财富 · '+label);
const logo=page.getByRole('img',{name:'知行财富',exact:true});assert.equal(await logo.count(),1);assert.ok(await logo.evaluate(e=>e.complete&&e.naturalWidth>0));
for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
await page.setViewportSize({width,height});
if(environment==='production'){assert.equal(await page.locator('.environment-badge').count(),0);assert.equal(await page.getByText('正式环境',{exact:true}).count(),0);}
else{assert.equal(await page.locator('.sidebar .environment-badge').innerText(),label);assert.ok(await page.locator('.sidebar .environment-badge').isVisible());}
await page.screenshot({path:path.join(process.env.PORTFOLIO_TEST_OUTPUT,'brand-'+environment+'-'+name+'.png'),fullPage:true});assert.ok(await logo.isVisible());assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));}
await page.close();console.log('PASS '+environment+' logo, sidebar label, title and responsive layout');}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
