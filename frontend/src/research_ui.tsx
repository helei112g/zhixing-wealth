import React,{useEffect,useState} from 'react';
import {LoaderCircle,Plus} from 'lucide-react';
import {api,money,Obj,query} from './api';
import {Button,Empty,ErrorBox,Notice,Panel} from './components';
import {StudioOverview,CapitalFlows,CollectionDialog,IndexFinancials,moduleLabels} from './research_studio';

const num=(v:any,d=2)=>v==null?'-':money(v,d);

export function ResearchPage({onData}:Obj){
 const [catalog,C]=useState<Obj|null>(null),[selected,S]=useState(()=>{try{return new URLSearchParams(location.search).get('index')||localStorage.getItem('research-selected')||'';}catch{return '';}}),[revision,R]=useState(0),[running,Running]=useState(false),[detail,Detail]=useState<Obj|null>(null),[error,E]=useState('');
 const [adding, setAdding]=useState(false);
 const [initialSync, setInitialSync]=useState('');
 useEffect(()=>{let active=true;api('/research/catalog').then(v=>{if(active){C(v);S(s=>v.subscriptions.some((p:Obj)=>p.id===s)?s:v.subscriptions[0]?.id||'');}}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[revision]);
 useEffect(()=>{let active=true,busy=false;const refresh=async()=>{if(document.hidden||busy)return;busy=true;try{const v=await api('/research/revision');if(active){R(v.research_revision);Running(v.updating);}}catch(e:any){if(active)E(e.message);}finally{busy=false;}};refresh();const timer=setInterval(refresh,2500);return()=>{active=false;clearInterval(timer);};},[]);
 function select(id:string){S(id);try{localStorage.setItem('research-selected',id);const u=new URL(location.href);u.searchParams.set('index',id);history.replaceState(null,'',u);}catch{}}
 const profile=catalog?.subscriptions.find((p:Obj)=>p.id===selected);
 return <div className="research-page">
  <ErrorBox error={error}/>
  <div className="studio-heading">
   <div>
    <label htmlFor="research-index">观察指数</label>
    <select id="research-index" aria-label="观察指数" value={selected} onChange={e=>select(e.target.value)}>
     {!selected&&<option value="">尚未添加</option>}
     {catalog?.subscriptions.map((p:Obj)=><option key={p.id} value={p.id}>
      {p.name} · {p.code}{p.subscription?.enabled===false?' · 已暂停':''}
     </option>)}
    </select>
    <IndexInformation d={detail?.id===selected?detail:profile}/>
   </div>
   <div className="inline-actions">
    {profile&&<Button onClick={()=>onData(selected)}>数据管理</Button>}
    <Button primary disabled={!catalog} onClick={()=>setAdding(true)}><Plus size={15}/>添加采集</Button>
   </div>
  </div>
  {selected?<IndexDetail key={selected} id={selected} revision={revision} onLoaded={Detail}/>
   :<Empty title="还没有指数档案">选择要采集的指数和模块。内置目录不会自动采集，打开页面只读取本地数据。</Empty>}
  {initialSync===selected&&initialSync&&<p role="status" className="muted small">首次全量任务已受理。<button className="text-button" onClick={()=>onData(selected)}>在数据管理查看进度和结果</button></p>}
  {running&&<p role="status" className="muted small"><LoaderCircle size={14} className="spin"/>采集正在执行，已有有效快照可继续查看。</p>}
  {adding&&catalog&&<CollectionDialog catalog={catalog} onClose={()=>setAdding(false)} onSaved={(value:Obj)=>{
   setAdding(false);
   select(value.id);
   if(value.job_id){setInitialSync(value.id);Running(true);}
   R(value.research_revision);
  }}/>}
 </div>;
}

function IndexDetail({id,revision,onLoaded}:Obj){
 const [basis,Basis]=useState('price'),[method,Method]=useState('mcw'),[window,Window]=useState(3),[tab,Tab]=useState('studio'),[d,D]=useState<Obj|null>(null),[error,E]=useState(''),[loading,L]=useState(false);
 useEffect(()=>{let active=true;L(true);E('');api('/research/indices/'+encodeURIComponent(id)+'?'+query({basis,method,window})).then(v=>{if(active){D(v);onLoaded(v);}}).catch(e=>{if(active)E(e.message);}).finally(()=>{if(active)L(false);});return()=>{active=false;};},[id,basis,method,window,revision]);
 const current=d;
 return <><ErrorBox error={error}/>{!current?<Panel><p className="muted">{loading?'读取本地指数档案…':'暂无有效档案'}</p></Panel>:<>{loading&&<p role="status" className="muted small">正在读取所选口径，暂显示上次数据…</p>}<div className="research-detail-header"><div><h2>{current.profile.name} <small>{current.series_code}</small></h2><p className="muted small">{current.profile.provider==='csi'?'中证指数':'国证指数'} · {current.profile.kind} · CNY · 行情 {current.latest?.date||'-'} · 估值 {current.valuation.pe?.date||'-'} · 成分 {current.membership_date||'-'}</p></div><div className="segmented research-basis" aria-label="指数主口径">{[['price','价格指数'],['total_return','全收益指数']].map(([k,l])=><button key={k} aria-pressed={current.basis===k} className={current.basis===k?'active':''} onClick={()=>Basis(k)}>{l}</button>)}</div></div><div className="instrument-tabs research-tabs" role="tablist" aria-label="指数详情">{[['studio','观察台'],['composition','成分与财务'],['funds','资金与持仓']].map(([k,l])=><button key={k} role="tab" id={'research-tab-'+k} aria-controls="research-content" aria-selected={tab===k} onClick={()=>Tab(k)}>{l}</button>)}</div><div role="tabpanel" id="research-content" aria-labelledby={'research-tab-'+tab}>{tab==='studio'?<StudioOverview d={current} method={current.method} setMethod={Method} window={current.window} setWindow={Window}/>:tab==='composition'?<CompositionPage d={current}/>:<CapitalFlows d={current}/>}</div></>}</>;
}
function IndexInformation({d}:Obj){
 const [open,Open]=useState(false);
 if(!d)return null;
 const p=d.profile||d;
 return <div className="index-info-anchor" onMouseEnter={()=>Open(true)} onMouseLeave={e=>{if(!e.currentTarget.contains(document.activeElement))Open(false);}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))Open(false);}} onKeyDown={e=>{if(e.key==='Escape')Open(false);}}>
  <button className="index-info-button" aria-expanded={open} aria-haspopup="dialog" onFocus={()=>Open(true)} onClick={()=>Open(true)}>ⓘ 基本资料</button>
  {open&&<div className="index-info-popover" role="dialog" aria-label="指数基本资料">
   <h3>{p.name} · 基本资料</h3><dl className="key-values">
    <dt>价格 / 全收益代码</dt><dd>{p.code} / {p.return_code||'理杏仁同代码全收益序列'}</dd>
    <dt>编制机构 / 币种</dt><dd>{p.provider==='csi'?'中证指数':'国证指数'} / CNY</dd>
    <dt>成分数量</dt><dd>{d.companies?.length||'尚未采集'}</dd>
    <dt>价格指数发布日期</dt><dd>{p.launch}</dd>
    <dt>全收益独立发布日期</dt><dd>{p.return_launch||'待核验'}</dd>
    <dt>编制方案与规则</dt><dd>{p.method_url?<a href={p.method_url} target="_blank" rel="noopener noreferrer">{p.lixinger_identity?'理杏仁身份来源 ↗':'官方资料 ↗'}</a>: '独立官方资料待核验'}</dd>
    <dt>加权 / 调样</dt><dd>{d.index_info?.calculationMethod||'以官方方案为准'} / {d.index_info?.rebalancingFrequency||'待核验'}</dd>
   </dl>
  </div>}
 </div>;
}

