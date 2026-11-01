import {money,Obj,percent,signedMoney,tone} from './api';

export function SummaryReturns({performance:a,annual}:Obj){return <div className="summary-return summary-returns" data-return-summary>
 <div className="summary-label">累计收益率</div><strong className={tone(a.return_on_cost)}>{percent(a.return_on_cost,true)}</strong>
 <small>累计收益 ÷ 累计买入成本</small>
 <div className="annual-return"><span>年化收益率（XIRR）</span><b className={tone(a.xirr)}>{percent(a.xirr,true)}</b></div>
 {annual&&<div className="summary-goal"><span>目标 <b>{percent(annual.target)}</b></span><span className={tone(annual.gap)}>相差 {annual.gap==null?'—':(Number(annual.gap)>0?'+':'')+money(Number(annual.gap)*100)+' 个百分点'}</span></div>}
 <small>仅实际投入资金，不含未投入现金</small>{(a.xirr==null||a.provisional)&&<small>{a.xirr_status}{a.provisional?' · 含待核对数据':''}</small>}
 </div>;}

export function CurrentHoldingProfit({performance:a}:Obj){return <div className="current-holding-profit" data-current-holding-profit>
 <span>当前持仓收益 <b className={tone(a.holding_profit)}>{signedMoney(a.holding_profit)} CNY</b></span>
 <small>{a.holding_profit==null?'行情或剩余成本待核对':'市值 − 剩余含费税记账成本'}</small>
 {a.xirr_status?.includes('期初')&&<small>期初份额仅计算记账以来。</small>}
 <small>已卖出收益 {signedMoney(a.realized_sale_profit)} · 净分红 {signedMoney(a.dividends_net)} CNY</small>
 {a.pending_settlement&&<small>已卖出和分红收益含已确认应收。</small>}
 </div>;}

export function InvestmentProgress({holding:h}:Obj){const hasPlan=Number(h.target_amount)>0,remaining=h.net_invested_base==null?null:Number(h.target_amount)-Number(h.net_invested_base),progress=Math.max(0,Math.min(100,Number(h.completion)*100));return <div className={'investment-progress'+(hasPlan&&remaining!==null&&remaining<0?' over-plan':'')}>
 <b>{hasPlan?percent(h.completion):'未设计划'}</b>{hasPlan&&h.completion!=null&&<div className="bar" role="progressbar" aria-label={h.instrument.name+'计划完成度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-valuetext={percent(h.completion)}><i style={{width:progress+'%'}}/></div>}
 <small>{!hasPlan?'设置目标后计算进度':remaining==null?'剩余需投入 —':remaining>=0?'剩余需投入 '+money(remaining):'超出计划 '+money(-remaining)}</small>
 </div>;}
