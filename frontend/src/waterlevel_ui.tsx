import React, {useEffect, useState} from 'react';
import {Download, RefreshCw} from 'lucide-react';
import {CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {api, money, Obj, percent, query} from './api';
import {Button, Empty, ErrorBox, Field, Notice, Panel} from './components';
import {UpdateHistory, useSubmission} from './sync_ui';
import {ValuationMethods} from './research_studio';
import {ValuationTrend, ValuationLine} from './research_charts';
import './waterlevel.css';

const methods: Obj = {mcw:'总市值加权', ew:'等权', median:'中位数'};
const states: Obj = {available:'已取得', missing:'缺失', stale:'数据陈旧', invalid_current:'当前值无效', source_error:'更新失败，保留旧值', error:'更新失败'};
const metricNames: Obj = {pe:'PE TTM', pb:'PB', dividend_yield:'股息率'};
const number = (value: any) => value == null ? '—' : money(value, 2);
const ratio = (value: any) => value == null ? '—' : percent(value);

function visibleRows(rows: Obj[], window: string, asof: string) {
  const start = window==='all' ? '' : String(Number(asof.slice(0,4))-Number(window))+asof.slice(4,7);
  return rows.filter(row=>row.date.slice(0,7)>=start);
}

function History({statistics, label, unit, multiplier=1, window='10', asof, means=false, zero=false}: Obj) {
  const rows = visibleRows(statistics.chart, window, asof)
    .map((row: Obj) => ({...row, ...Object.fromEntries(['value','mean5','mean10'].map(key => [key, row[key] == null ? null : Number(row[key]) * multiplier]))}));
  if (!rows.some((row: Obj) => row.value != null)) return <div className="waterlevel-chart-empty">尚无此口径曲线</div>;
  return <div role="img" aria-label={label+'历史曲线'} className="waterlevel-chart">
    <ResponsiveContainer width="100%" height={220} minWidth={0}>
      <LineChart data={rows} accessibilityLayer margin={{top:12,right:12,bottom:0,left:0}}>
        <CartesianGrid stroke="var(--line)" vertical={false}/>
        <XAxis dataKey="date" interval="preserveStartEnd" minTickGap={65} tick={{fontSize:10}} tickFormatter={value=>value.slice(0,7)}/>
        <YAxis domain={['auto','auto']} width={58} tick={{fontSize:10}}/>
        <Tooltip formatter={(value:any)=>number(value)+' '+unit} contentStyle={{fontSize:12,borderRadius:8}}/>
        {zero&&<ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="3 3"/>}
        <Line dataKey="value" name="观测值" stroke="#365f91" dot={rows.length===1} connectNulls={false} isAnimationActive={false}/>
        {means&&<Line dataKey="mean5" name="5年完整月均值" stroke="#b88453" dot={false} connectNulls={false} isAnimationActive={false}/>}
        {means&&<Line dataKey="mean10" name="10年完整月均值" stroke="#268979" dot={false} connectNulls={false} isAnimationActive={false}/>}
      </LineChart>
    </ResponsiveContainer>
  </div>;
}

function StatisticCard({label, statistics, unit, multiplier=1, window='10', asof, means=false, zero=false}: Obj) {
  const shown = (value: any) => value == null ? '—' : number(Number(value) * multiplier);
  return <Panel title={label} className="waterlevel-statistic">
    <div className="waterlevel-current"><strong>{shown(statistics.current)}</strong><span>{unit}</span>
      <small>{statistics.date||'尚无观测'} · {states[statistics.status]||statistics.status}</small>
    </div>
    <History {...{statistics,label,unit,multiplier,window,asof,means,zero}}/>
    <div className="waterlevel-window-values">
      {[5,10].map(years=><div key={years}><b>{years}年分位 {ratio(statistics.windows[years].percentile_ratio)}</b>
        {means&&<><span>均值 {shown(statistics.means[years].value)}</span><span>偏离 {ratio(statistics.means[years].deviation_ratio)}</span>
          <small>完整月 {statistics.means[years].count}/{statistics.means[years].required}</small></>}
        <small>分位样本 {statistics.windows[years].count} · {statistics.windows[years].status==='available'?'跨度完整':'历史不足'}</small>
      </div>)}
    </div>
  </Panel>;
}

function ComparisonCard({data, metric, setMetric, setMethod, window, loading}: Obj) {
  const comparison = data.comparisons[metric];
  const statistics = data.series.find((item:Obj)=>item.id==='full_market_'+metric).statistics;
  const definitions:ValuationLine[] = [
    {key:'value', label:metricNames[metric]+' · '+methods[data.method], color:'#365f91'},
    {key:'mean5', label:'5年完整月均值', color:'#bc8650'},
    {key:'mean10', label:'10年完整月均值', color:'#298879'},
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
  return <Panel title="全市场估值走势" className="waterlevel-comparison studio-valuation-panel" right={<span className="pill">理杏仁 A股全指 · 1000002</span>}>
    <div className="studio-controls">
      <div className="studio-choice"><span>估值方法</span><ValuationMethods methods={Object.keys(methods)} value={data.method} onChange={setMethod}/></div>
      <div className="studio-choice"><span>观察指标</span><div className="segmented" aria-label="全市场观察指标">
        {['pe','pb'].map(key=><button type="button" key={key} aria-pressed={metric===key} className={metric===key?'active':''} onClick={()=>setMetric(key)}>{metricNames[key]}</button>)}
      </div></div>
    </div>
    <div className="waterlevel-current"><strong>{number(statistics.current)}</strong><span>倍</span>
      <small>{statistics.date||'尚无观测'} · {states[statistics.status]}</small></div>
    <div className="studio-valuation-chart waterlevel-comparison-chart" aria-busy={loading}>
      {rows.some((row:Obj)=>row.value!=null)
        ? <ValuationTrend rows={rows} label="全市场估值与历史均值" lines={definitions} preserveBounds/>
        : <Notice>尚无所选口径的估值序列，可在数据来源手动更新理杏仁 A股全指。</Notice>}
    </div>
    <p className="muted small">左轴为估值倍数。两综指使用独立右轴，以 {comparison.base_month||'首个共同有效月份'} 为基准100。
      均值使用完整月末观测。{metric==='pe'?'国债收益率倒数仅作参考。':''}</p>
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

function MarketSources({data, run, busy, onIndices}: Obj) {
  return <>
    <Panel title="市场数据更新" right={<Button primary disabled={busy} onClick={()=>run(data.groups.map((g:Obj)=>g.group))}><RefreshCw size={14}/>更新全部来源</Button>}>
      <p className="muted small">市场数据只在这里手动更新。全市场估值使用本机理杏仁额度，其余来源按实际发布日期取数。</p>
      <div className="table-wrap"><table><thead><tr><th>数据组 / 来源</th><th>最近检查</th><th>状态</th><th>操作</th></tr></thead><tbody>
        {data.groups.map((group:Obj)=><tr key={group.group}><td>{group.label}<small>{group.publisher}</small></td>
          <td>{group.checked_at?.slice(0,19).replace('T',' ')||'尚未检查'}</td><td>{states[group.status]}<small>{group.error}</small>{group.notes.map((note:string)=><small key={note}>{note}</small>)}</td>
          <td><Button small disabled={busy} onClick={()=>run([group.group])}>更新</Button></td></tr>)}
      </tbody></table></div>
    </Panel>
    <Panel title="指数估值来源"><p>复用指数资料库中的理杏仁估值快照。请添加需要观察的指数并启用估值模块；来源和方法未取得时保持缺失。</p><Button onClick={onIndices}>打开指数资料库</Button></Panel>
    <UpdateHistory/>
    <Panel title="来源版本与方法边界">
      {data.sources.map((source:Obj)=><details className="research-source" key={source.snapshot_id}><summary>{source.index_id||source.group} · {source.publisher}</summary>
        <p className="muted small">检查 {source.checked_at} · 快照 {source.snapshot_id}<br/>SHA-256 {source.sha256}</p>
        {source.error&&<p className="muted small">更新失败：{source.error}</p>}
        {source.evidence.map((item:Obj,index:number)=><p key={index}><a href={item.url} target="_blank" rel="noopener noreferrer">来源 {index+1} ↗</a><small className="research-source-digest">抓取 {item.fetched_at} · {item.sha256}</small></p>)}
      </details>)}
      <details><summary>完整计算说明和当前缺口</summary><ul>{data.limitations.map((text:string)=><li key={text}>{text}</li>)}</ul></details>
    </Panel>
  </>;
}

export function WaterlevelPage({today,onIndices}: Obj) {
  const [method, setMethod] = useState('mcw');
  const [metric, setMetric] = useState('pe');
  const [fullMetric, setFullMetric] = useState('pe');
  const asof = today;
  const [tab, setTab] = useState('overview');
  const [window, setWindow] = useState('10');
  const [data, setData] = useState<Obj|null>(null);
  const [revision, setRevision] = useState(0);
  const [errorsVersion, setErrorsVersion] = useState('');
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reports, setReports] = useState<Obj[]>([]);
  const [selectedReport, setSelectedReport] = useState('');
  const [saving, setSaving] = useState(false);
  const submission = useSubmission(()=>setRevision(value=>value+1));

  useEffect(()=>{
    let active=true;
    setLoading(true);
    const request = api('/market-waterlevel?'+query({method,asof}));
    request.then(value=>{if(active){setData(value);setError('');}}).catch(error=>{if(active)setError(error.message);})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[method,asof,revision,errorsVersion]);

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
  function update(groups:string[]) {return submission.submit({scopes:['market'],resources:groups.map(group=>({scope:'market',group}))});}
  const blocked=loading||saving||submission.busy||running;
  const lines=(keys:string[])=>data?.series.filter((item:Obj)=>keys.includes(item.id)).map((item:Obj)=><StatisticCard key={item.id} label={item.label} statistics={item.statistics} unit={item.unit} window={window} asof={asof} zero={['money_gap','repo_spread','cn_term','us_term','erp'].includes(item.id)}/>);
  return <div className="waterlevel-page">
    <ErrorBox error={error||submission.error}/>
    {running&&<p className="muted small" role="status">更新正在执行，已有有效数据保留；结果与失败项可在数据来源中核对。</p>}
    {loading&&<p role="status" className="muted small">正在读取所选研究口径…</p>}
    {!data?<Empty title="市场水位尚未就绪">{error?'读取失败，请核对错误信息。':'正在读取本地资料。'}</Empty>:<>
      <div className="instrument-tabs" role="tablist" aria-label="市场水位栏目">{[['overview','水位总览'],['equity','股票估值'],['bonds','债券与资金'],['macro','宏观背景'],['sources','数据来源']].map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}</button>)}</div>
      {tab!=='sources'&&<div className="waterlevel-view-options"><span className="muted small">截至 {data.asof} · 各指标实际日期独立显示 · 月末采样，当月显示最新观测；全历史为已取得范围</span><label>曲线窗口 <select aria-label="水位曲线窗口" value={window} onChange={event=>setWindow(event.target.value)}><option value="5">5年</option><option value="10">10年</option><option value="all">全历史</option></select></label></div>}
      {tab==='overview'&&<><ScoreOverview data={data}/><ComparisonCard data={data} metric={fullMetric} setMetric={setFullMetric} setMethod={setMethod} window={window} loading={loading}/><div className="waterlevel-grid">{lines(['erp','bond_inverse'])}</div><details className="waterlevel-boundaries"><summary>当前覆盖与计算边界</summary><ul>{data.limitations.map((text:string)=><li key={text}>{text}</li>)}</ul></details></>}
      {tab==='equity'&&<>
        <h3>主要指数估值</h3>
        <div className="waterlevel-view-options"><div className="segmented" aria-label="市场估值方法">{Object.entries(methods).map(([key,label])=><button key={key} className={data.method===key?'active':''} disabled={loading} aria-pressed={data.method===key} onClick={()=>setMethod(key)}>{String(label)} · {key}</button>)}</div>
          <Field label="对照指标"><select aria-label="对照指标" value={metric} onChange={event=>setMetric(event.target.value)}>{Object.entries(metricNames).map(([key,label])=><option key={key} value={key}>{String(label)}</option>)}</select></Field></div>
        <p className="muted small">mcw按总市值汇总；ew为等权投资口径，可能出现非正估值；median反映典型成分。三者不能互相替代。均值仅包含截至上月的60/120个完整自然月。</p>
        <p className="waterlevel-legend"><span>蓝线：月末观测</span><span>棕线：5年均值</span><span>绿线：10年均值</span></p>
        <div className="waterlevel-grid">{data.indices.map((index:Obj)=><StatisticCard key={index.id} label={index.label+' · '+metricNames[metric]} statistics={index.metrics[metric]} unit={metric==='dividend_yield'?'%':'倍'} multiplier={metric==='dividend_yield'?100:1} window={window} asof={asof} means/>)}</div>
        <Button onClick={onIndices}>在指数资料库管理采集</Button>
      </>}
      {tab==='bonds'&&<><p className="muted small">信用利差按同日AAA 3Y中票减3Y国债；FR007/FDR007是定盘利率，不是R007/DR007。1个百分点=100 BP。</p><div className="waterlevel-grid">{lines(['cn_10y','cn_2y','cn_30y','us_10y','us_2y','us_30y','cn_term','us_term','credit_spread','aaa_3y','gov_3y','fr007','fdr007','repo_spread'])}</div></>}
      {tab==='macro'&&<><Notice>日期为统计月份，不能当作发布日期。社融使用人民银行直接公布的存量同比；M2−M1为同月同比之差，跨M1统计口径的历史仅供参考。</Notice><div className="waterlevel-grid">{lines(['pmi','nonmanufacturing_pmi','cpi_yoy','ppi_yoy','m1_yoy','m2_yoy','money_gap','sf_stock_yoy'])}</div><p className="muted small">本批来源未统一提供原始发布日期；未知日期保留为空，抓取时间见来源记录。</p></>}
      {tab==='sources'&&<><MarketSources data={data} run={update} busy={blocked} onIndices={onIndices}/>
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
