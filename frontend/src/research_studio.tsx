import React,{useEffect,useId,useRef,useState} from 'react';
import {Info} from 'lucide-react';
import {Bar,BarChart,Cell,CartesianGrid,Line,LineChart,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {api,money,Obj,percent,query} from './api';
import {Button,Empty,ErrorBox,Field,Modal,Notice,Panel} from './components';
import {LinkedHistory,Scenario,ValuationTrend} from './research_charts';

const num=(v:any,d=2)=>v==null?'-':money(v,d),pct=(v:any)=>v==null?'-':percent(v);
export const moduleLabels:Obj={index_info:'指数基本信息',price:'价格日线',volatility:'指数波动率',total_return:'全收益日线',valuations:'日频估值与场内基金/市值',memberships:'当前成分与权重',historical_memberships:'十年历史成分',index_financials:'指数季度/TTM财务',market_activity:'融资与北向持仓',tracking_funds:'跟踪基金',stock_valuations:'公司估值',financials:'公司财务',historical_financials:'历史财务备用汇总'};
const methodNames:Obj={mcw:'总市值加权',ew:'等权',ewpvo:'正数等权',median:'中位数',overall:'旧源整体法',arithmetic:'旧源算术平均'};
const metricNames:Obj={pe:'PE TTM',pb:'PB',ps:'PS TTM',dividend_yield:'股息率'};
const methodExplanations:Record<string,{purpose:string;algorithm:string;example:string}>={
 mcw:{
  purpose:'看经济规模较大的资产如何定价。',
  algorithm:'先汇总成分公司的市值和对应财务数据，再计算整体估值，大公司影响更大。',
  example:'以 PE TTM 为例：总市值合计 ÷ 近12个月净利润合计，包含亏损公司的利润。',
 },
 ew:{
  purpose:'看等额组合与规模加权组合有什么差异。',
  algorithm:'假设每家公司投入相同金额，先汇总这些投资对应的财务份额，再计算组合估值。',
  example:'以 PE TTM 为例：公司数 ÷ 各公司 PE 倒数之和，包含负值；不是把 PE 直接做算术平均。',
 },
 ewpvo:{
  purpose:'观察盈利公司组成的等额组合估值。',
  algorithm:'按理杏仁口径剔除该指标非正的样本，再计算等额组合估值。',
  example:'PE TTM 先剔除亏损公司，再对盈利收益率等权平均并取倒数。',
 },
 median:{
  purpose:'看估值是否扩散到典型公司。',
  algorithm:'将参与计算的公司估值排序，取居中位置；每家公司一票，不按市值加权。',
  example:'反映典型公司的估值水平，较少受少数极端估值影响；与总市值加权对照，观察变化是否只集中在大公司。',
 },
};
const colors=['#365f91','#bc8650','#298879','#8866a6','#6e7f8b'];
export const windowName=(w:number)=>w?w+'年':'全历史';

export function TokenSettings(){
 const [state,State]=useState<Obj|null>(null);
 const [token,Token]=useState('');
 const [error,Error]=useState('');
 const [busy,Busy]=useState(false);
 const [message,Message]=useState('');
 const load=async()=>State(await api('/settings/lixinger'));
 useEffect(()=>{load().catch(e=>Error(e.message));},[]);

 async function saveToken(){
  Busy(true);
  Error('');
  Message('');
  try{
   await api('/settings/lixinger/token',{token,expected_revision:state?.revision},'PUT');
   Token('');
   await load();
   Message('Token已保存到本机文件；尚未发起采集。');
  }catch(e:any){
   Error(e.message);
   await load().catch(()=>{});
  }finally{
   Token('');
   Busy(false);
  }
 }

 return <Panel title="理杏仁数据源">
  <ErrorBox error={error}/>
  <p className="muted small">Token仅保存到本机文件，每次采集由本机进程读取。保存不会发起请求。</p>
  <p role="status">{state?.configured?'已配置本机Token':'尚未配置Token'}</p>
  <div className="studio-form-grid">
   <Field label="理杏仁Token"><input type="password" aria-label="理杏仁Token" autoComplete="off" spellCheck={false} value={token} disabled={busy} onChange={e=>Token(e.target.value)}/></Field>
   <Button disabled={busy||!state||token.length<8} onClick={saveToken}>保存到本机文件</Button>
  </div>
  {state&&<p className="muted small token-file-path">保存路径：{state.path}</p>}
  {message&&<p role="status">{message}</p>}
 </Panel>;
}

export function CollectionDialog({catalog,profile,onClose,onSaved}:Obj){
 const [code,Code]=useState(profile?.code||''),[modules,Modules]=useState<string[]>((profile?.subscription?.modules||catalog.default_modules).filter((m:string)=>m!=='tracking_funds')),[limited,Limited]=useState(true),[start,Start]=useState(profile?.subscription?.history_start||new Date(Date.now()-30*86400000).toISOString().slice(0,10)),[enabled,Enabled]=useState(profile?.subscription?.enabled??true),[preview,P]=useState<Obj|null>(null),[busy,B]=useState(false),[error,E]=useState('');
 const generation=useRef(0);
 useEffect(()=>{if(profile)Limited(Boolean(profile.subscription?.history_start));},[]);
 function invalidate(){generation.current++;P(null);E('');}
 async function inspect(){B(true);E('');const turn=generation.current;try{const v=await api('/research/collection/preview',{code,modules,history_start:limited?start:null});if(turn===generation.current)P(v);}catch(e:any){E(e.message);}finally{B(false);}}
 async function save(){if(!preview)return;B(true);E('');try{const v=await api('/research/collection',{code:preview.profile.code,modules:preview.modules,history_start:preview.history_start,enabled,expected_research_revision:preview.research_revision},'PUT');onSaved(v);}catch(e:any){E(e.message);P(null);}finally{B(false);}}
 return <Modal title={profile?'管理指数采集':'添加指数采集'} onClose={onClose} busy={busy}><ErrorBox error={error}/><Field label="指数代码或名称"><input aria-label="采集指数代码" list="index-catalog-options" value={code} disabled={busy||Boolean(profile)} onChange={e=>{Code(e.target.value);invalidate();}}/><datalist id="index-catalog-options">{catalog.items.map((p:Obj)=><option key={p.code} value={p.code}>{p.name}</option>)}</datalist></Field><p className="muted small">目录只用于身份识别，未添加的指数不会采集。试用先从一个指数、较小日期范围开始。</p><div className="collection-modules">{catalog.modules.filter((m:string)=>m!=='tracking_funds').map((m:string)=><label className="check" key={m}><input type="checkbox" aria-label={moduleLabels[m]} checked={modules.includes(m)} disabled={busy||m==='index_info'} onChange={e=>{Modules(e.target.checked?[...modules,m]:modules.filter(x=>x!==m));invalidate();}}/><span><b>{moduleLabels[m]}</b><small>{catalog.module_sources?.[m]}</small></span></label>)}</div><div className="segmented" aria-label="采集历史范围"><button type="button" disabled={busy} aria-pressed={!limited} className={!limited?'active':''} onClick={()=>{Limited(false);invalidate();}}>全历史</button><button type="button" disabled={busy} aria-pressed={limited} className={limited?'active':''} onClick={()=>{Limited(true);invalidate();}}>指定起始日</button></div>{limited&&<Field label="采集起始日"><input aria-label="采集起始日" type="date" value={start} disabled={busy} onChange={e=>{Start(e.target.value);invalidate();}}/></Field>}<label className="check"><input type="checkbox" checked={enabled} disabled={busy} onChange={e=>Enabled(e.target.checked)}/>启用此指数；关闭即暂停并保留历史</label><Button disabled={busy||!code||!modules.length} onClick={inspect}>预览范围与调用量</Button>{preview&&<div className="collection-preview"><h3>{preview.profile.name} · {preview.profile.code}</h3><p>价格 {preview.profile.code} / 全收益 {preview.profile.return_code}</p><p>{preview.effective_start} → {preview.end}；历史成分最多十年。</p><div className="table-wrap"><table><thead><tr><th>端点</th><th>预计首次调用</th><th>本机余量</th></tr></thead><tbody>{preview.endpoint_budget.map((r:Obj)=><tr key={r.endpoint}><td>{r.endpoint}</td><td>{r.calls}</td><td>{r.remaining}</td></tr>)}</tbody></table></div>{preview.endpoint_budget.some((r:Obj)=>r.calls>r.remaining)&&<Notice>完整回填超过当前预算。可缩小模块/范围；分片会保留进度，额度不足不会继续请求。</Notice>}<p className="muted small">{preview.limitation}{preview.unsupported_modules?.length>0&&` 未纳入采集：${preview.unsupported_modules.map((m:string)=>moduleLabels[m]).join('、')}（跨市场尚未核验）。`}</p><Button primary disabled={busy} onClick={save}>确认保存采集设置</Button></div>}</Modal>;
}

export function HistoryWindow({value,onChange,label,windows=[3,5,10,0]}:{value:number;onChange:(value:number)=>void;label:string;windows?:number[]}){
 return <div className="segmented" aria-label={label}>{windows.map(w=>
  <button key={w} aria-pressed={value===w} className={value===w?'active':''} onClick={()=>onChange(w)}>{windowName(w)}</button>
 )}</div>;
}

export function ValuationMethods({methods,value,onChange}:{methods:string[];value:string;onChange:(method:string)=>void}){
 const [help,Help]=useState<string|null>(null);
 const id=useId();

 return <div className="segmented studio-methods" aria-label="估值方法">
  {methods.map(method=>{
   const explanation=methodExplanations[method];
   const open=help===method;
   const tooltipId=`${id}-${method}`;
   return <div className="studio-method-option" key={method}>
    <button type="button" aria-pressed={value===method} className={value===method?'active':''} onClick={()=>onChange(method)}>{methodNames[method]}</button>
    {explanation&&<div className="studio-method-help"
     onMouseEnter={()=>Help(method)}
     onMouseLeave={e=>{if(!e.currentTarget.contains(document.activeElement))Help(null);}}
     onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))Help(null);}}
     onKeyDown={e=>{if(e.key==='Escape')Help(null);}}>
     <button type="button" className="studio-method-info" aria-label={`${methodNames[method]}计算说明`} aria-describedby={open?tooltipId:undefined}
      onFocus={()=>Help(method)} onClick={()=>Help(method)}><Info size={14} aria-hidden="true"/></button>
     {open&&<div id={tooltipId} className="studio-method-tooltip" role="tooltip">
      <strong>{methodNames[method]} · {method}</strong>
      <p className="studio-method-purpose">{explanation.purpose}</p>
      <p>{explanation.algorithm}</p>
      <p>{explanation.example}</p>
      <small>采用理杏仁结果，参与样本及特殊值处理沿用源端口径。</small>
     </div>}
    </div>}
   </div>;
  })}
 </div>;
}

