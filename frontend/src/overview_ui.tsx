import {money,Obj,percent,signedMoney,tone} from './api';
import {Panel,XirrBasis} from './components';
import {CurrentHoldingProfit,SummaryReturns} from './performance_summary';
import {ProfitEquation} from './profit_ui';
import {PlanFundingNotice} from './plan_ui';

export function PortfolioIntroduction({portfolio}:Obj){return <details className="portfolio-introduction"><summary>组合介绍 <small>{portfolio.description?'查看背景与策略':'暂未填写'}</small></summary>{portfolio.description?<p>{portfolio.description}</p>:<p className="muted">暂无组合介绍。</p>}</details>;}

export function OverviewSummary({dash,capital=false}:Obj){
 const a=dash.capital_summary||{},annual=dash.annual_performance,cashLabel=a.available_amount===a.account_available_cash?'剩余可投金额':'账面可用现金';
 return <Panel className={'overview-summary'+(capital?' capital-summary':'')} title="组合表现">
 <div className="summary-main">
 <div className="summary-assets"><div className="summary-label">总资产 · CNY</div><strong>{money(dash.total_assets)}</strong><small>持仓市值 + {cashLabel}{dash.receivables?.length>0?' + 未到账应收及在途':''}</small></div>
 <div className="summary-market"><div className="summary-label">持仓市值 · CNY</div><strong>{money(a.market_value)}</strong><CurrentHoldingProfit performance={a}/></div>
 <div className="summary-cumulative"><div className="summary-label">累计收益 · CNY</div><strong className={'summary-profit '+tone(a.profit)}>{signedMoney(a.profit)}</strong><ProfitEquation value={a.profit_breakdown} compact/></div>
 <SummaryReturns performance={a} annual={annual}/>
 </div>
 <dl className="summary-facts"><div><dt>总预算 · CNY</dt><dd>{money(a.planned_amount)}</dd><small>计划投入额度</small></div><div className="summary-cost"><dt>累计投入（累计买入成本） · CNY</dt><dd>{money(a.cost)}</dd><small>历次买入与再投的含费税成本</small></div><div><dt>净投入 · CNY</dt><dd>{money(a.invested_amount)}</dd><small>实际支出 − 已到账卖出回款 − 现金分红</small><small>红利再投不增加外部本金</small></div><div><dt>已投入仓位</dt><dd>{percent(a.invested_weight)}</dd><small>净投入 ÷ 总预算</small></div><div><dt>剩余可投金额 · CNY</dt><dd>{money(a.available_amount)}</dd><small>总预算 − 净投入</small><small>未到账回款不扣减净投入</small>{a.available_amount!==a.account_available_cash&&<small>账面可用现金 {money(a.account_available_cash)} CNY</small>}{['shortfall','unknown'].includes(dash.plan_funding?.status)&&<PlanFundingNotice value={dash.plan_funding} compact/>}</div></dl>
 <XirrBasis performance={a}/></Panel>;
}

export function PeriodQuality({period:r}:Obj){if(!r.estimated&&!r.incomplete)return null;return <details className="period-quality"><summary>{r.incomplete?'数据不完整':'暂估'} · 查看原因</summary><ul>{r.quality_reasons?.length?r.quality_reasons.map((q:Obj)=><li key={q.message}><b>{q.from}{q.to!==q.from?' 至 '+q.to:''}</b><span>{q.message}</span></li>):<li>该期间有缺失或待核对数据，请查看组合的数据提示。</li>}</ul></details>;}



export function CurveQuality({quality,onReminders,onSettings,hasTodos}:Obj){if(!quality?.missing_days)return null;return <div className="curve-warning" role="status"><b>部分组合收益曲线暂无法展示：{quality.from} 至 {quality.to}</b><ul>{quality.reasons.map((r:Obj)=><li key={r.message}>{r.from}{r.to!==r.from?' 至 '+r.to:''}：{r.message}</li>)}</ul><p>{quality.note}</p><button className="text-button" onClick={hasTodos?onReminders:onSettings}>{hasTodos?'前往提醒中心核对 →':'前往全局设置补齐历史数据 →'}</button></div>;}
