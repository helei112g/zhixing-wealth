import React,{useEffect,useState} from 'react';
import {ArrowDown,ArrowDownUp,ArrowLeft,ArrowUp,LoaderCircle,Plus} from 'lucide-react';
import {api,money,Obj,percent,query} from './api';
import {Button,Empty,ErrorBox,Notice,Panel} from './components';
import {StudioOverview,CapitalFlows,CollectionDialog,IndexFinancials,moduleLabels} from './research_studio';
import {activeJob,useSubmission} from './sync_ui';
import {IndexObservationDates,IndexValuationCell,IndexValuationPosition,primaryValuations,ValuationLegend,valuationGroups,valuationLabel} from './index_valuation';
import type {ValuationGroup} from './index_valuation';

const num=(v:any,d=2)=>v==null?'-':money(v,d);
const indexScopes=['index_prices','memberships','valuations','financials'];
const jobStates:Obj={pending:'等待中',running:'更新中',complete:'已完成',partial:'部分失败',error:'更新失败',interrupted:'已中断'};
const annualOffsets=[1,2,3] as const;
const recentReturnColumns=[
 {key:'ytd',label:'今年'},
 {key:'monthly',label:'本月'},
 {key:'daily',label:'上一个交易日'},
] as const;
type ReturnColumn=`annual_${typeof annualOffsets[number]}`|typeof recentReturnColumns[number]['key'];
type ReturnSort={key:ReturnColumn;direction:'asc'|'desc'};

