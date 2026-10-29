import {useEffect,useState} from 'react';
import {api,kindNames,money,Obj,percent,signedMoney,tone,units,exactMoney} from './api';
import {Button,ErrorBox,Metric,Modal,Notice,XirrBasis} from './components';
import './planning.css';
import {SplitEffectView} from './split_ui';

export function TradeDetailDrawer({pid,version,selected,open,onClose,onSelect,action}:Obj){
 const [data,Data]=useState<Obj|null>(null),[error,Error]=useState(''),[retry,Retry]=useState(0);
 useEffect(()=>{if(!open||!pid||!selected)return;let active=true;Data(null);Error('');api('/portfolios/'+pid+'/transactions/'+selected).then(v=>{if(active)Data(v);}).catch(e=>{if(active)Error(e.message);});return()=>{active=false;};},[pid,version,selected,open,retry]);
 if(!open)return null;
 const row=data?.portfolio_id===pid&&data?.event.id===selected?data:null,neighbors=row?.neighbors||[],index=neighbors.findIndex((r:Obj)=>r.id===selected);
 const event=row?.event,f=row?.facts,dividend=event&&['DIVIDEND','REINVEST'].includes(event.kind);
 return <Modal title="交易收益与修订记录" subtitle={event?event.date+' '+kindNames[event.kind]+' · '+(row?.instrument?.name||'组合资金'):'读取所选流水'} onClose={onClose} wide className="trade-detail-drawer">
  <ErrorBox error={error}/>{error&&<Button small onClick={()=>Retry(n=>n+1)}>重试读取详情</Button>}
  {!row?!error&&<p role="status">读取交易详情…</p>:<div className="trade-detail" data-transaction-selected={event.id}>
   {neighbors.length>0&&<div className="trade-switch"><Button small disabled={index<=0} onClick={()=>onSelect(neighbors[index-1].id)}>上一笔</Button><select aria-label="侧栏选择交易" value={selected} onChange={e=>onSelect(e.target.value)}>{neighbors.map((n:Obj)=><option key={n.id} value={n.id}>{n.date} {kindNames[n.kind]} · {n.id.slice(0,8)}</option>)}</select><Button small disabled={index<0||index>=neighbors.length-1} onClick={()=>onSelect(neighbors[index+1].id)}>下一笔</Button></div>}
   <p className="muted small">当前流水 {event.id.slice(0,8)}{f.quantity?' · '+units(f.quantity)+'份 · 未含费税单价 '+money(f.unit_price,6)+' '+f.currency:''}{row.cancelled?' · 已取消':''}</p>
   {f.split?<SplitEffectView value={f.split}/>:row.actual?<ActualTradeResult row={row} rows={neighbors} onSelect={onSelect} action={action}/>:<div className="metrics three"><Metric label={f.amount_label+' · CNY'} value={f.base_total}/><div className="metric"><div>手续费及税费 · {f.currency}</div><strong>{money(f.fees_native)}</strong></div><div className="metric"><div>交易锁定汇率</div><strong>{money(f.fx_rate,6)}</strong><small>1 {f.currency} = {money(f.fx_rate,6)} CNY</small></div></div>}
   {dividend&&<p className="transaction-dividend-equation">税前 {exactMoney(f.dividend_gross_native)} {f.currency} − 税费 {exactMoney(f.dividend_tax_native)} {f.currency} = 税后 {exactMoney(f.dividend_net_native)} {f.currency} · 到账 {event.pay_date}</p>}
   {row.forward_performance&&<ForwardMarketPerformance value={row.forward_performance}/>}
   <div className="reason-block"><small>当时的理由 · 原始交易记录</small><p>{event.reason||'当时未填写理由'}</p></div>
   <section className="transaction-revisions" aria-label="本笔交易修订记录"><h3>修订记录</h3>{row.revisions.length?row.revisions.map((r:Obj)=><div className="audit-record" key={r.id}><header><b>{r.reason}</b><small>{r.created_at.replace('T',' ').slice(0,19)}</small></header><details><summary>查看修订前后原始字段</summary><pre>{JSON.stringify({before:r.before_data,after:r.after_data},null,2)}</pre></details></div>):<p className="muted">暂无修订记录。</p>}</section>
   <footer className="form-actions"><Button onClick={onClose}>关闭侧栏</Button></footer>
  </div>}
 </Modal>;
}

