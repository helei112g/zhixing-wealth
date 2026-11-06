import {useEffect,useRef,useState} from 'react';
import {api,Obj} from './api';
import {Button,ErrorBox,Panel} from './components';

const labels:Obj={market:'市场水位',index_info:'指数基本信息',index_financials:'指数季度财务',market_activity:'资金与持仓',tracking_funds:'跟踪基金',quotes:'持仓行情',fx:'汇率',index_prices:'指数价格',memberships:'成分名单与权重',valuations:'指数及成分估值',financials:'成分财务',price:'价格指数',total_return:'全收益指数',historical_memberships:'历史有效成分',stock_valuations:'成分公司估值',historical_financials:'历史成分财务'};
const states:Obj={complete:'完成',ok:'完成',manual:'需手工核对',partial:'部分失败',error:'失败',pending:'等待中',running:'更新中',interrupted:'已中断',unsupported:'暂不支持'};
const activeJob=(j:Obj)=>['pending','running'].includes(j.state);
const indexScopes=['index_prices','memberships','valuations','financials'];

function useJobs(path:string){
 const[jobs,Jobs]=useState<Obj[]>([]),[error,E]=useState(''),[refresh,R]=useState(0);
 useEffect(()=>{let active=true,busy=false;async function load(){if(busy||document.hidden)return;busy=true;try{const v=await api(path);if(active){Jobs(v.jobs);E('');}}catch(e:any){if(active)E(e.message);}finally{busy=false;}}load();const timer=setInterval(load,2500);window.addEventListener('focus',load);return()=>{active=false;clearInterval(timer);window.removeEventListener('focus',load);};},[path,refresh]);
 return {jobs,error,reload:()=>R(v=>v+1)};
}
function resourceFor(item:Obj){
 if(item.resource)return item.resource;
 return item.source_kind==='fx'?{scope:'fx',currency:item.currency,full:true}:{scope:'quotes',instrument_id:item.instrument_id||null,full:true};
}
function JobRecord({job,onRetry,busy}:Obj){
 return <details className="job-record" data-update-job={job.id} open={activeJob(job)}><summary><span>{job.started_at?.slice(0,19).replace('T',' ')}</span><b>{states[job.state]||job.state}</b><small>{job.scopes?.map((s:string)=>labels[s]||s).join('、')||job.message}</small><small className="job-trigger">{job.resources?.some((r:Obj)=>r.full)&&'全量校对 · '}触发原因：{job.trigger_label||({manual:'手动更新',index_create:'指数建档'} as Obj)[job.trigger]||'历史记录未记录'}</small></summary>
 {job.resources&&<p className="muted small">已处理 {job.completed_resources?.length||0} / {job.resources.length} 项{job.market_cutoff&&' · 收盘数据请求截至 '+job.market_cutoff}{job.information_cutoff&&' · 公告资料请求截至 '+job.information_cutoff}。实际数据日期以各项结果为准。</p>}
 {(job.items||[]).map((item:Obj,n:number)=><div className="research-job-item" key={n}><span>{item.name||labels[item.scope]} {item.resource?.currency||item.target||''} {labels[item.module]||item.module||''} · {states[item.status]||item.status}{item.date&&' · '+item.date}{item.count!=null&&' · '+item.count+' 条'}</span>{(item.error||item.message)&&<small>{item.error||item.message}</small>}{item.difference&&<small>新增 {item.difference.added} · 修订 {item.difference.revised}</small>}{item.notes?.length>0&&<small>{item.notes.join('；')}</small>}{item.status==='error'&&<Button small disabled={busy||activeJob(job)} onClick={()=>onRetry(resourceFor(item))}>重试此项</Button>}</div>)}
 </details>;
}
export function useSubmission(reload:()=>void){
 const[busy,B]=useState(false),[error,E]=useState('');const pending=useRef<Obj|null>(null);
 async function submit(body:Obj){
  const signature=JSON.stringify(body);
  if(pending.current&&pending.current.signature!==signature){E('上一请求结果尚未核实，请先在更新记录核对，再继续原范围请求。');return;}
  const request=pending.current||{signature,body:{...body,request_id:crypto.randomUUID()}};pending.current=request;B(true);E('');
  try{const value=await api('/data-updates/run',request.body);pending.current=null;reload();return value;}
  catch(e:any){if(e.status&&e.status<500)pending.current=null;E(e.message+(!e.status||e.status>=500?'；提交结果尚未确定，请先核对更新记录，再重试原范围（保留请求标识）。':''));reload();}
  finally{B(false);}
 }
 return {submit,busy,error};
}
export function UpdateHistory(){
 const feed=useJobs('/data-updates/history'),submit=useSubmission(feed.reload);
 return <Panel title="更新记录"><p className="muted small">公共数据与行情更新统一展示最近 5 条，同一任务的子任务合并展示；按开始时间倒序排列。</p><ErrorBox error={feed.error||submit.error}/>{feed.jobs.length?feed.jobs.map(j=><JobRecord key={j.kind+':'+j.id} job={j} busy={submit.busy} onRetry={(r:Obj)=>submit.submit({scopes:[r.scope],resources:[r]})}/>):<p className="muted">尚无更新记录。</p>}</Panel>;
}
export function IndexUpdate({indexId,subscription,onUpdated}:Obj){
 const feed=useJobs('/data-updates/jobs'),submission=useSubmission(feed.reload);
 const[jobId,JobId]=useState(''),[message,M]=useState('');
 const relevant=feed.jobs.filter(j=>j.resources?.some((r:Obj)=>r.target===indexId));
 const active=relevant.find(activeJob),job=relevant.find(j=>j.id===jobId)||active||relevant[0];
 const notified=useRef('');
 useEffect(()=>{if(job&&!activeJob(job)){const key=job.id+':'+job.state+':'+job.finished_at;if(notified.current!==key){notified.current=key;onUpdated();}}},[job?.id,job?.state,job?.finished_at]);
 const disabled = submission.busy || !!active || !subscription.enabled;
 const valuation = {scope:'valuations',target:indexId,module:'valuations'};
 async function run(resource?:Obj, full=false){
  const body = resource
   ? {scopes:[resource.scope],resources:[resource]}
   : {scopes:indexScopes,targets:[indexId],full};
  const result = await submission.submit(body);
  if(result){
   JobId(result.job_id);
   M('更新任务已受理，完成状态及实际数据日期见下方。');
  }
 }
 return <Panel title="更新当前指数" right={<div className="inline-actions">
  <Button small disabled={disabled||!subscription.modules.includes('valuations')} onClick={()=>run(valuation)}>更新指数估值</Button>
  <Button small disabled={disabled} onClick={()=>run(undefined,true)}>全量校对</Button>
  <Button primary disabled={disabled} onClick={()=>run()}>{submission.busy?'正在提交…':active?'正在更新…':'更新当前指数已订阅数据'}</Button>
 </div>}>
  <p className="muted small">当前指数仅手动更新。日常更新补齐新数据并复核近期记录，全量校对重新请求已订阅模块的配置范围。模块失败时保留上次有效快照。</p>
  <ErrorBox error={submission.error||feed.error}/>{message&&<p className="muted small" role="status">{message}</p>}
  {job&&<JobRecord job={{...job,items:job.items.filter((i:Obj)=>i.target===indexId||i.resource?.target===indexId)}} busy={disabled} onRetry={run}/>}
 </Panel>;
}