export const trendClass=(value:any)=>value==null?'':Number(value)>0?'positive':Number(value)<0?'negative':'';

function ValuationRanks({analysis}:Obj){
 return <details className="studio-ranks"><summary>历史估值分位对照</summary>
  <div className="table-wrap"><table><thead><tr><th>窗口</th><th>当前排名</th><th>均值 / 中位数</th><th>P10 / P25 / P75 / P90</th><th>前序样本与实际区间</th></tr></thead>
   <tbody>{[3,5,10,0].map(w=>{const v=analysis?.ranks?.[w];return <tr key={w}>
    <td>{windowName(w)}{v?.partial?' *':''}</td><td>{pct(v?.rank)}</td><td>{num(v?.mean)} / {num(v?.quantiles?.p50)}</td>
    <td>{['p10','p25','p75','p90'].map(k=>num(v?.quantiles?.[k])).join(' / ')}</td>
    <td>{v?.count||0}<small>{v?.start||'-'} → {v?.end||'-'}</small></td>
   </tr>;})}</tbody></table></div>
  <p className="muted small">* 来源实际历史较短；分位不含当日，缺口不补零。此处仅展示历史估值位置。</p>
 </details>;
}

function CurrentValuationRanks({analysis,pending}:Obj){
 return <div className="studio-current-ranks" aria-label="当前估值历史分位">
  {[3,5,10].map(years=>{
   const rank=analysis?.ranks?.[years];
   return <div key={years}>
    <span>{years}年分位</span><strong>{pct(rank?.rank)}{rank?.partial&&<sup>*</sup>}</strong>
    <small>{!analysis?(pending?'正在读取…':'暂无结果'):rank?.rank==null?'有效历史不足':rank.partial?'历史不足整窗，按已有历史计算':`${rank.count} 个前序观测`}</small>
    <small>{rank?.start||'-'} → {rank?.end||'-'}</small>
   </div>;
  })}
 </div>;
}

