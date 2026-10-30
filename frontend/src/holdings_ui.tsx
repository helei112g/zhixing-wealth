import {CSSProperties,useEffect,useState} from 'react';
import {PieChart} from 'lucide-react';
import {marketKey,marketNames,money,Obj,percent,signedMoney,tone,units} from './api';
import {Button,Empty,Panel} from './components';

export function HoldingsTable({dash,action,openDetail,tags}:Obj){
 const[page,Page]=useState(1),[pageSize,Size]=useState(20),total=dash.holdings.length,pages=Math.max(1,Math.ceil(total/pageSize)),current=Math.min(page,pages),shown=dash.holdings.slice((current-1)*pageSize,current*pageSize);
 useEffect(()=>Page(p=>Math.min(p,pages)),[pages]);
 return <Panel title="当前持仓与投入计划" right={<Button small onClick={tags}><PieChart size={14}/>标签分析</Button>}>
 <p className="muted small holdings-basis">净投入是实际投入减已回收资金；成本价是剩余持仓的含费税平均成本。份额 × 现价 = 市值，份额 × 成本价 = 持仓成本；外币市值按估值汇率折算为 CNY。</p>
 {!dash.holdings.length?<Empty title="还没有持仓">点击页面右上角“记录一笔”开始买入；保存时自动建立标的。</Empty>:<div className="table-wrap"><table className="holdings-table"><thead><tr>
 {['标的','持仓市值 · CNY','累计收益率 / 金额 · CNY','年化收益率（XIRR）','累计净分红 · CNY','持仓占比','总资产占比','投入计划 · CNY','计划进度','操作'].map(label=><th scope="col" key={label}>{label}</th>)}
 </tr></thead><tbody>{shown.map((h:Obj)=>{const hasPlan=Number(h.target_amount)>0,remaining=h.net_invested_base==null?null:Number(h.target_amount)-Number(h.net_invested_base);return <tr key={h.instrument_id} className="holding-row" tabIndex={0} aria-label={'查看'+h.instrument.name+'持仓详情'} onClick={()=>openDetail(h.instrument_id)} onKeyDown={e=>{if(e.target===e.currentTarget&&(e.key==='Enter'||e.key===' ')){e.preventDefault();openDetail(h.instrument_id);}}}>
 <td className="holding-identity"><span className={'market-tag market-'+marketKey(h.instrument)}>{marketNames[marketKey(h.instrument)]}</span><button className="text-button" onClick={e=>{e.stopPropagation();openDetail(h.instrument_id);}}>{h.instrument.name}</button><small>{h.instrument.code}.{h.instrument.market} · {h.instrument.currency}{Number(h.quantity)===0?' · 已清仓 / 尚未买入':''}</small></td>
 <td className="holding-valuation"><b>{money(h.value_base)}</b><small>{units(h.quantity)}份 × 现价 {money(h.price,4)} {h.instrument.currency}</small><small>成本价 {money(h.average_cost,4)} {h.instrument.currency}/份 · 含费税</small>{h.instrument.currency!=='CNY'&&<small>估值汇率 1 {h.instrument.currency} = {money(h.fx?.rate,6)} CNY</small>}</td>
 <td className="holding-return"><b className={tone(h.performance?.return_on_cost)}>{percent(h.performance?.return_on_cost,true)}</b><small className="muted">{signedMoney(h.performance?.profit)}{h.performance?.provisional?' · 暂计':''}</small></td>
 <td className={tone(h.performance?.xirr)} title={h.performance?.xirr_status}>{percent(h.performance?.xirr,true)}<small>{h.performance?.xirr==null?'待数据 / 无有效解':'仅实际投入资金'}</small></td>
 <td>{money(h.performance?.dividends_net)}<small>已计入累计收益</small></td>
 <td><b>{percent(h.holding_weight)}</b><small>占全部持仓市值</small></td><td>{percent(h.weight)}<small>含剩余可投及应收</small></td>
 <td className="holding-plan"><b>{money(h.target_amount)}</b><small>计划占总预算 {money(h.target_pct)}%</small><small>净投入 {money(h.net_invested_base)}</small><small>已投占总预算 {percent(h.input_weight)}</small></td>
 <td className={'holding-progress'+(hasPlan&&remaining!==null&&remaining<0?' over-plan':'')}><b>{hasPlan?percent(h.completion):'未设计划'}</b>{hasPlan&&h.completion!=null&&<div className="bar" role="progressbar" aria-label={h.instrument.name+'计划完成度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.max(0,Math.min(100,Number(h.completion)*100))} aria-valuetext={percent(h.completion)}><i style={{width:Math.max(0,Math.min(100,Number(h.completion)*100))+'%'}}/></div>}<small>{!hasPlan?'设置目标后计算进度':remaining==null?'剩余需投入 —':remaining>=0?'剩余需投入 '+money(remaining):'超出计划 '+money(-remaining)}</small></td>
 <td><Button small onClick={e=>{e.stopPropagation();action('plan',{entity:h});}}>设置计划</Button></td></tr>;})}</tbody></table></div>}
 {total>0&&<nav className="holdings-pagination" aria-label="持仓与投入计划分页"><span aria-live="polite">共 {total} 只 · 第 {current} / {pages} 页</span><label>每页<select aria-label="持仓每页条数" value={pageSize} onChange={e=>{Size(Number(e.target.value));Page(1);}}>{[10,20,50].map(n=><option key={n} value={n}>{n} 条</option>)}</select></label><div className="inline-actions"><Button small disabled={current===1} onClick={()=>Page(current-1)}>上一页</Button><Button small disabled={current===pages} onClick={()=>Page(current+1)}>下一页</Button></div></nav>}
 <p className="muted small">点击标的行查看详情。持仓占比以全部证券市值为分母，总资产占比包含剩余可投金额与应收。计划进度 = 净投入 ÷ 计划金额。</p>
 </Panel>;}

const metrics:Record<string,{label:string;note:string}>={return_on_cost:{label:'累计收益率',note:'累计收益 ÷ 累计买入成本，含净分红、汇兑与费税。'},profit:{label:'累计收益金额',note:'按 CNY 比较累计收益金额，含净分红、汇兑与费税。'},xirr:{label:'年化收益率（XIRR）',note:'仅按实际投入现金流计算；持有期不同，年化不代表未来收益。'}};
export function HoldingsRanking({dash,openDetail}:Obj){const[metric,Metric]=useState('return_on_cost'),[scope,Scope]=useState('all');
 const rows=dash.holdings.filter((h:Obj)=>Number(h.performance?.cost)>0&&(scope==='all'||Number(h.quantity)>0)).slice().sort((a:Obj,b:Obj)=>{const av=a.performance?.[metric],bv=b.performance?.[metric];if(av==null||bv==null)return av==null?(bv==null?a.instrument_id.localeCompare(b.instrument_id):1):-1;return Number(bv)-Number(av)||a.instrument_id.localeCompare(b.instrument_id);});
 const values=rows.map((h:Obj)=>Number(h.performance?.[metric]||0)),lo=Math.min(0,...values),hi=Math.max(0,...values),span=hi-lo||1,zero=hi===lo?50:-lo/span*100;
 const valueText=(h:Obj)=>metric==='profit'?signedMoney(h.performance?.profit):percent(h.performance?.[metric],true);
 return <Panel className="holdings-ranking" title="标的收益排行榜" right={<div className="ranking-filters"><label>排序指标<select aria-label="排行榜排序指标" value={metric} onChange={e=>Metric(e.target.value)}>{Object.entries(metrics).map(([key,v])=><option value={key} key={key}>{v.label}</option>)}</select></label><label>标的范围<select aria-label="排行榜标的范围" value={scope} onChange={e=>Scope(e.target.value)}><option value="all">全部已投资标的</option><option value="holding">仅当前持仓</option></select></label></div>}>
 <p className="muted small">截至 {dash.date} · 共 {rows.length} 只 · {metrics[metric].note} 缺少有效数据的标的排在末尾。</p>
 {!rows.length?<Empty title="暂无可排名的标的">有实际买入记录后展示排行榜。</Empty>:<><div className="ranking-axis"><span>{metric==='profit'?'累计收益 · CNY':metrics[metric].label} · 从高到低</span><span>亏损 ← 0 → 盈利</span></div><div className="ranking-scroll"><ol className="ranking-list" style={{'--ranking-columns':Math.ceil(rows.length/50),'--ranking-rows':Math.min(rows.length,50)} as CSSProperties}>{rows.map((h:Obj,n:number)=>{const raw=h.performance?.[metric],value=raw==null?null:Number(raw);return <li key={h.instrument_id} className="ranking-row" data-ranking-id={h.instrument_id} data-ranking-value={raw??''}>
 <span className={'rank-number'+(n<3?' rank-top':'')}>{n+1}</span><button className="ranking-name text-button" title={h.instrument.name+' · '+h.instrument.code+'.'+h.instrument.market} onClick={()=>openDetail(h.instrument_id)}><span>{h.instrument.name}</span><small>{h.instrument.code}.{h.instrument.market} · {Number(h.quantity)===0?'已清仓':'持有中'}</small></button>
 <div className="ranking-track" aria-hidden="true"><i className="ranking-zero" style={{left:zero+'%'}}/>{value!==null&&<i className={'ranking-bar '+tone(value)} style={{left:(value>=0?zero:(value-lo)/span*100)+'%',width:Math.abs(value)/span*100+'%'}}/>}</div>
 <div className="ranking-value"><b className={tone(raw)}>{valueText(h)}</b><small>{raw==null?'暂无有效值':h.quote?.fallback?'成交价暂估':h.performance?.provisional?'暂计':metric==='profit'?'CNY':signedMoney(h.performance?.profit)+' CNY'}</small></div>
 </li>;})}</ol></div></>}
 </Panel>;
}
