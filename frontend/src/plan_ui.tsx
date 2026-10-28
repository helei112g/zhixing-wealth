import {money,Obj} from './api';
import './planning.css';

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