export function ResearchPage({onData}:Obj){
 const [catalog,C]=useState<Obj|null>(null),[selected,S]=useState(''),[revision,R]=useState(0),[running,Running]=useState(false),[error,E]=useState('');
 const [collectionDialog,setCollectionDialog]=useState<{profile?:Obj}|null>(null);
 const [initialSync, setInitialSync]=useState('');
 const [listPage,setListPage]=useState(1);
 const [group,setGroup]=useState<ValuationGroup>('all');
 const [listSort,setListSort]=useState<ReturnSort|null>(null);
 const [indexJob,setIndexJob]=useState<Obj|null>(null);
 useEffect(()=>{let active=true;api('/research/catalog').then(v=>{if(active){C(v);S(s=>v.subscriptions.some((p:Obj)=>p.id===s)?s:'');E('');}}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[revision]);
 useEffect(()=>{let active=true,busy=false;const refresh=async()=>{if(document.hidden||busy)return;busy=true;try{const v=await api('/research/revision');if(active){R(v.research_revision);Running(v.updating);}}catch(e:any){if(active)E(e.message);}finally{busy=false;}};refresh();const timer=setInterval(refresh,2500);return()=>{active=false;clearInterval(timer);};},[]);
 const profile=catalog?.subscriptions.find((p:Obj)=>p.id===selected);

 function sortList(key:ReturnColumn){
  setListSort(previous=>({key,direction:previous?.key===key&&previous.direction==='desc'?'asc':'desc'}));
  setListPage(1);
 }

 return <div className="research-page">
  <ErrorBox error={error}/>
  <div className="studio-heading">
   {selected?<button type="button" className="text-button back-link" onClick={()=>S('')}><ArrowLeft size={14}/>返回指数列表</button>
    :<p className="muted small">浏览已建档指数，选择名称查看详情。</p>}
   <div className="inline-actions">
    {profile&&<Button onClick={()=>onData(selected)}>数据管理</Button>}
    <Button primary disabled={!catalog} onClick={()=>setCollectionDialog({})}><Plus size={15}/>添加采集</Button>
   </div>
  </div>
  {selected?<IndexDetail key={selected} id={selected} revision={revision}/>
   :<IndexList revision={revision} running={running} page={listPage} onPage={setListPage} onSelect={S} onData={onData}
    group={group} onGroup={(value:ValuationGroup)=>{setGroup(value);setListPage(1);}}
    sort={listSort} onSort={sortList} onResetSort={()=>{setListSort(null);setListPage(1);}} job={indexJob} setJob={setIndexJob}
    onEdit={catalog&&((row:Obj)=>{
     const saved=catalog.subscriptions.find((profile:Obj)=>profile.id===row.id);
     if(saved)setCollectionDialog({profile:saved});
    })}/>}
  {initialSync===selected&&initialSync&&<p role="status" className="muted small">首次全量任务已受理。<button className="text-button" onClick={()=>onData(selected)}>在数据管理查看进度和结果</button></p>}
  {running&&<p role="status" className="muted small"><LoaderCircle size={14} className="spin"/>采集正在执行，已有有效快照可继续查看。</p>}
  {collectionDialog&&catalog&&<CollectionDialog catalog={catalog} profile={collectionDialog.profile} onClose={()=>setCollectionDialog(null)} onSaved={(value:Obj)=>{
   setCollectionDialog(null);
   if(!collectionDialog.profile)S(value.id);
   if(value.job_id){setInitialSync(value.id);Running(true);}
   R(value.research_revision);
  }}/>}
 </div>;
}

function IndexList({revision,running,page,onPage,onSelect,onData,group,onGroup,sort,onSort,onResetSort,job,setJob,onEdit}:Obj){
 const [listing,setListing]=useState<Obj|null>(null);
 const [error,setError]=useState('');
 const [listError,setListError]=useState('');
 const [retry,setRetry]=useState(0);
 const submission=useSubmission(()=>{});
 const pending=job&&activeJob(job);

 useEffect(()=>{
  let active=true;
  setListError('');
  api('/research/indices?'+query({method:'mcw',page,valuation_group:group,sort_by:sort?.key,sort_direction:sort?.direction})).then(value=>{
   if(!active)return;
   const lastPage=Math.max(1,Math.ceil(value.total/value.page_size));
   if(page>lastPage)onPage(lastPage);
   else setListing({...value,sort});
  }).catch(error=>{if(active)setListError(error.message);});
  return()=>{active=false;};
 },[page,revision,sort,group,retry]);

 useEffect(()=>{
  if(!pending)return;
  let active=true, busy=false;
  async function load(){
   if(busy||document.hidden)return;
   busy=true;
   try{
    const result=await api('/data-updates/jobs/'+job!.id);
    if(active){setJob({...result,target:job!.target});setError('');}
   }catch(error:any){if(active)setError(error.message);}
   finally{busy=false;}
  }
  load();
  const timer=setInterval(load,2500);
  return()=>{active=false;clearInterval(timer);};
 },[job?.id,pending]);

 async function update(id:string){
  const result=await submission.submit({scopes:indexScopes,targets:[id]});
  if(result)setJob({id:result.job_id,target:id,state:'pending'});
 }

 const current=listing?.valuation_group===group?listing:null;
 const rows:Obj[]=current?.items||[];
 const counts:Record<string,number>=listing?.valuation_counts||{};
 const total=Object.values(counts).reduce((sum,count)=>sum+count,0);
 const annualColumns=annualOffsets.map(offset=>({
  key:`annual_${offset}` as const,
  year:Number(current?.calendar_year)-offset,
 }));
 const appliedSort:ReturnSort|null|undefined=current?.sort;
 const loading=current?.page!==page||appliedSort!==sort;
 const grouped=group==='all'&&!appliedSort;
 const columnCount=3+primaryValuations.length+annualColumns.length+recentReturnColumns.length;
 const returnHeader=(key:ReturnColumn,label:string)=>{
  const direction=appliedSort?.key===key?appliedSort.direction:null;
  const Icon=direction==='asc'?ArrowUp:direction==='desc'?ArrowDown:ArrowDownUp;
  return <th key={key} scope="col" className="index-return-sort-heading"
   aria-sort={direction==='asc'?'ascending':direction==='desc'?'descending':undefined}>
   <button type="button" onClick={()=>onSort(key)}>
    {label}<Icon size={13} aria-hidden="true"/>
   </button>
  </th>;
 };
 const ratioCell=(key:string|number, change?:Obj)=><td key={key} title={change?change.from_date+' 至 '+change.to_date:'缺少可用取价'}>
  <span className={Number(change?.ratio)>0?'positive':Number(change?.ratio)<0?'negative':''}>{percent(change?.ratio,true)}</span>
 </td>;
 return <>
  <ErrorBox error={listError||error||submission.error}/>
  {listError&&<Button small onClick={()=>setRetry(value=>value+1)}>重试读取指数列表</Button>}
  {listing&&total>0&&<nav className="index-valuation-groups" aria-label="主估值指标分组">
   {[{key:'all',label:'全部'},...valuationGroups].map(option=><button key={option.key} type="button"
    aria-label={option.label} aria-pressed={group===option.key} onClick={()=>onGroup(option.key)}>
    <span>{option.label}</span><b>{option.key==='all'?total:counts[option.key]??0}</b>
   </button>)}
  </nav>}
  {!current?(listError?null:<p className="muted small" role="status">正在读取本地指数列表…</p>)
   :!rows.length?(group==='all'?<Empty title="还没有指数档案">选择要采集的指数和模块。添加后即可在此查看进度和数据。</Empty>:<Empty title="该分类暂无指数"/>)
   :<Panel title="指数列表" right={<div className="inline-actions"><small className="muted">{current.total} 个指数</small>
    {sort&&<Button small onClick={onResetSort}>{group==='all'?'恢复分组':'清除收益排序'}</Button>}</div>}>
    <ValuationLegend/>
    <p className="muted small">估值采用总市值加权。各指标列显示原始 5 年 / 10 年分位；主指标为股息率时，估值位置用 1 减原始分位。历史不足显示「—」。涨跌幅按价格指数收盘点位计算，年、月以上期末收盘为基准。</p>
    <div className="table-wrap"><table className="index-library-table" aria-label="指数列表" aria-busy={loading&&!listError}>
     <thead><tr>
      <th scope="col">指数</th><th scope="col">主指标估值位置</th>
      {primaryValuations.map(metric=><th key={metric.key} scope="col">{metric.short}</th>)}
      {annualColumns.map(({key,year})=>returnHeader(key,year+'年'))}
      {recentReturnColumns.map(({key,label})=>returnHeader(key,label))}
      <th scope="col">操作</th>
     </tr></thead>
     <tbody>{rows.map((row,i)=>{
      const rowJob=job?.target===row.id?job:null;
      const paused=row.profile.subscription?.enabled===false;
      const category=row.profile.primary_valuation||'unclassified';
      const previous=rows[i-1]?.profile.primary_valuation||'unclassified';
      return <React.Fragment key={row.id}>
       {grouped&&(i===0||category!==previous)&&<tr className="index-valuation-group-row" data-valuation-group={category}>
        <td colSpan={columnCount}><b>{valuationLabel(category)}</b><span>{counts[category]} 个指数</span></td></tr>}
       <tr data-index-id={row.id}>
       <th scope="row"><button className="text-button" onClick={()=>onSelect(row.id)}>{row.profile.name}</button>
        <small>{row.profile.code}{paused?' · 已暂停':''}</small>
        <IndexObservationDates priceDate={row.latest?.date} valuations={row.valuation}/>
        <button className="text-button index-classification-edit" type="button" disabled={!onEdit} onClick={()=>onEdit(row)}>{category==='unclassified'?'设置分类':'编辑分类'}</button>
       </th>
       <td><IndexValuationPosition position={row.valuation_position} valuations={row.valuation}/></td>
       {primaryValuations.map(metric=><IndexValuationCell key={metric.key} metric={metric.key} value={row.valuation[metric.key]}/>)}
       {annualColumns.map(({year})=>ratioCell(year,row.changes.annual[year]))}
       {recentReturnColumns.map(({key})=>ratioCell(key,row.changes[key]))}
       <td><button type="button" className="text-button" disabled={running||submission.busy||pending||paused} onClick={()=>update(row.id)}>更新数据</button>
        {rowJob&&<small role="status">{jobStates[rowJob.state]||rowJob.state}{['error','partial','interrupted'].includes(rowJob.state)&&
         <> · <button className="text-button" onClick={()=>onData(row.id)}>查看结果</button></>}</small>}
       </td>
       </tr>
      </React.Fragment>;
     })}</tbody>
    </table></div>
    {current.total>current.page_size&&<div className="research-pagination">
     <Button disabled={current.page===1||loading&&!listError} onClick={()=>onPage(current.page-1)}>上一页</Button>
     <small>{current.page} / {Math.ceil(current.total/current.page_size)}</small>
     <Button disabled={current.page*current.page_size>=current.total||loading&&!listError} onClick={()=>onPage(current.page+1)}>下一页</Button>
    </div>}
   </Panel>}
 </>;
}

function IndexDetail({id,revision}:Obj){
 const [basis,Basis]=useState('price'),[method,Method]=useState('mcw'),[window,Window]=useState(3),[tab,Tab]=useState('studio'),[d,D]=useState<Obj|null>(null),[error,E]=useState(''),[loading,L]=useState(false);
 useEffect(()=>{let active=true;L(true);E('');api('/research/indices/'+encodeURIComponent(id)+'?'+query({basis,method,window})).then(v=>{if(active)D(v);}).catch(e=>{if(active)E(e.message);}).finally(()=>{if(active)L(false);});return()=>{active=false;};},[id,basis,method,window,revision]);

 if(!d)return <><ErrorBox error={error}/><Panel><p className="muted">{loading?'读取本地指数档案…':'暂无有效档案'}</p></Panel></>;
 const {profile}=d;
 return <>
  <ErrorBox error={error}/>
  {loading&&<p role="status" className="muted small">正在读取所选口径，暂显示上次数据…</p>}
  <div className="research-detail-header">
   <div>
    <div className="research-index-identity">
     <h2>{profile.name} <small>{d.series_code}</small></h2>
     <IndexInformation d={d}/>
    </div>
    <p className="muted small">{profile.provider==='csi'?'中证指数':'国证指数'} · {profile.kind} · CNY · 行情 {d.latest?.date||'-'} · 估值 {d.valuation.pe?.date||'-'} · 成分 {d.membership_date||'-'}{profile.subscription?.enabled===false?' · 已暂停':''}</p>
   </div>
   <div className="segmented research-basis" aria-label="指数主口径">
    {[['price','价格指数'],['total_return','全收益指数']].map(([k,l])=><button key={k} aria-pressed={d.basis===k} className={d.basis===k?'active':''} onClick={()=>Basis(k)}>{l}</button>)}
   </div>
  </div>
  <div className="instrument-tabs research-tabs" role="tablist" aria-label="指数详情">
   {[['studio','观察台'],['composition','成分与财务'],['funds','资金与持仓']].map(([k,l])=><button key={k} role="tab" id={'research-tab-'+k} aria-controls="research-content" aria-selected={tab===k} onClick={()=>Tab(k)}>{l}</button>)}
  </div>
  <div role="tabpanel" id="research-content" aria-labelledby={'research-tab-'+tab}>
   {tab==='studio'?<StudioOverview d={d} method={d.method} setMethod={Method} window={d.window} setWindow={Window}/>:tab==='composition'?<CompositionPage d={d}/>:<CapitalFlows d={d}/>}
  </div>
 </>;
}
function IndexInformation({d}:Obj){
 const [open,Open]=useState(false);
 const p=d.profile;
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