function IndustryDistribution({d}:Obj){
 const groups:Obj={};
 if(d.memberships_complete){
  d.companies.forEach((r:Obj)=>{const name=r.industry||'行业待核验';groups[name]=(groups[name]||0)+Number(r.weight);});
 }
 const entries=Object.entries(groups).sort((a,b)=>Number(b[1])-Number(a[1]));
 const colors=['#3e6d9b','#2c8a7b','#9474aa','#c18a50','#738d48'];
 return <Panel title="行业分布" right={<small className="muted">权重日期 {d.membership_date||'-'}</small>}>
  {entries.length?<ol className="industry-list" style={{'--industry-row-count':Math.ceil(entries.length/2)} as React.CSSProperties}>
   {entries.map(([name,weight],i)=><li className="research-weight" key={name}>
    <span className="industry-rank">{i+1}</span><span className="industry-name">{name}</span>
    <div><i style={{width:Math.min(100,Number(weight))+'%',background:colors[i%colors.length]}}/></div>
    <b>{num(weight)}%</b>
   </li>)}
  </ol>:<p className="muted">缺少全量同日权重，暂不计算行业分布。</p>}
 </Panel>;
}

function CompositionPage({d}:Obj){
 const [section,Section]=useState('finance');
 const sections=[['finance','指数财务'],['companies','成分公司']];
 const modules=section==='finance'?['index_financials']:['memberships','stock_valuations','financials'];
 return <>
  <IndustryDistribution d={d}/>
  <div className="segmented composition-tabs" aria-label="成分与财务内容">{sections.map(([k,label])=>
   <button key={k} aria-pressed={section===k} className={section===k?'active':''} onClick={()=>Section(k)}>{label}</button>
  )}</div>
  <div className="composition-coverage">{modules.map(m=>{const v=d.module_status?.[m];return <div key={m}>
   <b>{moduleLabels[m]}</b><span>{v?.status==='available'?'已取得 · '+(v.date||'日期未提供'):v?.status==='error'?'采集失败':v?.status==='unsupported'?'暂不支持':'尚未采集'}</span>{v?.reason&&<small>{v.reason}</small>}
  </div>;})}</div>
  {section==='finance'?<IndexFinancials d={d}/>:<IndexComposition d={d}/>}
 </>;
}

