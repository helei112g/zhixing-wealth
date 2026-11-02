import {money,Obj} from './api';
import './planning.css';

export function PlanAllocation({value}:{value:Obj}){
 const over=Number(value.unallocated_target_pct)<0,complete=Number(value.unallocated_target_pct)===0;
 return <section className={'plan-allocation'+(over?' overallocated':'')} aria-live="polite">
  <div><span>设置后计划合计</span><b>{money(value.total_target_pct)}% / 100%</b><small>{money(value.total_target_amount)} CNY</small></div>
  <div><span>{over?'超出总预算':complete?'总预算已分配完成':'尚可分配'}</span><strong>{money(Math.abs(Number(value.unallocated_target_pct)))}%</strong><b>{money(Math.abs(Number(value.unallocated_target_amount)))} CNY</b></div>
  <p>按本次输入替换当前标的计划后计算；尚可分配额是未分配的总预算，实际现金能否完成计划见下方核对。{over?'计划合计超过100%，可调整比例，也可继续保存。':''}</p>
 </section>;
}

export function PlanFundingNotice({value,compact=false}:{value:Obj|null|undefined;compact?:boolean}){
 if(!value)return null;
 const short=value.status==='shortfall',unknown=value.status==='unknown';
 const text=short?'计划资金缺口 '+money(value.shortfall)+' CNY':unknown?'计划资金缺口暂无法核算':value.status==='balanced'?'剩余计划与可投金额匹配':'计划后余量 '+money(value.surplus)+' CNY';
 return <div className={'plan-funding '+(compact?'compact ':'')+(short?'shortfall':'')} aria-live="polite">
  <strong>{text}</strong>
  {!compact&&<>{unknown?<p className="muted">存在无法核实的净投入或可投金额，请核对历史记录；仍可保存计划。</p>:<p>剩余计划需投入 <b>{money(value.required_amount)}</b> − 剩余可投金额 <b>{money(value.available_amount)}</b> CNY</p>}
  <small>{value.basis}</small>{short&&<small>此项仅提醒，可继续保存计划。</small>}</>}
 </div>;
}
