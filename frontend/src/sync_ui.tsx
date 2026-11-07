import {useEffect,useRef,useState} from 'react';
import {api,Obj} from './api';
import {Button,ErrorBox,Panel} from './components';

const labels:Obj={market:'市场水位',index_info:'指数基本信息',volatility:'指数波动率',index_financials:'指数季度财务',market_activity:'资金与持仓',quotes:'持仓行情',fx:'汇率',index_prices:'指数价格',memberships:'成分名单与权重',valuations:'指数及成分估值',financials:'成分财务',price:'价格指数',total_return:'全收益指数',historical_memberships:'历史有效成分',stock_valuations:'成分公司估值',historical_financials:'历史成分财务'};
const states:Obj={complete:'完成',ok:'完成',manual:'需手工核对',partial:'部分失败',error:'失败',pending:'等待中',running:'更新中',interrupted:'已中断',unsupported:'暂不支持'};
export const activeJob=(j:Obj)=>['pending','running'].includes(j.state);

export function useJobs(path:string){
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
 {(job.items||[]).map((item:Obj,n:number)=><div className="research-job-item" key={n}><span>{item.name||item.resource?.series||item.resource?.group||labels[item.scope]} {item.resource?.currency||item.target||''} {labels[item.module]||item.module||''} · {states[item.status]||item.status}{item.date&&' · '+item.date}{item.count!=null&&' · '+item.count+' 条'}</span>{(item.error||item.message)&&<small>{item.error||item.message}</small>}{item.difference&&<small>新增 {item.difference.added} · 修订 {item.difference.revised}</small>}{item.notes?.length>0&&<small>{item.notes.join('；')}</small>}{item.status==='error'&&<Button small disabled={busy||activeJob(job)} onClick={()=>onRetry(resourceFor(item))}>重试此项</Button>}</div>)}
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
export function UpdateRecords({feed,submission}:Obj){
 return <Panel title="更新记录">
  <p className="muted small">展示本模块最近 5 条更新记录，同一任务的子任务合并展示，按开始时间倒序排列。</p>
  <ErrorBox error={feed.error||submission.error}/>
  {feed.jobs.length?feed.jobs.map((job:Obj)=><JobRecord key={job.kind+':'+job.id} job={job} busy={submission.busy}
   onRetry={(resource:Obj)=>submission.submit({scopes:[resource.scope],resources:[resource]})}/>):<p className="muted">尚无更新记录。</p>}
 </Panel>;
}

export function UpdateHistory({area}: {area:'portfolio'|'research'}){
 const feed=useJobs('/data-updates/history?area='+area);
 const submission=useSubmission(feed.reload);
 return <UpdateRecords feed={feed} submission={submission}/>;
}