export function StudioOverview({d,method,setMethod,window,setWindow}:Obj){
 const [metric,Metric]=useState('pe');
 const [valuationWindow,ValuationWindow]=useState(3);
 const [analysis,Analysis]=useState<Obj|null>(null);
 const [failure,Failure]=useState<Obj|null>(null);
 const methods=['mcw','ew','ewpvo','median','overall','arithmetic'].filter(m=>(d.valuation_methods||[]).includes(m)||method===m);
 const context=JSON.stringify([d.id,d.basis,method,metric,d.research_revision,d.analysis_id]);
 const requestKey=JSON.stringify([context,valuationWindow]);
 const currentAnalysis=analysis?.context===context?analysis:null;
 const error=failure?.requestKey===requestKey?failure.message:'';
 const pending=currentAnalysis?.requestKey!==requestKey&&!error;

 useEffect(()=>{
  let active=true;
  Failure(null);
  api('/research/indices/'+encodeURIComponent(d.id)+'/studio?'+query({basis:d.basis,method,window:valuationWindow,metric}))
   .then(v=>{
    if(!active)return;
    if(v.research_revision!==d.research_revision||v.analysis_id!==d.analysis_id){
     Failure({requestKey,message:'数据版本已变化，等待本地档案同步后重算。'});
     return;
    }
    Analysis({...v,context,requestKey});
   })
   .catch(e=>{if(active)Failure({requestKey,message:e.message});});
  return()=>{active=false;};
 },[requestKey]);

 const curve=(currentAnalysis?.valuation_curve||[]).map((r:Obj)=>Object.fromEntries(
  Object.entries(r).map(([k,v])=>[k,k==='date'?v:v==null?null:Number(v)*(metric==='dividend_yield'&&k!=='close'?100:1)])
 ));
 return <>
  <Panel className="studio-summary studio-overview-summary">
   <div><span>{d.basis==='price'?'价格':'全收益'}收盘 · 点</span><strong>{num(d.latest?.close)}</strong><small>{d.latest?.date||'尚无数据'}</small></div>
   <div className="studio-change-summary"><span>涨跌幅</span><div className="studio-change-grid">
    {[['daily','最近交易日'],['weekly','本周'],['monthly','本月'],['ytd','本年']].map(([k,label])=><div key={k}>
     <small>{label}</small><b className={trendClass(d.changes[k]?.ratio)}>{pct(d.changes[k]?.ratio)}</b>
    </div>)}
   </div></div>
   <div><span>{metricNames[metric]} · {methodNames[method]}</span><strong>{metric==='dividend_yield'?pct(d.valuation[metric]?.current):num(d.valuation[metric]?.current)}</strong><small>估值 {d.valuation[metric]?.date||'尚无此方法数据'}</small></div>
   <div><span>前十大股票权重</span><strong>{num(d.top10_weight)}%</strong><small>权重日期 {d.membership_date||'-'}</small></div>
  </Panel>
  <Panel title="估值走势" className="studio-valuation-panel" right={<HistoryWindow value={valuationWindow} onChange={ValuationWindow} label="估值历史窗口"/>}>
   <div className="studio-controls">
    <div className="studio-choice"><span>估值方法</span><ValuationMethods methods={methods} value={method} onChange={setMethod}/></div>
    <div className="studio-choice"><span>观察指标</span><div className="segmented" aria-label="观察指标">
     {Object.entries(metricNames).map(([k,v])=><button key={k} aria-pressed={metric===k} className={metric===k?'active':''} onClick={()=>Metric(k)}>{String(v)}</button>)}
    </div></div>
   </div>
   <CurrentValuationRanks analysis={currentAnalysis} pending={pending}/>
   <ErrorBox error={error}/>
   <p className="studio-valuation-status muted small" role="status">{pending?(currentAnalysis?`正在读取${windowName(valuationWindow)}，暂显示${windowName(currentAnalysis.window)}曲线…`:'正在读取估值分析…'):error&&currentAnalysis?`未完成切换，仍显示${windowName(currentAnalysis.window)}曲线。`:'\u00a0'}</p>
   <div className="studio-valuation-chart" aria-busy={pending} data-valuation-window={currentAnalysis?.window}>
   {curve.length?<ValuationTrend rows={curve} label="估值与三五十年滚动均值" lines={[
    ...['value','mean3','mean5','mean10'].map((key,i)=>({key,label:i?`${[3,5,10][i-1]}年日频平均`:metricNames[metric],color:colors[i]})),
    {key:'close',label:'同日指数点位 · 右轴',color:colors[4],axis:'price',dash:'4 3'},
   ]}/>:pending?null:<Notice>{error?'估值分析读取失败。':'尚无所选方法的估值序列；可在数据来源页查看采集状态。'}</Notice>}
   </div>
   <p className="muted small">分位不含当日观测；估值每日等权滚动平均包含当日，不足整窗留空。点位使用独立右轴。</p>
   <ValuationRanks analysis={currentAnalysis}/>
  </Panel>
  <Panel title="点位走势" className="studio-price-panel" right={<HistoryWindow value={window} onChange={setWindow} label="点位历史窗口"/>}>
   <p className="muted small">{windowName(window)} · 本地行情 {d.daily_series[0]?.date||'-'} → {d.latest?.date||'-'}；切换只读取本地数据。</p>
   <LinkedHistory d={d} window={window}/>
  </Panel>
  <Scenario key={d.analysis_id+method} d={d} method={method} setMethod={setMethod}/>
  <div className="studio-risk" data-studio-ready={Boolean(currentAnalysis)&&!pending&&!error}>
   <span>全量权重 HHI {num(currentAnalysis?.concentration_hhi,4)}</span>
  </div>
 </>;
}

