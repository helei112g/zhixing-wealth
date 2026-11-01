import {RefreshCw} from 'lucide-react';
import {marketKey,marketNames,money,Obj,percent,signedMoney,tone,units} from './api';
import {Button,Panel,XirrBasis} from './components';
import {CurrentHoldingProfit,InvestmentProgress,SummaryReturns} from './performance_summary';
import {ProfitEquation,PriceProfitDetails} from './profit_ui';

export function InstrumentSummary({data,sync,compact=false}:Obj){const h=data.holding,a=h.performance||{},pending=data.dividend_candidates.filter((r:Obj)=>['pending','needs_review'].includes(r.status)).length;return <Panel className={'instrument-summary instrument-performance'+(compact?' compact':'')}><header className="instrument-summary-heading"><div><div className="instrument-identity"><span className={'market-tag market-'+marketKey(h.instrument)}>{marketNames[marketKey(h.instrument)]}</span><h2>{h.instrument.name}</h2><span className="instrument-code">{h.instrument.code}.{h.instrument.market}</span></div><p>{h.instrument.currency} · 行情截至 {h.quote?.date||'暂无行情'} · {h.quote?.source||'等待同步'}</p></div><Button small onClick={sync}><RefreshCw size={14}/>更新此标的</Button></header>
{!compact&&<><div className="instrument-return-grid">
<div className="instrument-market"><span>持仓市值 · CNY</span><strong>{money(h.value_base)}</strong><small>{units(h.quantity)}份 × {money(h.price,4)} {h.instrument.currency}</small><CurrentHoldingProfit performance={a}/></div>
<div className="instrument-profit"><span>累计收益 · CNY</span><strong className={tone(a.profit)}>{signedMoney(a.profit)}</strong><ProfitEquation value={a.profit_breakdown} compact/></div>
<SummaryReturns performance={a}/></div>

<div className="instrument-capital-heading">投入与持仓</div><dl className="instrument-summary-facts"><div><dt>累计买入成本 · CNY</dt><dd>{money(a.cost)}</dd><small>历次买入与再投，含费税</small></div><div><dt>净投入 · CNY</dt><dd>{money(h.net_invested_base)}</dd><small>实际支出 − 已到账回款和现金分红</small></div><div><dt>持仓内占比</dt><dd>{percent(h.holding_weight)}</dd><small>占总资产 {percent(h.weight)} · 计划占总预算 {money(h.target_pct)}%</small></div></dl>
<div className="instrument-investment-plan" data-investment-plan><div><span>计划投资金额 · CNY</span><b>{money(h.target_amount)}</b></div><div><span>实际净投入 · CNY</span><b>{money(h.net_invested_base)}</b></div><InvestmentProgress holding={h}/></div>
<PriceProfitDetails value={a.profit_breakdown}/>
<XirrBasis performance={a}/>{(a.provisional||a.pending_settlement||pending>0)&&<p className="muted small">{a.provisional?'部分分红税额或除息日待核对，收益为暂计。':''}{a.pending_settlement?'存在未到账应收：已计入收益，尚未回补可投金额。':''}{pending>0?'另有'+pending+'笔分红待确认，未计入收益。':''}</p>}</>}</Panel>;}
