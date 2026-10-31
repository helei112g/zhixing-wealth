import {money,Obj,signedMoney,tone} from './api';

export function ProfitEquation({value,compact=false}:Obj){const b=value;if(!b)return null;
 const split='cash_dividends_net' in b&&'reinvested_dividends_net' in b,rounding=split?b.component_rounding_adjustment:b.rounding_adjustment;
 const terms=[['price_net','价格收益','含汇兑，已扣费税'],...(split?[['cash_dividends_net','现金分红净收益','含已确认应收；到账后才回补可投金额'],['reinvested_dividends_net','红利再投资分红收益','按再投记录归集；非新增个人本金']]:[['dividends_net','累计净分红','含已确认应收；已计入收益']]),...(Number(b.other_income)!==0?[['other_income','其他收入','利息等收入']]:[])];
 return <div className={'profit-equation'+(compact?' compact':'')} data-profit-equation>
 {compact?<div className="equation-caption">= 收益构成 · CNY</div>:<div className="equation-total"><small>累计收益 · CNY</small><b className={tone(b.total)}>{signedMoney(b.total)}</b></div>}
 {!compact&&<span className="equation-symbol">=</span>}
 {terms.map(([key,label,note],i)=><div className="equation-part" key={key}>{i>0&&<span className="equation-symbol">+</span>}<div><small>{label}</small><b className={tone(b[key])}>{signedMoney(b[key])}</b>{!compact&&<small className="muted">{note}</small>}</div></div>)}
 {split&&<small className="muted equation-dividend-total">累计净分红合计 {signedMoney(b.dividends_net)} CNY，已包含在累计收益中。</small>}
 {compact&&<small className="muted equation-basis">价格收益含汇兑、已扣费税；分红含已确认应收，再投非新增本金。</small>}
 {Number(rounding)!==0&&rounding!=null&&<small className="muted equation-rounding">两位显示舍入差 {signedMoney(rounding)}；总额按未舍入值计算</small>}
 {!b.complete&&<small className="muted equation-rounding">数据不完整，未知项不按0计算。</small>}
 </div>;
}

export function PriceProfitDetails({value}:Obj){const b=value;if(!b)return null;return <details className="price-profit-details" open><summary>价格收益怎么算 · CNY</summary><p>持仓市值 <b>{money(b.market_value)}</b> + 卖出净回款（含应收） <b>{money(b.sale_proceeds)}</b> − 累计买入成本 <b>{money(b.buy_cost)}</b> − 其他非分红费用 <b>{money(b.extra_costs)}</b> = 价格收益 <b className={tone(b.price_net)}>{signedMoney(b.price_net)}</b></p><small>{b.price_basis}。买卖费税分别包含在买入成本和卖出净回款中，不再重复扣除。各金额显示保留两位小数。</small></details>;}