export function IndexFinancials({d}:Obj){
 const [period,Period]=useState('12-31');
 const [metric,Metric]=useState('revenue');
 const fields:Obj={revenue:'ps.oi',profit:'ps.npatoshopc',adjusted_profit:'ps.npadnrpatoshaopc',operating_cf:'cfs.ncffoa',assets:'bs.ta',liabilities:'bs.tl',equity:'bs.toe'};
 const labels:Obj={revenue:'营业收入',profit:'归母净利润',adjusted_profit:'扣非归母净利润',operating_cf:'经营现金流',assets:'总资产',liabilities:'总负债',equity:'净资产'};
 const periods:Obj={'12-31':'年度','03-31':'一季度','06-30':'半年','09-30':'三季度'};
 const supplied=(d.index_financials||[]).length>0;
 const balance=fields[metric].startsWith('bs.');
 const rows=supplied?(d.index_financials||[]).filter((r:Obj)=>r.date.endsWith(period)).map((r:Obj)=>({
  date:r.date,
  amount:r.values['q.'+fields[metric]+'.t']==null?null:Number(r.values['q.'+fields[metric]+'.t'])/1e8,
  growth:r.values['q.'+fields[metric]+'.t_y2y'],
  published_at:r.published_at,
 })):(period==='12-31'?(d.financials.historical_years||[]).map((r:Obj)=>({
  date:r.period,amount:r.metrics[metric]?.amount==null?null:Number(r.metrics[metric].amount)/1e8,
  growth:r.metrics[metric]?.growth,published_at:null,
 })):[]);
 const latest=rows[rows.length-1];
 const growthRows=rows.map((r:Obj)=>({...r,percent:r.growth==null?null:Number(r.growth)*100}));

 return <Panel title="指数财务趋势">
  <p className="muted small">按每个报告期当时的成分公司汇总，观察指数整体经营规模。{supplied?'直接使用理杏仁指数财务；不是今天的成分回填过去。':'理杏仁指数财务尚无数据，年度营收、利润及非金融现金流使用已核验历史名单汇总。'}</p>
  <div className="segmented studio-finance-metrics" aria-label="指数财务指标">{Object.entries(labels).map(([k,label])=>
   <button key={k} aria-pressed={metric===k} className={metric===k?'active':''} onClick={()=>Metric(k)}>{String(label)}</button>
  )}</div>
  <div className="segmented" aria-label="财报同期比较">{Object.entries(periods).map(([k,label])=>
   <button key={k} aria-pressed={period===k} className={period===k?'active':''} onClick={()=>Period(k)}>{String(label)}历史</button>
  )}</div>
  <p className="muted small">{periods[period]}跨年比较 · {balance?'报告期末存量':period==='12-31'?'全年累计':period==='03-31'?'每年1—3月累计':period==='06-30'?'每年1—6月累计':'每年1—9月累计'} · CNY亿元</p>
  {latest?<>
   <div className="studio-summary studio-finance-summary">
    <div><span>{labels[metric]} · {periods[period]} · 亿元</span><strong>{num(latest.amount)}</strong><small>报告期 {latest.date}</small></div>
    <div><span>{supplied?'同篮子同比':'历史汇总同比'}</span><strong className={trendClass(latest.growth)}>{pct(latest.growth)}</strong><small>披露 {latest.published_at||'源端未提供'}</small></div>
    <div><span>本地同期财报覆盖</span><strong>{rows.length} 期</strong><small>{rows[0].date} → {latest.date}</small></div>
   </div>
   <div role="img" aria-label="指数财务金额趋势"><ResponsiveContainer width="100%" height={300} minWidth={0}>
    <LineChart accessibilityLayer data={rows}><CartesianGrid stroke="var(--line)" vertical={false}/><XAxis dataKey="date" minTickGap={65}/><YAxis width={75} domain={['auto','auto']}/><Tooltip formatter={(v:any)=>num(v)+' 亿元'}/>
     <Line dataKey="amount" name={labels[metric]} stroke={colors[0]} dot={{r:3}} connectNulls={false} isAnimationActive={false}/>
    </LineChart>
   </ResponsiveContainer></div>
   <div role="img" aria-label="指数财务同比趋势"><ResponsiveContainer width="100%" height={190} minWidth={0}>
    <BarChart data={growthRows}><CartesianGrid stroke="var(--line)" vertical={false}/><XAxis dataKey="date" minTickGap={65}/><YAxis width={75} tickFormatter={v=>v+'%'}/><Tooltip formatter={(v:any)=>num(v)+'%'}/>
     <Bar dataKey="percent" name={supplied?'同篮子同比':'历史汇总同比'} isAnimationActive={false} maxBarSize={32}>
      {growthRows.map((r:Obj)=><Cell key={r.date} fill={Number(r.percent)>=0?'#bf4b50':'#258879'}/>)}
     </Bar>
    </BarChart>
   </ResponsiveContainer></div>
   <details><summary>查看各期金额与同比</summary><div className="table-wrap"><table><thead><tr><th>报告期</th><th>{labels[metric]} · 亿元</th><th>{supplied?'同篮子同比':'历史汇总同比'}</th><th>披露日期</th></tr></thead>
    <tbody>{[...rows].reverse().map((r:Obj)=><tr key={r.date}><td>{r.date}</td><td>{num(r.amount)}</td><td className={trendClass(r.growth)}>{pct(r.growth)}</td><td>{r.published_at||'源端未提供'}</td></tr>)}</tbody>
   </table></div></details>
  </>:<Empty title="尚无所选报告期数据">{d.module_status?.index_financials?.reason||'已采集范围内无此类报告期；缺失不补零。'}</Empty>}
  <p className="muted small">{supplied?'来源：理杏仁。金额为当期成分简单加总；同比由源端用当期成分的上年回溯财报计算，与相邻两点直接相除不同。':'来源：东方财富公司财报与历史成分名单；覆盖不足保留缺口。'}历史数据为当前可得版本。 <a href="https://www.lixinger.com/wiki/stock-collection-fs-calculation" target="_blank" rel="noopener noreferrer">计算说明 ↗</a></p>
 </Panel>;
}