function ForwardMarketPerformance({value:v}:{value:Obj}){
 return <section className="forward-performance" aria-label="交易后行情表现"><h3>交易后行情表现</h3>
 <p className="muted small">{v.basis_label} · 以交易日收盘价 / 净值为起点，按自然月观察。该参考不计入上方实际交易收益。</p>
 <div className="forward-windows">{v.windows.map((w:Obj)=><div className="forward-window" key={w.months}>
  <span>{w.months}个月后</span><strong className={w.return===null?'pending':tone(w.return)}>{w.return===null?w.status_label:percent(w.return,true)}</strong>
  <small>目标日期 {w.target_date}{w.date?' · 实际行情日 '+w.date:''}</small>
  {w.return!==null&&<><small>{v.base_date} 至 {w.date} · {money(v.base_price,4)} → {money(w.price,4)} {v.currency}</small>
  {w.relative_trade_return!==null&&<small>相对实际成交价参考 {percent(w.relative_trade_return,true)}</small>}<small>{v.base_source===w.source?w.source:v.base_source+' → '+w.source}</small></>}
 </div>)}</div><p className="muted small">到期日休市时，取随后7日内首个可得行情日；未到期或缺行情时保留未知。卖出后的涨跌仍表示标的行情方向。</p></section>;
}

export function TodoActions({todos,action}:Obj){if(!todos?.length)return null;return <div className="todo-actions">{todos.map((t:Obj)=><div key={t.id}><span>{t.title}</span><Button small onClick={()=>t.type==='confirm_split'?action('event',{initialKind:'SPLIT',instrumentId:t.instrument_id,prefill:t.prefill,splitNotice:t.notice}):action('event',{entity:t.event,splitNotice:t.notice})}>{t.label||(t.type==='confirm_split'?'核对送转/拆股份额':'核对这笔分红')}</Button></div>)}</div>;}
export function ActualTradeResult({row,rows,onSelect,action}:Obj){const a=row.actual;if(!a)return null;const sell=row.event.kind==='SELL';return <div className="actual-trade-result"><div className="metrics trade-result-metrics"><Metric label={a.closed?'截至平仓累计收益 · CNY':'截至当前累计收益 · CNY'} value={a.profit} positive note={a.status}/><div className="metric"><div>累计收益率 · 含确认净分红与关联费税</div><strong className={tone(a.return_on_cost)}>{percent(a.return_on_cost,true)}</strong><small>买入成本 {money(a.cost)} CNY</small></div><div className="metric"><div>年化收益率（XIRR）</div><strong className={tone(a.xirr)}>{percent(a.xirr,true)}</strong><small>{a.xirr_status}</small></div>{!sell&&<Metric label="剩余批次市值 · CNY" value={a.market_value} note={'未卖出 '+units(a.remaining_quantity)+'份'}/>}</div><TodoActions todos={row.todos?.filter((t:Obj)=>t.type!=='verify_dividend')} action={action}/>{a.unallocated_notes?.map((n:string)=><Notice key={n}>{n}</Notice>)}{a.provisional&&<Notice>相关分红的税额或除息日尚未核对，当前盈亏为暂计。<TodoActions todos={row.todos} action={action}/></Notice>}<XirrBasis performance={a}/><p className="muted small">按本组合实际买入成本、卖出净回款、确认净分红和关联费税计算。已平仓不再随证券价格变化；历史更正、分红和补扣税仍按真实记录重算。</p>{a.parts.length>0&&<details className="advanced" open={sell}><summary>{sell?'本次卖出关联的买入批次':'本笔买入已关联的卖出'} · {a.parts.length}笔</summary><div className="table-wrap"><table><thead><tr><th>{sell?'买入批次':'卖出记录'}</th><th>卖出份额</th><th>分摊买入成本 · CNY</th><th>卖出净回款 · CNY</th><th>含息费税盈利 · CNY</th></tr></thead><tbody>{a.parts.map((p:Obj)=>{const target=sell?p.buy_event_id:p.sell_event_id,ref=rows?.find((r:Obj)=>r.id===target);return <tr key={p.buy_event_id+':'+p.sell_event_id}><td>{ref?<button className="text-button" onClick={()=>onSelect(target)}>{sell?p.buy_date:p.date} · {target.slice(0,8)}</button>:<span>{target.slice(0,8)}</span>}</td><td>{units(p.quantity)}</td><td>{money(p.cost)}</td><td>{money(p.proceeds)}</td><td className={tone(p.profit)}>{signedMoney(p.profit)}</td></tr>;})}</tbody></table></div></details>}{sell&&a.cash_flows&&<details className="advanced"><summary>核对 XIRR 现金流（实际天数 / 365）</summary><div className="table-wrap"><table><thead><tr><th>现金日期</th><th>投入为负，回收为正 · CNY</th></tr></thead><tbody>{a.cash_flows.map((f:Obj,n:number)=><tr key={n}><td>{f.date}</td><td>{signedMoney(f.amount)}</td></tr>)}</tbody></table></div></details>}</div>}

