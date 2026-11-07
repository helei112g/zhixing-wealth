import {useEffect, useState} from 'react';
import {api, Obj} from './api';
import {Button, ErrorBox, Panel} from './components';
import {CollectionDialog, moduleLabels, SourceReview, TokenSettings} from './research_studio';
import {activeJob, UpdateRecords, useJobs, useSubmission} from './sync_ui';

const indexScopes=['index_prices','memberships','valuations','financials'];

function UpdateActions({label, disabled, onUpdate}: {label:string; disabled:boolean; onUpdate:(full:boolean)=>void}){
 return <div className="inline-actions" role="group" aria-label={label}>
  <Button small disabled={disabled} onClick={()=>onUpdate(false)}>增量更新</Button>
  <Button small disabled={disabled} onClick={()=>onUpdate(true)}>全量校准</Button>
 </div>;
}

function providerFor(profile:Obj, module:string){
 if(profile.exposure==='overseas')return profile.provider==='cni'?'国证指数':'中证指数';
 if(['stock_valuations','financials','historical_financials'].includes(module))return '东方财富';
 return '理杏仁';
}

function IndexData({profile, busy, revision, onUpdate, onEdit}:Obj){
 const [detail,setDetail]=useState<Obj|null>(null);
 const [error,setError]=useState('');
 useEffect(()=>{
  let current=true;
  api('/research/indices/'+encodeURIComponent(profile.id)+'?method=mcw').then(value=>{
   if(current){setDetail(value);setError('');}
  }).catch(error=>{if(current)setError(error.message);});
  return()=>{current=false;};
 },[profile.id,revision]);

 const resources:Obj[]=detail?.collection.resources||[];
 const groups=new Map<string,Obj[]>();
 for(const resource of resources){
  const provider=providerFor(profile,resource.module);
  const list=groups.get(provider)||[];
  list.push(resource);
  groups.set(provider,list);
 }
 const disabled=busy||!detail?.collection.subscription.enabled||!!error;
 return <section className="data-target-details" aria-label={profile.name+'数据来源'}>
  <div className="inline-actions">
   <UpdateActions label={profile.name+'全部已订阅数据'} disabled={disabled||!resources.length}
    onUpdate={full=>onUpdate(resources,full)}/>
   <Button small onClick={onEdit}>管理采集</Button>
  </div>
  <ErrorBox error={error}/>
  {!detail&&!error&&<p className="muted small">正在读取本地来源…</p>}
  {detail&&!detail.collection.subscription.enabled&&<p className="muted small">采集已暂停，已发布数据仍可查看。</p>}
  {detail&&<div className="table-wrap"><table className="data-sources-table">
   <thead><tr><th scope="col">来源</th><th scope="col">数据模块</th><th scope="col">最新数据日期</th></tr></thead>
   {[...groups].map(([provider,entries])=><tbody key={provider}>{entries.map((resource,index)=>{
    const module=resource.module;
    const snapshot=detail.sources.find((source:Obj)=>source.module===module);
    const status=detail.module_status[module];
    const date=module==='index_info'?null:status?.date;
    return <tr key={module}>
     {index===0&&<th scope="rowgroup" rowSpan={entries.length}>{provider}</th>}
     <td>{moduleLabels[module]||module}</td>
     <td>
      {date?<time dateTime={date}>{date}</time>:snapshot&&module==='index_info'
       ?<span>核对日 {snapshot.fetched_at.slice(0,10)}</span>:snapshot?'日期待核验':'待采集'}
      {snapshot?.publisher&&snapshot.publisher!==provider&&<small>当前快照 · {snapshot.publisher}</small>}
      {status?.reason&&<small className="muted">{status.reason}</small>}
     </td>
    </tr>;
   })}</tbody>)}
  </table></div>}
  {detail&&<details className="data-target-evidence">
   <summary>来源证据、采集进度与历史版本</summary>
   {detail.sources.map((source:Obj)=><details key={source.module} className="research-source">
    <summary>{moduleLabels[source.module]||source.module} · {source.effective_date||'日期待核验'}</summary>
    <p className="muted small">抓取 {source.fetched_at} · 快照 {source.snapshot_id}</p>
    {source.limitation&&<p>{source.limitation}</p>}
    {source.evidence.map((item:Obj,n:number)=><p key={n}>
     <a href={item.url} target="_blank" rel="noopener noreferrer">来源 {n+1} ↗</a>
    </p>)}
   </details>)}
   {Object.entries(detail.collection.modules).map(([module, value])=>{
    const progress=value as Obj;
    return <p key={module} className="muted small">{moduleLabels[module]||module} · 分片 {progress.completed}/{progress.planned}
     {progress.last_reconciled&&' · 最近完整校准 '+progress.last_reconciled}</p>;
   })}
   <SourceReview d={detail}/>
  </details>}
 </section>;
}

function MarketData({item,busy,onUpdate}:Obj){
 return <section className="data-target-details" aria-label={item.label+'数据来源'}>
  <div className="inline-actions">
   <UpdateActions label={item.label+'全部来源'} disabled={busy||!item.updatable}
    onUpdate={full=>onUpdate([item.resource],full)}/>
  </div>
  <div className="table-wrap"><table className="data-sources-table">
   <thead><tr><th scope="col">来源</th><th scope="col">最新数据日期</th><th scope="col">状态</th></tr></thead>
   <tbody><tr>
    <td>{item.publisher}</td>
    <td>{item.dates.length?item.dates.map((value:Obj)=>{
     const date=value.frequency==='monthly'?value.date.slice(0,7):value.date;
     return <div key={value.label}><time dateTime={date}>{date}</time>
      <small>{value.label}{value.frequency==='monthly'?' · 统计月':''}</small></div>;
    }):'—'}</td>
    <td>{item.error?'更新失败':item.dates.length?'已取得':'待采集'}{item.error&&<small>{item.error}</small>}</td>
   </tr></tbody>
  </table></div>
  {!item.updatable&&<p className="muted small">指数资料库的 A 股全指估值采集已暂停。</p>}
  {item.evidence.length>0&&<details className="data-target-evidence">
   <summary>最近来源组证据</summary>
   <p className="muted small">快照 {item.snapshot_id}</p>
   {item.evidence.map((entry:Obj,n:number)=><p key={n}>
    <a href={entry.url} target="_blank" rel="noopener noreferrer">来源 {n+1} ↗</a>
   </p>)}
  </details>}
 </section>;
}

