import {Obj, money, units} from './api';
import './planning.css';

export function SplitSummary({value:s}:{value:Obj}){
 return <span className="split-summary">{units(s.quantity_before)} → {units(s.quantity_after)}份
  <small>倍数 {units(s.factor)} · {s.lots.length}笔参与</small>
  {s.status==='no_position'&&<small>除权前无持仓 · 本次不调整份额</small>}
 </span>;
}

export function SplitEffectView({value:s}:{value:Obj}){
 return <section className="split-effect" aria-label="送转股参与批次">
  <h3>{s.date} · 送转 / 拆股份额</h3><SplitSummary value={s}/>
  <p>持仓总成本 {money(s.cost_native)} {s.currency}，送转前后不变。</p>
  {s.lots.length>0&&<div className="table-wrap"><table><thead><tr><th>买入批次</th><th>送转前剩余</th><th>送转后</th><th>份额变化</th></tr></thead>
   <tbody>{s.lots.map((lot:Obj)=><tr key={lot.buy_event_id}><td>{lot.date}<small>{lot.buy_event_id.slice(0,8)}</small></td><td>{units(lot.quantity_before)}份</td><td>{units(lot.quantity_after)}份</td><td>{units(lot.quantity_change)}份</td></tr>)}</tbody></table></div>}
  <p className="muted small">按当前历史流水重算，仅除权日前剩余份额参与；此前已卖出的份额、除权日及之后新买入的份额不参与。补录、更正或取消交易后自动更新，已确认流水保留，无需再次确认。</p>
 </section>;
}
