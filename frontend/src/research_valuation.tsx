import React from 'react';
import {money,Obj,percent} from './api';
import {Panel} from './components';

export function rankLabel(window?:Obj){
 return (window?.rank==null?'-':percent(window.rank))+(window?.status==='partial_history'?'*':'');
}

export function PartialCoverage({value}:Obj){
 const partial=[3,5,10].filter(w=>value?.windows?.[w]?.status==='partial_history');
 if(!partial.length)return null;
 return <small className="research-partial-coverage">*不足 {partial.join(' / ')} 年 · 实际覆盖<br/>{value.coverage?.start} 至 {value.coverage?.end}</small>;
}

export function ValuationCrossCheck({value}:Obj){
 if(!value)return null;
 const pe=value.pe,compared=['warning','within_threshold'].includes(pe.status),warning=pe.status==='warning';
 return <Panel title="中证估值交叉校验" className={'research-valuation-check'+(warning?' has-warning':'')}>
  <p role={warning?'alert':undefined}><strong>{warning?'PE 差异较大，建议核对口径':compared?'PE 差异未达提示阈值':'PE 未完成校验'}</strong></p>
  {compared?<p>{pe.date} · 韭圈儿 PE TTM {money(pe.primary,4)} 倍 / 中证滚动 PE {money(pe.official,4)} 倍；相对差异 {percent(pe.relative_difference,true)}。</p>:<p>{pe.reason}{pe.reference_date&&`（中证最近有效日期 ${pe.reference_date}）`}</p>}
  <p className="muted small">仅比较同日 PE；以中证数值为分母，绝对相对差异达到 {percent(value.threshold)} 时提示。两家计算细节可能不同，校验不替换韭圈儿历史或分位。</p>
  <p className="muted small">{value.pb.reason}。</p>
 </Panel>;
}