function trimEmptyEdges(rows:Obj[],keys:string[]){
 const known=(r:Obj)=>keys.some(key=>r[key]!=null);
 const first=rows.findIndex(known);
 if(first<0)return [];
 let last=rows.length-1;
 while(last>first&&!known(rows[last]))last--;
 return rows.slice(first,last+1);
}

function FundSeriesChart({rows,field,name,changes}:Obj){
 const series=trimEmptyEdges(rows,[field]);
 return <div role="img" aria-label={name} data-start={series[0]?.date} data-end={series[series.length-1]?.date}>
  <h3>{name}</h3>
  {series.length?<>
   <p className="muted small">显示 {series[0].date} → {series[series.length-1].date}</p>
   <ResponsiveContainer width="100%" height={290} minWidth={0}>
    <BarChart accessibilityLayer data={series}>
     <CartesianGrid vertical={false} stroke="var(--line)"/><XAxis dataKey="date" minTickGap={80}/><YAxis width={65}/><Tooltip/>
     <Bar dataKey={field} fill={colors[0]} isAnimationActive={false}>
      {series.map((r:Obj)=><Cell key={r.date} fill={changes?(Number(r[field])>=0?'#bf4b50':'#258879'):colors[0]}/>)}
     </Bar>
    </BarChart>
   </ResponsiveContainer>
  </>:<p className="muted">所选范围暂无该指标的有效记录。</p>}
 </div>;
}

