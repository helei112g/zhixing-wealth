import React, {useEffect, useState} from 'react';
import {Download} from 'lucide-react';
import {CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {api, money, Obj, percent, query} from './api';
import {Button, Empty, ErrorBox, Field, Notice, Panel} from './components';
import {HistoryWindow, ValuationMethods} from './research_studio';
import {ValuationTrend, ValuationLine} from './research_charts';
import './waterlevel.css';

const methods: Obj = {mcw:'总市值加权', ew:'等权', ewpvo:'正数等权', median:'中位数'};
const states: Obj = {available:'已取得', missing:'缺失', stale:'数据陈旧', invalid_current:'当前值无效', source_error:'更新失败，保留旧值', error:'更新失败'};
const metricNames: Obj = {pe:'PE TTM', pb:'PB'};
const number = (value: any) => value == null ? '—' : money(value, 2);
const ratio = (value: any) => value == null ? '—' : percent(value);

function visibleRows(rows: Obj[], window: number, asof: string) {
  const start = window===0 ? '' : String(Number(asof.slice(0,4))-Number(window))+asof.slice(4,7);
  return rows.filter(row=>row.date.slice(0,7)>=start);
}

function History({statistics, label, unit, window=10, asof, zero=false, zoom=false}: Obj) {
  const [start, setStart] = useState<string>();
  const [end, setEnd] = useState<string>();
  const [range, setRange] = useState<string[]>();
  const cancel = () => {setStart(undefined);setEnd(undefined);};
  const visible = visibleRows(statistics.chart, window, asof);
  const selected = range ? visible.filter(row=>row.date>=range[0]&&row.date<=range[1]) : visible;
  const rows = selected.map((row: Obj) => ({date:row.date,value:row.value==null?null:Number(row.value)}));
  if (!rows.some((row: Obj) => row.value != null)) return <div className="waterlevel-chart-empty">尚无此口径曲线</div>;
  return <div className={zoom?'waterlevel-chart waterlevel-zoom-chart':'waterlevel-chart'}>
    {zoom&&<div className="waterlevel-zoom-controls"><small className="muted">{range?range.join(' 至 '):'在图中拖拽框选时段'}</small>{range&&<Button small onClick={()=>setRange(undefined)}>恢复全历史</Button>}</div>}
    <div role="img" aria-label={label+'历史曲线'}><ResponsiveContainer width="100%" height={220} minWidth={0}>
      <LineChart data={rows} accessibilityLayer margin={{top:12,right:12,bottom:0,left:0}}
        onMouseDown={zoom ? ({activeLabel})=>{if(typeof activeLabel==='string')setStart(activeLabel);} : undefined}
        onMouseMove={zoom ? ({activeLabel})=>{if(start&&typeof activeLabel==='string')setEnd(activeLabel);} : undefined}
        onMouseUp={zoom ? ({activeLabel})=>{
          const last = typeof activeLabel==='string' ? activeLabel : end;
          if(start&&last&&start!==last)setRange([start,last].sort());
          cancel();
        } : undefined}
        onMouseLeave={zoom ? cancel : undefined}>
        <CartesianGrid stroke="var(--line)" vertical={false}/>
        <XAxis dataKey="date" interval="preserveStartEnd" minTickGap={65} tick={{fontSize:10}} tickFormatter={value=>value.slice(0,7)}/>
        <YAxis domain={['auto','auto']} width={58} tick={{fontSize:10}}/>
        <Tooltip formatter={(value:any)=>number(value)+' '+unit} contentStyle={{fontSize:12,borderRadius:8}}/>
        {start&&end&&<ReferenceArea x1={start} x2={end} fill="#365f91" fillOpacity={.15}/>}
        {zero&&<ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="3 3"/>}
        <Line dataKey="value" name="观测值" stroke="#365f91" dot={rows.length===1} connectNulls={false} isAnimationActive={false}/>
      </LineChart>
    </ResponsiveContainer></div>
  </div>;
}

function StatisticCard({label, statistics, unit, window=10, asof, zero=false, zoom=false}: Obj) {
  return <Panel title={label} className="waterlevel-statistic">
    <div className="waterlevel-current"><strong>{number(statistics.current)}</strong><span>{unit}</span>
      <small>{statistics.date||'尚无观测'} · {states[statistics.status]||statistics.status}</small>
    </div>
    <History {...{statistics,label,unit,window,asof,zero,zoom}}/>
    <div className="waterlevel-window-values">
      {[5,10].map(years=><div key={years}><b>{years}年分位 {ratio(statistics.windows[years].percentile_ratio)}</b>
        <small>分位样本 {statistics.windows[years].count} · {statistics.windows[years].status==='available'?'跨度完整':'历史不足'}</small>
      </div>)}
    </div>
  </Panel>;
}

const marginLines:ValuationLine[] = [
  {key:'margin_balance',label:'沪深合计',color:'#365f91'},
  {key:'sh_margin_balance',label:'沪市两融余额',color:'#b88453'},
  {key:'sz_margin_balance',label:'深市两融余额',color:'#268979'},
];
const turnoverLines:ValuationLine[] = [{key:'a_share_turnover',label:'成交额',color:'#365f91'}];

function MarketAmountCard({data, lines, title, children}: {
  data:Obj; lines:ValuationLine[]; title:string; children:React.ReactNode;
}) {
  const [window, setWindow] = useState(0);
  const series = lines.map(line=>data.series.find((item:Obj)=>item.id===line.key).statistics);
  const current = series[0];
  const monthly = new Map<string,Obj>();
  for (const [index, line] of lines.entries()) {
    for (const row of visibleRows(series[index].chart, window, data.asof)) {
      const date = row.date.slice(0,7);
      if(!monthly.has(date))monthly.set(date,{date});
      monthly.get(date)![line.key] = row.value==null?null:Number(row.value)/1e8;
    }
  }
  return <Panel title={title} className="studio-valuation-panel"
    right={<HistoryWindow label={title+'曲线窗口'} value={window} onChange={setWindow} windows={[5,10,0]}/>}>
    <div className="waterlevel-current">
      <strong>{current.current==null?'—':number(Number(current.current)/1e8)}</strong><span>亿元</span>
      <small>{current.date||'尚无观测'} · {states[current.status]}</small>
    </div>
    {current.current==null?<Notice>尚无有效观测，可在数据管理的市场指标中更新。</Notice>
      :<ValuationTrend rows={[...monthly.values()]} label={title+'历史曲线'} lines={lines} preserveBounds unit="亿元"/>}
    <p className="muted small">{children} 曲线按月末最后观测展示，当月取最新已发布交易日。</p>
  </Panel>;
}

function ComparisonCard({data, metric, setMetric, setMethod, window, setWindow, loading, selectedMethod, methodStatus}: Obj) {
  const comparison = data.comparisons[metric];
  const statistics = data.series.find((item:Obj)=>item.id==='full_market_'+metric).statistics;
  const definitions:ValuationLine[] = [
    {key:'value', label:metricNames[metric]+' · '+methods[data.method], color:'#365f91'},
    {key:'mean5', label:'5年完整月均值', color:'#bc8650'},
    {key:'mean10', label:'10年完整月均值', color:'#298879'},
    {key:'historical_mean', label:'历史平均', color:'#a75157'},
  ];
  if(metric==='pe') definitions.push({key:'bond_inverse', label:'10Y国债收益率倒数', color:'#a89963', dash:'3 3'});
  definitions.push(
    {key:'sh', label:'上证综指 · 右轴', color:'#8866a6', axis:'price', dash:'4 3'},
    {key:'sz', label:'深证综指 · 右轴', color:'#6e7f8b', axis:'price', dash:'4 3'},
  );
  const rows = visibleRows(comparison.chart, window, data.asof).map((row:Obj)=>({
    date:row.date,
    ...Object.fromEntries(definitions.map(item=>[item.key,row[item.key]==null?null:Number(row[item.key])])),
  }));
  return <Panel title="全市场估值走势" className="waterlevel-comparison studio-valuation-panel" right={<HistoryWindow label="全市场曲线窗口" value={window} onChange={setWindow} windows={[5,10,0]}/>}>
    <div className="studio-controls">
      <div className="studio-choice"><span>估值方法</span><ValuationMethods methods={Object.keys(methods)} value={selectedMethod} onChange={setMethod}/></div>
      <div className="studio-choice"><span>观察指标</span><div className="segmented" aria-label="全市场观察指标">
        {['pe','pb'].map(key=><button type="button" key={key} aria-pressed={metric===key} className={metric===key?'active':''} onClick={()=>setMetric(key)}>{metricNames[key]}</button>)}
      </div></div>
    </div>
    {methodStatus&&<p role="status" className="muted small waterlevel-method-status">{methodStatus}</p>}
    <div className="waterlevel-current"><strong>{number(statistics.current)}</strong><span>倍</span>
      <small>{statistics.date||'尚无观测'} · {states[statistics.status]}</small></div>
    <div className="studio-valuation-chart waterlevel-comparison-chart" aria-busy={loading}>
      {rows.some((row:Obj)=>row.value!=null)
        ? <ValuationTrend rows={rows} label="全市场估值与历史均值" lines={definitions} preserveBounds/>
        : <Notice>尚无所选口径的估值序列，可在研究工具的数据管理手动更新理杏仁 A股全指。</Notice>}
    </div>
    <p className="muted small">估值来源为理杏仁 A股全指（1000002）。左轴为估值倍数。两综指使用独立右轴，以 {comparison.base_month||'首个共同有效月份'} 为基准100。
      历史平均从该口径首个有效月起累计，只纳入已完成月份的有效月末观测，切换窗口沿用原起点。{metric==='pe'?'国债收益率倒数仅作参考。':''}</p>
  </Panel>;
}

const broadIndices = [
  ['csi:000300','#153557'], ['csi:000688','#509ca5'], ['cni:399006','#bd7632'],
  ['csi:000905','#33865d'], ['csi:000852','#933f3f'], ['csi:930050','#855eaa'],
  ['csi:000510','#08778d'],
];

function BroadCard({data, window, setWindow, setMethod, loading, selectedMethod, methodStatus}: Obj) {
  const chart = (id:string) => data.series.find((item:Obj)=>item.id===id).statistics.chart;
  const indices = broadIndices.map(([key,color])=>{
    const index = data.indices.find((item:Obj)=>item.id===key);
    return {key,label:index.label,color,points:index.metrics.pe.chart};
  });
  const series = [
    {key:'market',label:'全市场',color:'#5076a4',points:chart('full_market_pe')},
    ...indices,
    {key:'bond',label:'10Y国债收益率倒数',color:'#a89963',dash:'4 3',points:chart('bond_inverse')},
  ];
  const monthly = new Map<string,Obj>();
  for (const {key,points} of series) {
    for (const {date,value} of points) {
      const month = date.slice(0,7);
      if (!monthly.has(month)) monthly.set(month,{date:month});
      monthly.get(month)![key] = value==null ? null : Number(value);
    }
  }
  const rows = visibleRows([...monthly.values()].sort((a,b)=>a.date.localeCompare(b.date)), window, data.asof);
  const lines = series.filter(({key})=>rows.some(row=>row[key]!=null));
  return <Panel title="境内宽基指数 PE 对照" className="studio-valuation-panel" right={<HistoryWindow label="股票估值曲线窗口" value={window} onChange={setWindow} windows={[5,10,0]}/>}>
    <div className="studio-controls"><div className="studio-choice"><span>估值方法</span>
      <ValuationMethods methods={Object.keys(methods)} value={selectedMethod} onChange={setMethod}/>
    </div></div>
    {methodStatus&&<p role="status" className="muted small waterlevel-method-status">{methodStatus}</p>}
    <div className="studio-valuation-chart" aria-busy={loading}>{lines.some(line=>line.key!=='bond')
      ? <ValuationTrend rows={rows} label="境内宽基指数 PE 对照" lines={lines} preserveBounds/>
      : <Notice>尚无所选口径的估值历史，可在研究工具的数据管理更新相关指数的估值模块。</Notice>}
    </div>
    <p className="muted small">单位为倍。各估值线独立按月末取样，缺失历史留空。国债收益率倒数仅作参照，可用图例隐藏。</p>
    {series.some(line=>!lines.includes(line))&&<p className="muted small">所选窗口无可绘制观测 · {series.filter(line=>!lines.includes(line)).map(line=>line.label).join('、')}</p>}
  </Panel>;
}

function ScoreOverview({data}: Obj) {
  const categories = [['overall','综合'],['equity','股票'],['rates','利率债'],['credit','信用债']];
  return <>
    <div className="waterlevel-scores">
      {categories.map(([key,label])=>{const value=key==='overall'?data.scores.overall:data.scores.categories[key];return <section className="waterlevel-score" key={key} aria-label={label+'水位'}>
        <span>{label}水位</span><strong>{number(value.score)}</strong><b>{value.label}</b>
        <div className="waterlevel-meter" role="img" aria-label={label+'水位刻度'}>{value.score!=null&&<i style={{left:Math.min(100,Math.max(0,Number(value.score)))+'%'}}/>}</div>
        <small>有效{key==='overall'?'大类':'指标'} {value.included}/{value.expected}</small>
      </section>;})}
    </div>
    <p className="muted small">股票评分固定使用指数 mcw 与全市场中位数，切换图表估值方法不改变评分。历史相对位置不代表未来收益；缺类时综合覆盖范围改变。</p>
    <Panel title="规则摘要" right={<span className="pill">程序计算</span>}>
      <p>{data.commentary.overall}</p><ul>{data.commentary.evidence.map((text:string)=><li key={text}>{text}</li>)}</ul>
      <details><summary>展开评分依据</summary><div className="table-wrap"><table><thead><tr><th>指标</th><th>口径</th><th>数据日</th><th>窗口 / 样本</th><th>水位</th></tr></thead><tbody>
        {Object.values(data.scores.categories).flatMap((category:any)=>category.members).map((item:Obj)=><tr key={item.id}>
          <td>{item.label}</td><td>{item.inverse?'分位反向':methods[item.method]||'—'}</td><td>{item.date||'—'}</td>
          <td>{item.window?item.window+'年 / '+item.count:'不足5年'}</td><td>{number(item.score)}<small>{states[item.status]||item.status}</small></td>
        </tr>)}
      </tbody></table></div></details>
    </Panel>
  </>;
}

export function WaterlevelPage({today,onData}: Obj) {
  const [method, setMethod] = useState('ewpvo');
  const [fullMetric, setFullMetric] = useState('pe');
  const asof = today;
  const [tab, setTab] = useState('overview');
  const [overviewWindow, setOverviewWindow] = useState(10);
  const [equityWindow, setEquityWindow] = useState(0);
  const [data, setData] = useState<Obj|null>(null);
  const [revision, setRevision] = useState(0);
  const [errorsVersion, setErrorsVersion] = useState('');
  const [refreshRequest, setRefreshRequest] = useState(0);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reports, setReports] = useState<Obj[]>([]);
  const [selectedReport, setSelectedReport] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(()=>{
    let active=true;
    setLoading(true);
    const request = api('/market-waterlevel?'+query({method,asof}));
    request.then(value=>{if(active){setData(value);setError('');}}).catch(error=>{if(active)setError(error.message);})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[method,asof,revision,errorsVersion,refreshRequest]);

  useEffect(()=>{
    let active=true, busy=false;
    async function refresh() {
      if(document.hidden||busy)return;
      busy=true;
      try {const result=await api('/market-waterlevel/revision');if(active){setRunning(result.updating);setRevision(result.research_revision);setErrorsVersion(result.errors_version);}}
      catch(error:any){if(active)setError(error.message);}
      finally{busy=false;}
    }
    refresh();
    const timer=setInterval(refresh,3000);
    api('/market-waterlevel/snapshots').then(value=>{if(active)setReports(value.items);}).catch(error=>{if(active)setError(error.message);});
    return()=>{active=false;clearInterval(timer);};
  },[]);

  async function freeze() {
    if(!data)return;
    setSaving(true);
    try {
      const result=await api('/market-waterlevel/snapshots',{analysis_id:data.analysis_id,method:data.method,asof:data.asof,expected_research_revision:data.research_revision});
      setReports((await api('/market-waterlevel/snapshots')).items);
      setSelectedReport(result.snapshot_id);
      setError('');
    } catch(error:any){setError(error.message);}
    finally{setSaving(false);}
  }
  function selectMethod(next: string) {
    if (next === method && !error) return;
    if (next === method) {
      setRefreshRequest(value=>value+1);
    }
    setError('');
    setLoading(true);
    setMethod(next);
  }
  const selectedMethod = loading ? method : data?.method;
  const methodStatus = data && method!==data.method
    ? `${loading ? '正在读取' : '读取失败，未切换到'}${methods[method]}，当前显示${methods[data.method]}。`
    : '';
  const blocked=loading||saving||running;
  const lines=(keys:string[],window=0,zoom=false)=>data?.series.filter((item:Obj)=>keys.includes(item.id)).map((item:Obj)=><StatisticCard key={item.id} label={item.label} statistics={item.statistics} unit={item.unit} window={window} zoom={zoom} asof={asof} zero={['money_gap','repo_spread','cn_term','us_term','erp'].includes(item.id)}/>);
  return <div className="waterlevel-page">
    <ErrorBox error={error}/>
    <div className="inline-actions"><Button onClick={onData}>数据管理</Button></div>
    {running&&<p className="muted small" role="status">更新正在执行，已有有效数据保留；结果与失败项可在研究工具的数据管理中核对。</p>}
    {loading&&!data&&<p role="status" className="muted small">正在读取本地市场数据…</p>}
    {!data?<Empty title="市场水位尚未就绪">{error?'读取失败，请核对错误信息。':'正在读取本地资料。'}</Empty>:<>
      <div className="instrument-tabs" role="tablist" aria-label="市场水位栏目">{[['overview','水位总览'],['equity','股票估值'],['bonds','债券与资金'],['macro','宏观背景'],['reports','报告存档']].map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}</button>)}</div>
      {tab!=='reports'&&<p className="muted small waterlevel-view-options">截至 {data.asof} · 各指标实际日期独立显示 · 月末采样，当月显示最新观测</p>}
      {tab==='overview'&&<><ScoreOverview data={data}/><ComparisonCard data={data} metric={fullMetric} setMetric={setFullMetric} setMethod={selectMethod} window={overviewWindow} setWindow={setOverviewWindow} loading={loading} selectedMethod={selectedMethod} methodStatus={methodStatus}/><div className="waterlevel-grid">{lines(['erp','bond_inverse'],overviewWindow)}</div><details className="waterlevel-boundaries"><summary>当前覆盖与计算边界</summary><ul>{data.limitations.map((text:string)=><li key={text}>{text}</li>)}</ul></details></>}
      {tab==='equity'&&<>
        <BroadCard data={data} window={equityWindow} setWindow={setEquityWindow} setMethod={selectMethod} loading={loading} selectedMethod={selectedMethod} methodStatus={methodStatus}/>
        <MarketAmountCard data={data} title="沪深两融余额" lines={marginLines}>来源为东方财富，合计同日沪市与深市的融资融券余额，范围为沪深两市。</MarketAmountCard>
        <MarketAmountCard data={data} title="A股全指当日成交额" lines={turnoverLines}>来源为理杏仁 A股全指（1000002），按指数样本统计已收盘交易日成交额。</MarketAmountCard>
      </>}
      {tab==='bonds'&&<><p className="muted small">信用利差按同日AAA 3Y中票减3Y国债；FR007/FDR007是定盘利率，不是R007/DR007。1个百分点=100 BP。</p><div className="waterlevel-grid">{lines(['cn_10y','cn_2y','cn_30y','us_10y','us_2y','us_30y','cn_term','us_term','credit_spread','aaa_3y','gov_3y','fr007','fdr007','repo_spread'],0,true)}</div></>}
      {tab==='macro'&&<><Notice>日期为统计月份，不能当作发布日期。社融使用人民银行直接公布的存量同比；M2−M1为同月同比之差，跨M1统计口径的历史仅供参考。</Notice><div className="waterlevel-grid">{lines(['pmi','nonmanufacturing_pmi','cpi_yoy','ppi_yoy','m1_yoy','m2_yoy','money_gap','sf_stock_yoy'],0,true)}</div><p className="muted small">本批来源未统一提供原始发布日期；未知日期保留为空，抓取时间见来源记录。</p></>}
      {tab==='reports'&&<>
        <details className="waterlevel-archive"><summary>研究报告存档与下载</summary>
          <p className="muted small">保存当前口径与来源版本以便复核。页面始终展示当前数据，历史报告可下载离线查看。</p>
          <div className="waterlevel-settings"><Button disabled={blocked} onClick={freeze}>冻结研究快照</Button>
            <Field label="研究快照"><select aria-label="研究快照" value={selectedReport} onChange={event=>setSelectedReport(event.target.value)}><option value="">选择已存档报告</option>{reports.map(report=><option key={report.id} value={report.id}>{report.created_at.slice(0,19).replace('T',' ')} · {report.id.slice(0,8)}</option>)}</select></Field>
            {selectedReport&&<a className="button" href={'/api/market-waterlevel/snapshots/'+selectedReport+'/export'} download><Download size={14}/>下载离线报告</a>}
          </div>
        </details></>}
    </>}
  </div>;
}
