import {useEffect,useState} from 'react';
import {Bell,ChevronRight} from 'lucide-react';
import {api,money,Obj,syncErrorText} from './api';
import {Button,Empty,ErrorBox,Panel} from './components';

const sources:Obj={family:'资产到期',dividend:'组合分红',portfolio:'交易核对',sync:'数据同步',system:'系统设置'};
export function useReminders(version:string){
 const[data,D]=useState<Obj|null>(null),[error,E]=useState('');
 useEffect(()=>{let active=true,busy=false;async function load(){if(busy||document.hidden)return;busy=true;try{const value=await api('/reminders');if(active){D(value);E('');}}catch(e:any){if(active)E(e.message);}finally{busy=false;}}load();const timer=setInterval(load,45000);window.addEventListener('focus',load);return()=>{active=false;clearInterval(timer);window.removeEventListener('focus',load);};},[version]);
 return {data,error};
}
export function RemindersBanner({data,error,onOpen}:Obj){
 if(!error&&!data?.count)return null;
 return <div className={'family-banner system-reminder-banner '+(data?.today?'urgent':'')} role="status"><Bell size={21}/><div><b>{error?'提醒暂不可用':'系统提醒 · '+data.count+' 项'}</b><small>{error||data.items.slice(0,2).map((r:Obj)=>r.title).join('　/　')}</small></div><Button small onClick={onOpen}>查看提醒 <ChevronRight size={14}/></Button></div>;
}
export function ReminderCenter({data,error,onAction,busy}:Obj){
 const[source,S]=useState('all'),focus=new URLSearchParams(location.search).get('focus');
 useEffect(()=>{if(focus&&data){const row=Array.from(document.querySelectorAll('[data-reminder-id]')).find(el=>el.getAttribute('data-reminder-id')===focus);row?.scrollIntoView({block:'center'});}},[focus,data]);
 const rows=(data?.items||[]).filter((r:Obj)=>source==='all'||r.source===source),hasAction=rows.some((r:Obj)=>r.action);
 return <Panel title="全部系统提醒" className="system-reminder-center" right={<select aria-label="提醒来源" value={source} onChange={e=>S(e.target.value)}><option value="all">全部来源</option>{Object.entries(sources).map(([k,v])=><option key={k} value={k}>{String(v)}</option>)}</select>}>
 <p className="muted small">还款、保费和续保只显示今天至未来 35 天，到期后移出列表。分红、交易核对和同步失败在问题解决后移除；这里不会自动记账或扣款。</p><ErrorBox error={error}/>
 {!data?<p>读取提醒…</p>:!rows.length?<Empty title="当前没有这类提醒"/>:<div className="table-wrap"><table><thead><tr><th>来源 / 日期</th><th>事项</th><th>金额</th><th>状态</th>{hasAction&&<th>操作</th>}</tr></thead><tbody>{rows.map((r:Obj)=><tr key={r.id} data-reminder-id={r.id} className={focus===r.id?'family-highlight':''}><td>{sources[r.source]}<small>{r.date||'—'}</small></td><td><b>{r.title}</b><small>{r.source==='sync'?syncErrorText(r.detail):r.detail}</small></td><td>{r.amount==null?'—':money(r.amount)+' '+r.currency}{r.estimated&&<small>预计金额</small>}</td><td>{r.days===0?'今天到期':r.days!=null?r.days+' 天后':r.source==='sync'?'待重试':r.source==='system'?'待检查':'待核对'}</td>{hasAction&&<td>{r.action?<Button small disabled={r.action.type==='sync'&&busy} onClick={()=>onAction(r.action)}>{r.action.label}</Button>:'—'}</td>}</tr>)}</tbody></table></div>}
 </Panel>;
}
