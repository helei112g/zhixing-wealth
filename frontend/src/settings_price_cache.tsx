import {useEffect, useState} from 'react';
import {api, money, Obj} from './api';
import {Button, ErrorBox, Panel} from './components';

import {RefreshCw} from 'lucide-react';

const PAGE_SIZE = 10;
const labels:Obj={quotes:'持仓行情',fx:'汇率'};

export function SettingsPriceCache({boot,syncing,sync}:Obj){
 const [page,setPage]=useState(1);
 const total=boot.instruments.length;
 const pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
 const current=Math.min(page,pages);
 useEffect(()=>{setPage(p=>Math.min(p,pages));},[pages]);
 const rows=boot.instruments.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE);
 const statuses=new Map<string,Obj>(boot.price_status.map((r:Obj)=>[r.instrument_id,r]));
 return <Panel title="行情与净值缓存" right={<Button small disabled={syncing} onClick={()=>sync(undefined,true)}>同步全部历史</Button>}>
  {total?<>
   <div className="table-wrap"><table className="price-cache-table"><thead><tr><th>标的</th><th>最新价格 / 净值 / 指数点位</th><th scope="col">最新数据日期</th><th scope="col">来源</th><th>历史点数</th><th>操作</th></tr></thead><tbody>{rows.map((i:Obj)=>{const r=statuses.get(i.id);return <tr key={i.id} data-cache-instrument={i.id}><td>{i.name}{i.builtin&&<span className="pill">官方全收益</span>}<small>{i.code}.{i.market} · {i.currency}</small></td><td>{money(r?.latest?.price,4)}</td><td>{r?.latest?.date?<time dateTime={r.latest.date}>{r.latest.date}</time>:'待同步'}</td><td>{r?.latest?.source||'—'}</td><td>{r?.count||0}</td><td><button className="text-button" disabled={syncing} onClick={()=>sync(i.id,true,'manual_instrument')}>重新同步</button></td></tr>;})}</tbody></table></div>
   <nav className="cache-pagination" aria-label="行情缓存分页"><span className="muted small" aria-live="polite">共 {total} 个标的 · 第 {current} / {pages} 页 · 每页 {PAGE_SIZE} 条</span><div className="inline-actions"><Button small disabled={current===1} onClick={()=>setPage(current-1)}>上一页</Button><Button small disabled={current===pages} onClick={()=>setPage(current+1)}>下一页</Button></div></nav>
  </>:<p className="muted">录入交易后，按已启用的全局策略补齐行情。</p>}
  <p className="muted small">价格与净值通过数据源同步；自动更新范围、频率和时刻由上方数据更新策略统一管理。记账后按已开启且非“仅手动”的策略补齐历史。自动更新失败项可在公共数据任务中精确重试；手动历史同步结果见更新记录，旧缓存保留。休市与基金净值晚公布时，价格日期可能不同。</p>
 </Panel>;
}

export function DataUpdateSettings(){
 const [d,D]=useState<Obj|null>(null),[draft,Draft]=useState<Obj[]>([]),[error,E]=useState(''),[busy,Busy]=useState(false),[review,Review]=useState(false),[message,Message]=useState('');
 async function load(preserve=false){const v=await api('/settings/data-updates');D(v);if(!preserve)Draft(v.policies);Review(false);}
 useEffect(()=>{load().catch(e=>E(e.message));},[]);
 async function save(){Busy(true);E('');try{const v=await api('/settings/data-updates',{policies:draft,expected_settings_revision:d?.settings_revision},'PUT');D(v);Draft(v.policies);Message('更新策略已保存');}catch(e:any){E(e.message);if(e.status===409||!e.status)Review(true);}finally{Busy(false);}}
 async function run(scopes:string[],targets?:string[],resources?:Obj[]){Busy(true);E('');try{await api('/data-updates/run',{scopes,targets,resources,request_id:crypto.randomUUID()});Message('已提交更新任务');}catch(e:any){E(e.message);}finally{Busy(false);}}
 return <><Panel title="数据更新" right={<span className="pill">投资组合</span>}><ErrorBox error={error}/><p className="muted small">持仓行情和汇率支持自动更新。排程应用于本机全部组合。</p>{d?<><div className="table-wrap"><table className="data-update-table"><thead><tr><th scope="col">数据范围</th><th scope="col">自动更新</th><th scope="col">频率</th><th scope="col">北京时间</th><th scope="col">操作</th></tr></thead><tbody>{draft.map((r:Obj,n:number)=>['quotes','fx'].includes(r.scope)&&<tr key={r.scope}><td>{labels[r.scope]}{(r.enabled!==d.policies[n].enabled||r.cadence!==d.policies[n].cadence||r.time!==d.policies[n].time)&&<small className="policy-saved-value">当前已保存：{d.policies[n].enabled?'开启':'关闭'} · {({daily:'每日',trade_day:'交易日',weekly:'每周一',manual:'仅手动'} as Obj)[d.policies[n].cadence]} {d.policies[n].time}</small>}</td><td><input type="checkbox" aria-label={labels[r.scope]+'自动更新'} checked={r.enabled} disabled={busy} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,enabled:e.target.checked}:x))}/></td><td><select aria-label={labels[r.scope]+'频率'} value={r.cadence} disabled={busy} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,cadence:e.target.value}:x))}><option value="daily">每日</option><option value="trade_day">交易日</option><option value="weekly">每周一</option><option value="manual">仅手动</option></select></td><td><input aria-label={labels[r.scope]+'时刻'} type="time" value={r.time} disabled={busy||r.cadence==='manual'} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,time:e.target.value}:x))}/></td><td><Button small disabled={busy} onClick={()=>run([r.scope])}>立即更新</Button></td></tr>)}</tbody></table></div><div className="research-settings-actions">{review&&<Button disabled={busy} onClick={()=>load(true).catch(e=>E(e.message))}>重新读取并核对（保留草稿）</Button>}<Button primary disabled={busy||review} onClick={save}>保存更新策略</Button><Button disabled={busy} onClick={()=>run(['quotes','fx'])}><RefreshCw size={14}/>更新行情与汇率</Button>{message&&<small role="status">{message}</small>}</div></>:<p className="muted">读取更新策略…</p>}</Panel></>;
}