export function CapitalFlows({d}:Obj){
 const [view,V]=useState('etf'),[cap,Cap]=useState('mc'),[span,Span]=useState(0),[northHistory,NorthHistory]=useState(false);
 const [a,A]=useState<Obj|null>(null),[error,E]=useState('');
 useEffect(()=>{
  let active=true;
  A(null);
  E('');
  api('/research/indices/'+encodeURIComponent(d.id)+'/studio?'+query({basis:d.basis,method:d.method,window:d.window}))
   .then(v=>{if(active&&v.analysis_id===d.analysis_id)A(v);})
   .catch(e=>{if(active)E(e.message);});
  return()=>{active=false;};
 },[d.analysis_id,d.basis,d.window]);

 const flowKey=view==='capitalization'?'change':'flow';
 const rawValues=(view==='capitalization'?a?.funds.capitalization[cap]:a?.funds[view==='northbound'&&northHistory?'northbound_daily':view])||[];
 const allValues=trimEmptyEdges(rawValues,['stock',flowKey]);
 const cut=span&&allValues.length?String(Number(allValues[allValues.length-1].date.slice(0,4))-span)+allValues[allValues.length-1].date.slice(4):'';
 const rows=allValues.filter((r:Obj)=>!cut||r.date>=cut).map((r:Obj)=>({
  ...r,
  date:r.date,
  stock:r.stock==null?null:Number(r.stock)/1e8,
  flow:r.flow==null?null:Number(r.flow)/1e8,
  change:r.change==null?null:Number(r.change)/1e8,
 }));
 const firstNonzero=rows.findIndex((r:Obj)=>['stock',flowKey].some(key=>r[key]!=null&&r[key]!==0));
 const chartRows=firstNonzero>0?rows.slice(firstNonzero):rows;
 const latestStock=[...rows].reverse().find((r:Obj)=>r.stock!=null);
 const latestFlow=[...rows].reverse().find((r:Obj)=>r[flowKey]!=null);
 const names:Obj={etf:['A股场内基金资产规模','场内基金认购净流入'],margin:['融资余额','融资净买入'],northbound:['北向持股市值','披露净买入'],capitalization:['成分市值','存量变化（非流量）']};

 return <Panel title="资金与持仓观察" right={<div className="segmented" aria-label="资金历史窗口">{[3,5,10,0].map(y=>
  <button key={y} className={span===y?'active':''} onClick={()=>Span(y)}>{windowName(y)}</button>
 )}</div>}>
  <ErrorBox error={error}/>
  <div className="segmented">{[['etf','场内基金'],['margin','融资'],['northbound','北向持仓'],['capitalization','成分总市值']].map(([k,l])=>
   <button key={k} className={view===k?'active':''} onClick={()=>V(k)}>{l}</button>
  )}</div>
  {view==='northbound'&&<label className="check"><input type="checkbox" checked={northHistory} onChange={e=>NorthHistory(e.target.checked)}/>查看2024-08-16前披露的日频记录（月汇总）</label>}
  {view==='capitalization'&&<select value={cap} aria-label="市值口径" onChange={e=>Cap(e.target.value)}><option value="mc">总市值</option><option value="cmc">流通市值</option><option value="ecmc">自由流通市值</option></select>}
  <p className="muted small">{view==='etf'&&'理杏仁A股场内基金合计；仅统计源端场内基金口径，不汇总场外基金，不将联接基金重复相加。'}单位 CNY亿元；{view==='northbound'?'2024-08-16后按实际季度披露，不平摊成日频。':'按月观察，缺日或未完成月份不输出完整月净流量。'}存量差额包含价格与成分变动，不能当作净流入，各类资金不能相加。</p>
  <p className="muted small">源端记录 {allValues[0]?.date||'-'} → {allValues[allValues.length-1]?.date||'-'}，共 {allValues.length} 个观察期；图表显示范围见各图。</p>
  {firstNonzero>0&&<p className="muted small fund-zero-prefix">图表已收起起始 {firstNonzero} 期零值或空值，从 {chartRows[0].date} 的首个非零统计开始；数据源返回零值不代表已核实当时没有资金。原始记录保留在下方明细。</p>}
  {rows.length>0&&<div className="studio-summary studio-funds-summary">
   <div><span>{names[view][0]} · 亿元</span><strong>{num(latestStock?.stock)}</strong><small>{latestStock?.date||'尚无有效记录'}</small></div>
   <div><span>{names[view][1]} · 亿元</span><strong className={trendClass(latestFlow?.[flowKey])}>{num(latestFlow?.[flowKey])}</strong><small>{latestFlow?`${latestFlow.date} · ${view==='capitalization'?'最近存量变化':'最近完整披露期'}`:'尚无完整披露期；不以存量差补净流量'}</small></div>
  </div>}
  {rows.length?<div className="two-columns">
   <FundSeriesChart rows={chartRows} field="stock" name={names[view][0]} changes={false}/>
   <FundSeriesChart rows={chartRows} field={flowKey} name={names[view][1]} changes/>
  </div>:<Empty title="尚无该类有效数据">请检查订阅模块、权限与实际覆盖，缺失不补零。</Empty>}
  <details className="fund-observations"><summary>实际日期与完整度</summary><div className="table-wrap"><table>
   <thead><tr><th>实际日期</th><th>存量 · 亿元</th><th>净流量 · 亿元</th><th>存量变化 · 亿元</th><th>流量完整度</th></tr></thead>
   <tbody>{[...rows].reverse().map((r:Obj)=><tr key={r.date}>
    <td>{r.date}</td><td>{num(r.stock)}</td><td className={trendClass(r.flow)}>{num(r.flow)}</td><td className={trendClass(r.change)}>{num(r.change)}</td><td>{r.complete?'完整':'缺失或未完成'}</td>
   </tr>)}</tbody>
  </table></div></details>
 </Panel>;
}


