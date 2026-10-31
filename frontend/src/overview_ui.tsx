import {money,Obj,percent,signedMoney,tone} from './api';
import {Button,Panel,XirrBasis} from './components';
import {ProfitEquation} from './profit_ui';
import {PlanFundingNotice} from './plan_ui';

export function PortfolioIntroduction({portfolio,onEdit}:Obj){return <details className="portfolio-introduction"><summary>组合介绍 <small>{portfolio.description?'查看背景与策略':'暂未填写'}</small></summary>{portfolio.description?<p>{portfolio.description}</p>:<p className="muted">写下组合的背景、目标与投资策略，便于随时回顾。</p>}<Button small onClick={onEdit}>编辑组合介绍</Button></details>;}

export function OverviewSummary({dash,capital=false}:Obj){
 const a=dash.capital_summary||{},annual=dash.annual_performance;
 return <Panel className={'overview-summary'+(capital?' capital-summary':'')} title="组合表现">
 <div className="summary-main">
 <div className="summary-assets"><div className="summary-label">总资产 · CNY</div><strong>{money(dash.total_assets)}</strong><small>持仓市值 + 剩余可投金额{dash.receivables?.length>0?' + 未到账应收及在途':''}</small></div>
 <div className="summary-market"><div className="summary-label">持仓市值 · CNY</div><strong>{money(a.market_value)}</strong><small>现有份额 × 当前估值；随行情和持仓变动</small></div>
 <div className="summary-cumulative"><div className="summary-label">累计收益 · CNY</div><strong className={'summary-profit '+tone(a.profit)}>{signedMoney(a.profit)}</strong><ProfitEquation value={a.profit_breakdown} compact/></div>
 <div className="summary-return"><div className="summary-label">累计收益率</div><strong className={tone(a.return_on_cost)}>{percent(a.return_on_cost,true)}</strong><small>累计收益 ÷ 累计买入成本</small><small>仅实际投入资金，不含未投入现金</small></div>
 <div className="summary-annual"><div className="summary-label">年化收益率（XIRR）</div><strong className={tone(a.xirr)}>{percent(a.xirr,true)}</strong><div className="summary-goal"><span>目标 <b>{percent(annual?.target)}</b></span><span className={tone(annual?.gap)}>相差 {annual?.gap==null?'—':(Number(annual.gap)>0?'+':'')+money(Number(annual.gap)*100)+' 个百分点'}</span></div><small>按实际投入与回收日期年化，不含未投入现金</small><small>{a.xirr_status}{a.provisional?' · 含待核对数据':''}</small></div>
 </div>
 <dl className="summary-facts"><div><dt>总预算 · CNY</dt><dd>{money(a.planned_amount)}</dd><small>计划投入额度；买卖及分红不改变</small></div><div className="summary-cost"><dt>累计投入（累计买入成本） · CNY</dt><dd>{money(a.cost)}</dd><small>买入及再投的含费税成本累计；卖出、分红不扣减</small><small>再投不代表新增个人本金</small></div><div><dt>净投入 · CNY</dt><dd>{money(a.invested_amount)}</dd><small>总预算 − 剩余可投金额</small><small>买入增加，卖出及现金分红到账后减少</small></div><div><dt>已投入仓位</dt><dd>{percent(a.invested_weight)}</dd><small>净投入 ÷ 总预算；不等同于市值占比</small></div><div><dt>剩余可投金额 · CNY</dt><dd>{money(a.available_amount)}</dd><small>买入减少，卖出及现金分红到账后增加</small><small>未到账应收不计入已回收现金</small>{['shortfall','unknown'].includes(dash.plan_funding?.status)&&<PlanFundingNotice value={dash.plan_funding} compact/>}</div></dl>
 <XirrBasis performance={a}/></Panel>;
}

export function PeriodQuality({period:r}:Obj){if(!r.estimated&&!r.incomplete)return null;return <details className="period-quality"><summary>{r.incomplete?'数据不完整':'暂估'} · 查看原因</summary><ul>{r.quality_reasons?.length?r.quality_reasons.map((q:Obj)=><li key={q.message}><b>{q.from}{q.to!==q.from?' 至 '+q.to:''}</b><span>{q.message}</span></li>):<li>该期间有缺失或待核对数据，请查看组合的数据提示。</li>}</ul></details>;}

export function CapitalSummary({dash}:Obj){return <OverviewSummary dash={dash} capital/>;}

export function CurveQuality({quality,onReminders,onSettings,hasTodos}:Obj){if(!quality?.missing_days)return null;return <div className="curve-warning" role="status"><b>部分组合收益曲线暂无法展示：{quality.from} 至 {quality.to}</b><ul>{quality.reasons.map((r:Obj)=><li key={r.message}>{r.from}{r.to!==r.from?' 至 '+r.to:''}：{r.message}</li>)}</ul><p>{quality.note}</p><button className="text-button" onClick={hasTodos?onReminders:onSettings}>{hasTodos?'前往提醒中心核对 →':'前往全局设置补齐历史数据 →'}</button></div>;}
