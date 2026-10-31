import {useEffect,useState} from 'react';
import {api,exactMoney,kindNames,money,Obj,signedMoney,units} from './api';
import {Button,ErrorBox,Modal} from './components';

export function DividendNotices({rows,action,onReload}:Obj){
 const[error,E]=useState(''),[selected,S]=useState<Obj|null>(null),[busy,B]=useState(false);
 const pending=(rows||[]).filter((r:Obj)=>['pending','future','needs_review'].includes(r.status)),ignored=(rows||[]).filter((r:Obj)=>r.status==='ignored');
 async function ignore(row:Obj,ignored=true){try{await api('/dividends/'+row.notice.id+'/ignore',{portfolio_id:row.portfolio_id,ignored});onReload();}catch(e:any){E(e.message);}}
 async function keep(){if(!selected||busy)return;B(true);E('');try{
  await api('/dividends/'+selected.notice.id+'/review',{portfolio_id:selected.portfolio_id,event_id:selected.event_id,mode:'keep_recorded',review_basis:selected.review_basis,expected_revision:selected.revision,reason:'已核对实际到账，保留原分红流水并完成核对'});
  S(null);await onReload();
 }catch(e:any){E(e.message);}finally{B(false);}}
 return <><ErrorBox error={error}/>{error&&<Button small disabled={busy} onClick={()=>{S(null);E('');onReload();}}>重新读取待核对分红</Button>}{pending.map((r:Obj)=><div className={'dividend-candidate '+(r.status==='needs_review'?'needs-review':'')} key={r.notice.id}>
  <header><b>{r.instrument.name} · {r.instrument.code}</b><span className="pill">{r.status==='needs_review'?'资料变化，需重新核对':r.status==='future'?'尚未除息':'待确认方式'}</span></header>
  <small>除息 {r.notice.ex_date} · 登记日 {r.eligibility_date||'源未提供'} · 来源派息日 {r.notice.pay_date||'待补充'}</small>
  <p>权益份额 {units(r.eligible_quantity)} × 每份 {r.notice.per_share} {r.notice.currency} = <b>{money(r.estimated_gross)} {r.notice.currency}</b>（参考估算）</p>
  {r.recorded_event&&<p className="reconcile-difference">原记账税前 {exactMoney(r.recorded_event.gross_amount)} {r.recorded_event.currency} · 参考差额 {signedMoney(r.gross_difference)} {r.notice.currency}；原方式：{kindNames[r.recorded_event.kind]}。确认前保留原到账记录。{r.review_changes?.length>0&&<>需核对：{r.review_changes.join('、')}。</>}</p>}
  <div className="inline-actions">
   {r.status==='needs_review'&&<Button small primary disabled={busy} onClick={()=>{E('');S(r);}}>保留实际到账金额并完成核对</Button>}
   {r.status==='needs_review'&&Number(r.estimated_gross)===0&&r.estimated_gross!==null?<Button small disabled={busy} onClick={()=>action('cancel',{entity:r.recorded_event})}>已无权益，核对并删除原分红</Button>:<Button small primary={r.status!=='needs_review'} disabled={busy||r.status==='future'||r.notice.currency!==r.instrument.currency||(r.notice.pay_date&&r.notice.pay_date<r.notice.ex_date)} onClick={()=>action('event',{proposal:r,initialKind:r.suggested_event.kind,instrumentId:r.instrument.id})}>{r.status==='future'?'除息日未到':r.notice.pay_date&&r.notice.pay_date<r.notice.ex_date?'特殊派息安排，需手工核对':r.notice.currency!==r.instrument.currency?'币种需手工核对':r.status==='needs_review'?'重新核对并重算':'核对现金分红 / 红利再投'}</Button>}
   {r.status!=='needs_review'&&<Button small disabled={busy} onClick={()=>ignore(r)}>忽略</Button>}
   {r.notice.source_url?.startsWith('https://')&&<a href={r.notice.source_url} target="_blank" rel="noreferrer">查看来源</a>}
  </div>
  {selected&&selected.notice.id===r.notice.id&&<div className="preview-box" aria-label="保留实际到账核对">
   <b>核对后保留以下原流水事实</b>
   <p>税前 {exactMoney(selected.recorded_event.gross_amount)} − 税费 {exactMoney(selected.recorded_event.tax)} = 税后 {exactMoney(selected.recorded_event.net_amount)} {selected.recorded_event.currency}；手续费 {exactMoney(selected.recorded_event.fee)} {selected.recorded_event.currency}。</p>
   <p>除息日 {selected.recorded_event.date} · 到账日 {selected.recorded_event.pay_date||'未填'} · 锁定汇率 {selected.recorded_event.fx_rate}；到账锁定汇率 {selected.recorded_event.pay_fx_rate||selected.recorded_event.fx_rate}。</p>
   {selected.recorded_event.kind==='REINVEST'&&<p>再投日 {selected.recorded_event.reinvest_date} · 实际份额 {units(selected.recorded_event.reinvest_quantity)} · 再投锁定汇率 {selected.recorded_event.reinvest_fx_rate||selected.recorded_event.pay_fx_rate||selected.recorded_event.fx_rate}。</p>}
   <p className="muted small">保存本次核对依据与结论，结束此待办；原流水及历史锁定汇率保持原样。后续权益或公告实质变化仍会提醒。</p>
   <div className="inline-actions"><Button primary disabled={busy} onClick={keep}>{busy?'保存核对结论…':'确认保留并完成核对'}</Button><Button disabled={busy} onClick={()=>S(null)}>返回继续核对</Button></div>
  </div>}
  <small>{r.notice.source} · {r.note}</small>
 </div>)}{!pending.length&&<p className="muted">暂无待确认或待重算的分红。已确认的收入在总览和流水中统计，未确认的公告不计收益。</p>}{ignored.length>0&&<details className="advanced"><summary>已忽略 {ignored.length}条</summary>{ignored.map((r:Obj)=><div className="record-line" key={r.notice.id}><span>{r.instrument.name} · {r.notice.ex_date} · 每份{r.notice.per_share}</span><Button small disabled={busy} onClick={()=>ignore(r,false)}>恢复提示</Button></div>)}</details>}</>;
}
export function DividendInbox({pid,version,action,onReload,onClose}:Obj){const[rows,R]=useState<Obj[]|null>(null),[error,E]=useState('');useEffect(()=>{let active=true;R(null);api('/portfolios/'+pid+'/dividends').then(v=>{if(active)R(v);}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[pid,version]);return <Modal title="分红确认与历史重算" onClose={onClose} wide><p className="muted small">补录、更正或取消买卖后自动重算登记日份额；已有分红可按实际到账保留并完成核对，或核对差额后更正原流水。</p><ErrorBox error={error}/>{rows?<DividendNotices rows={rows} action={action} onReload={onReload}/>:<p>读取本组合分红…</p>}</Modal>;}