export function SourceReview({d}:Obj){
 const [error,E]=useState(''),[busy,B]=useState(false),[reference,R]=useState(d.official_reference),[versions,V]=useState<Obj[]>([]),[frozen,F]=useState<Obj|null>(null);
 const root='/research/indices/'+encodeURIComponent(d.id),selection=useRef(0);
 useEffect(()=>{let live=true;api(root+'/versions').then(v=>{if(live)V(v.items);}).catch(e=>{if(live)E(e.message);});return()=>{live=false;};},[d.analysis_id]);
 async function check(){B(true);E('');try{const current=await api(root+'?method=mcw');R(await api(root+'/source-check',{analysis_id:current.analysis_id}));}catch(e:any){E(e.message);}finally{B(false);}}
 async function load(id:string){const turn=++selection.current;E('');F(null);if(!id)return;try{const v=await api(root+'/versions/'+id);if(turn===selection.current)F(v);}catch(e:any){if(turn===selection.current)E(e.message);}}
 const compact=(v:any):any=>Array.isArray(v)?v.slice(-100).map(compact):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,compact(x)])):v;
 return <><Panel title="官方同日交叉核验" right={<Button disabled={busy||!d.latest} onClick={check}>{busy?'正在核验…':'联网核验官方收盘'}</Button>}><ErrorBox error={error}/><p className="muted small">仅点击时请求官方最近45天日线，不消耗理杏仁额度，不拼接估值历史。收盘相符不代表PE/PB计算口径相同。</p>{reference?<><p>{reference.methodology_note}</p><div className="table-wrap"><table><thead><tr><th>日期</th><th>本地 / 官方收盘</th><th>差异 · 点</th><th>理杏仁 mcw / 官方 PE</th></tr></thead><tbody>{[...reference.rows].reverse().slice(0,10).map((r:Obj)=><tr key={r.date}><td>{r.date}</td><td>{num(r.local_close)} / {num(r.official_close)}</td><td>{num(r.difference,4)} · {r.close_matches?'相符':'待核对'}</td><td>{num(r.lixinger_pe)} / {num(r.official_pe)}</td></tr>)}</tbody></table></div></>:<p className="muted">尚未执行官方交叉核验；估值分母映射仍未核验。</p>}</Panel><Panel title="历史来源版本"><select aria-label="历史来源版本" defaultValue="" onChange={e=>load(e.target.value)}><option value="">选择最近100个不可变快照</option>{versions.map((v:Obj)=><option key={v.id} value={v.id}>{moduleLabels[v.module]||v.module} · {v.created_at} · {v.id.slice(0,8)}</option>)}</select>{frozen&&<><p>{frozen.data.source||'历史来源'} · 数据日期 {frozen.data.effective_date||'待核验'} · 快照 {frozen.snapshot_id}</p><details><summary>查看冻结数据（每组最多显示最后100条）</summary><pre className="frozen-source-data">{JSON.stringify(compact(frozen.data),null,2)}</pre></details></>}</Panel></>;
}