export function ResearchDataPage({initialTarget}: {initialTarget:string}){
 const [tab,setTab]=useState(initialTarget==='market'?'market':'indices');
 const [selected,setSelected]=useState(initialTarget==='market'?'':initialTarget);
 const [marketSelected,setMarketSelected]=useState('');
 const [dialog,setDialog]=useState<Obj|null>(null);
 const [catalog,setCatalog]=useState<Obj|null>(null);
 const [market,setMarket]=useState<Obj[]>([]);
 const [revision,setRevision]=useState(0);
 const [refresh,setRefresh]=useState(0);
 const [error,setError]=useState('');
 const [accepted,setAccepted]=useState(false);
 const feed=useJobs('/data-updates/history?area=research');
 const submission=useSubmission(feed.reload);
 const jobVersion=feed.jobs.map(job=>[job.id,job.state,job.items?.length].join(':')).join('|');
 const busy=submission.busy||feed.jobs.some(activeJob);

 useEffect(()=>{
  let current=true;
  async function loadRevision(){
   try{
    const result=await api('/research/revision');
    if(current)setRevision(result.research_revision);
   }catch(error:any){if(current)setError(error.message);}
  }
  loadRevision();
  const timer=setInterval(loadRevision,3000);
  return()=>{current=false;clearInterval(timer);};
 },[]);
 useEffect(()=>{
  let current=true;
  const request=tab==='indices'?'/research/catalog':'/market-waterlevel/sources';
  api(request).then(value=>{
   if(!current)return;
   setError('');
   if(tab==='indices')setCatalog(value);
   else setMarket(value.items);
  }).catch(error=>{if(current)setError(error.message);});
  return()=>{current=false;};
 },[tab,revision,refresh,jobVersion]);

 async function submit(body:Obj){
  setAccepted(false);
  const result=await submission.submit(body);
  if(result)setAccepted(true);
 }

 function update(resources:Obj[],full=false){
  return submit({
   scopes:[...new Set(resources.map(resource=>resource.scope))],
   resources:resources.map(resource=>({...resource,full})),
  });
 }

 const indices=catalog?.subscriptions||[];
 const hasEnabledIndices=indices.some((value:Obj)=>value.subscription?.enabled!==false);
 const profile=indices.find((value:Obj)=>value.id===selected);
 const item=market.find(value=>value.id===marketSelected);
 const marketResources=market.filter(value=>value.updatable).map(value=>value.resource);
 return <div className="research-page research-data-page">
  <ErrorBox error={error}/>
  <p className="muted small">批量更新按增量执行。选定单项后可增量更新或全量校准。</p>
  <div className="instrument-tabs" role="tablist" aria-label="研究数据类型">
   <button role="tab" aria-selected={tab==='indices'} onClick={()=>setTab('indices')}>指数资料</button>
   <button role="tab" aria-selected={tab==='market'} onClick={()=>setTab('market')}>市场指标</button>
  </div>
  {tab==='indices'?<Panel title="指数资料" right={<Button disabled={busy||!hasEnabledIndices}
   onClick={()=>submit({scopes:indexScopes})}>更新全部指数</Button>}>
   <label className="data-target-select">选择指数
    <select aria-label="数据管理指数" value={selected} onChange={event=>setSelected(event.target.value)}>
     <option value="">请选择已建档指数</option>
     {indices.map((value:Obj)=><option key={value.id} value={value.id}>{value.name} · {value.code}</option>)}
    </select>
   </label>
   {profile&&<IndexData key={profile.id} profile={profile} busy={busy}
    revision={revision+':'+jobVersion+':'+refresh} onUpdate={update} onEdit={()=>setDialog(profile)}/>}
  </Panel>:<Panel title="市场指标" right={<Button disabled={busy||!marketResources.length}
   onClick={()=>update(marketResources)}>更新全部市场指标</Button>}>
   <label className="data-target-select">选择指标
    <select aria-label="数据管理市场指标" value={marketSelected} onChange={event=>setMarketSelected(event.target.value)}>
     <option value="">请选择市场指标</option>
     {market.map(value=><option key={value.id} value={value.id}>{value.label}</option>)}
    </select>
   </label>
   {item&&<MarketData item={item} busy={busy} onUpdate={update}/>}
   <p className="muted small">市场水位使用的宽基估值在「指数资料」中更新，两处共享已发布的快照。</p>
  </Panel>}
  {accepted&&<p className="muted small" role="status">更新任务已受理，完成状态及实际数据日期见下方。</p>}
  <UpdateRecords feed={feed} submission={submission}/>
  <TokenSettings/>
  {dialog&&catalog&&<CollectionDialog catalog={catalog} profile={dialog}
   onClose={()=>setDialog(null)} onSaved={(value:Obj)=>{
    setSelected(value.id);setDialog(null);setRevision(value.research_revision);setRefresh(n=>n+1);
    localStorage.setItem('research-selected',value.id);
    const url=new URL(location.href);
    url.searchParams.set('index',value.id);
    history.replaceState(null,'',url);
   }}/>}
 </div>;
}
