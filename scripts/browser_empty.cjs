/* Fresh installation: no fixture writes, no external data fetches. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'../frontend/node_modules/playwright');
const base=process.env.PORTFOLIO_TEST_URL,output=process.env.PORTFOLIO_TEST_OUTPUT;
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 let checks=0;page.on('pageerror',e=>errors.push(e.message));
 const check=(value,label)=>{assert.ok(value,label);console.log('PASS',++checks,label);};
 const read=async p=>{const r=await page.request.get(base+p);assert.ok(r.ok());return r.json();};
 try{
  const health=await read('/api/health');
  assert.equal(health.environment,'test');assert.equal(health.data_directory,process.env.PORTFOLIO_TEST_DATA_DIR);
  const boot=await read('/api/bootstrap');
  for(const field of ['portfolios','instruments','allocations','event_refs','jobs','price_status','fx_entries'])
   check(Array.isArray(boot[field])&&boot[field].length===0,'fresh '+field+' is empty');
  check(!boot.latest_fx.HKD&&!boot.latest_fx.USD,'fresh install has no currency cache');
  const catalog=await read('/api/family/catalog');
  for(const [key,value] of Object.entries(catalog))if(Array.isArray(value))check(value.length===0,'fresh family '+key+' is empty');
  await page.goto(base);await page.getByRole('navigation',{name:'全局页面'}).waitFor();
  await page.getByRole('button',{name:'组合总览',exact:true}).click();
  await page.getByRole('button',{name:'创建第一个组合',exact:true}).waitFor();
  check(await page.getByText('从第一个组合开始',{exact:true}).isVisible(),'empty onboarding has no demo portfolio');
  await page.screenshot({path:path.join(output,'empty-desktop.png'),fullPage:true});
  await page.reload();await page.getByRole('button',{name:'组合总览',exact:true}).click();
  await page.getByRole('button',{name:'创建第一个组合',exact:true}).waitFor();
  check((await read('/api/bootstrap')).portfolios.length===0,'reload does not create records');
  await page.setViewportSize({width:390,height:844});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'empty state fits mobile');
  await page.screenshot({path:path.join(output,'empty-mobile.png'),fullPage:true});
  check(errors.length===0,'empty state has no browser errors');
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({checks,errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