function IndexComposition({d}:Obj){
 const [period,Period]=useState('');
 const [page,Page]=useState(1);
 const periods=Object.keys(d.financials.periods).sort().reverse();
 const selectedPeriod=periods.includes(period)?period:(d.financials.main_period||'');
 const rows=d.companies;
 useEffect(()=>Page(1),[d.id,d.membership_date]);
 return <><div className="company-period"><label>财报期 <select aria-label="公司财报期" value={selectedPeriod} onChange={e=>Period(e.target.value)}>{periods.map(v=><option key={v}>{v}</option>)}</select></label></div><section className="list-section research-companies-section"><div className="list-section-head"><h2>成分公司 <small>权重从高到低 · 前10大合计 {num(d.top10_weight)}%</small></h2><small className="muted">财报期 {selectedPeriod||'-'} · 金额 / 人民币亿元</small></div><p className="muted small">前10大以蓝色与排名标记；公司财务与估值日期分别列明。现金流公司明细保留金融公司，指数现金流合计仅含已识别的非金融公司。</p><div className="table-wrap"><table className="research-companies"><thead><tr><th scope="col">公司 / 代码</th><th scope="col">行业</th><th scope="col">权重</th><th scope="col">PE TTM · 倍</th><th scope="col">PB · 倍</th><th scope="col">营业收入 · 亿元</th><th scope="col">归母净利润 · 亿元</th><th scope="col">经营现金流净额 · 亿元</th><th scope="col">估值日期 / 来源</th></tr></thead><tbody>{rows.slice((page-1)*20,page*20).map((r:Obj)=>{const f=r.financials?.[selectedPeriod];return <tr key={r.code} className={r.weight_rank<=10?'top-company':''} data-weight-rank={r.weight_rank}><td><span className="company-weight-rank">{r.weight_rank<=10?'TOP ':''}{r.weight_rank}</span>{r.name}<small>{r.code} · {r.market}</small></td><td>{r.industry||'-'}</td><td>{num(r.weight)}%</td><td>{num(r.valuation?.pe)}<small>{Number(r.valuation?.pe)<0?'亏损':''}</small></td><td>{num(r.valuation?.pb)}<small>{r.valuation?.pb_status==='nonpositive_equity'?'非正净资产':''}</small></td>{['revenue','profit','operating_cf'].map(m=><td key={m}>{f?.[m]==null?'-':num(Number(f[m])/1e8)}<small>{f?.[m+'_published_at']||'公告日期未取得'}</small></td>)}<td>{r.valuation?.date||'-'}<small>{r.valuation?.source||'尚无估值'}</small></td></tr>;})}</tbody></table></div>{rows.length>20&&<div className="research-pagination"><Button disabled={page===1} onClick={()=>Page(page-1)}>上一页</Button><small>{page} / {Math.ceil(rows.length/20)}</small><Button disabled={page*20>=rows.length} onClick={()=>Page(page+1)}>下一页</Button></div>}</section></>;
}
