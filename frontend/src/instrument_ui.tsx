import {RefreshCw} from 'lucide-react';
import {marketKey,marketNames,money,Obj,percent,signedMoney,tone,units} from './api';
import {Button,Panel,XirrBasis} from './components';
import {ProfitEquation,PriceProfitDetails} from './profit_ui';

export function InstrumentSummary({data,sync,compact=false}:Obj){const h=data.holding,a=h.performance||{},pending=data.dividend_candidates.filter((r:Obj)=>['pending','needs_review'].includes(r.status)).length;return <Panel className={'instrument-summary instrument-performance'+(compact?' compact':'')}><header className="instrument-summary-heading"><div><div className="instrument-identity"><span className={'market-tag market-'+marketKey(h.instrument)}>{marketNames[marketKey(h.instrument)]}</span><h2>{h.instrument.name}</h2><span className="instrument-code">{h.instrument.code}.{h.instrument.market}</span></div><p>{h.instrument.currency} · 行情截至 {h.quote?.date||'暂无行情'} · {h.quote?.source||'等待同步'}</p></div><Button small onClick={sync}><RefreshCw size={14}/>更新此标的</Button></header>
{!compact&&<><div className="instrument-return-grid">
<div className="instrument-market"><span>持仓市值 · CNY</span><strong>{money(h.value_base)}</strong><small>{units(h.quantity)}份 × {money(h.price,4)} {h.instrument.currency}</small></div>
<div className="instrument-profit"><span>累计收益 · CNY</span><strong className={tone(a.profit)}>{signedMoney(a.profit)}</strong><small>含净分红、汇兑与费税</small></div>
<div className="instrument-return-primary"><span>累计收益率</span><strong className={tone(a.return_on_cost)}>{percent(a.return_on_cost,true)}</strong><small>累计收益 ÷ 累计买入成本</small></div>
<div><span>年化收益率（XIRR）</span><strong className={tone(a.xirr)}>{percent(a.xirr,true)}</strong><small>仅实际投入资金，不含未投入现金</small><small>{a.xirr_status}</small></div></div>
<div className="instrument-profit-breakdown"><ProfitEquation value={a.profit_breakdown}/></div>
<div className="instrument-capital-heading">投入与持仓</div><dl className="instrument-summary-facts"><div><dt>累计买入成本 · CNY</dt><dd>{money(a.cost)}</dd><small>历次买入及再投，含费税；不扣卖出和分红</small></div><div><dt>净投入 · CNY</dt><dd>{money(h.net_invested_base)}</dd><small>实际投入减已回收资金；到账回款及现金分红会减少此值</small></div><div><dt>持仓内占比</dt><dd>{percent(h.holding_weight)}</dd><small>占总资产 {percent(h.weight)} · 计划占总预算 {money(h.target_pct)}%</small></div></dl>
<PriceProfitDetails value={a.profit_breakdown}/>
<XirrBasis performance={a}/>{(a.provisional||a.pending_settlement||pending>0)&&<p className="muted small">{a.provisional?'部分分红税额或除息日待核对，收益为暂计。':''}{a.pending_settlement?'存在未到账应收：已计入收益，尚未回补可投金额。':''}{pending>0?'另有'+pending+'笔分红待确认，未计入收益。':''}</p>}</>}</Panel>;}
