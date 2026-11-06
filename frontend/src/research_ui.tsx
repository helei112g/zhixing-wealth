import React,{useEffect,useState} from 'react';
import {LoaderCircle,Plus,RefreshCw} from 'lucide-react';
import {IndexUpdate} from './sync_ui';
import {api,money,Obj,query} from './api';
import {Button,Empty,ErrorBox,Notice,Panel} from './components';
import {StudioOverview,SourceReview,CapitalFlows,IndexFinancials,CollectionDialog,moduleLabels} from './research_studio';

const labels:Obj={...moduleLabels,index_prices:'指数价格',memberships:'成分名单与权重',valuations:'指数及成分估值',financials:'成分财务',quotes:'持仓行情',fx:'汇率'};
const num=(v:any,d=2)=>v==null?'-':money(v,d);

export function ResearchPage(_:Obj){
 const [catalog,C]=useState<Obj|null>(null),[selected,S]=useState(()=>{try{return new URLSearchParams(location.search).get('index')||localStorage.getItem('research-selected')||'';}catch{return '';}}),[revision,R]=useState(0),[running,Running]=useState(false),[dialog,Dialog]=useState<Obj|boolean|null>(null),[detail,Detail]=useState<Obj|null>(null),[error,E]=useState('');
 async function load(){const v=await api('/research/catalog');C(v);if(!v.subscriptions.some((p:Obj)=>p.id===selected))S(v.subscriptions[0]?.id||'');}
 useEffect(()=>{let active=true;api('/research/catalog').then(v=>{if(active){C(v);S(s=>v.subscriptions.some((p:Obj)=>p.id===s)?s:v.subscriptions[0]?.id||'');}}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[revision]);
 useEffect(()=>{let active=true,busy=false;const refresh=async()=>{if(document.hidden||busy)return;busy=true;try{const v=await api('/research/revision');if(active){R(v.research_revision);Running(v.updating);}}catch(e:any){if(active)E(e.message);}finally{busy=false;}};refresh();const timer=setInterval(refresh,2500);return()=>{active=false;clearInterval(timer);};},[]);
 function select(id:string){S(id);try{localStorage.setItem('research-selected',id);const u=new URL(location.href);u.searchParams.set('index',id);history.replaceState(null,'',u);}catch{}}
 const profile=catalog?.subscriptions.find((p:Obj)=>p.id===selected);
 return <div className="research-page"><ErrorBox error={error}/><div className="studio-heading"><div><label htmlFor="research-index">观察指数</label><select id="research-index" aria-label="观察指数" value={selected} onChange={e=>select(e.target.value)}>{!selected&&<option value="">尚未添加</option>}{catalog?.subscriptions.map((p:Obj)=><option key={p.id} value={p.id}>{p.name} · {p.code}{p.subscription?.enabled===false?' · 已暂停':''}</option>)}</select><IndexInformation d={detail?.id===selected?detail:profile}/></div><div className="inline-actions">{profile&&<Button onClick={()=>Dialog(profile)}>管理采集</Button>}<Button primary onClick={()=>Dialog(true)}><Plus size={15}/>添加指数</Button></div></div>{selected?<IndexDetail key={selected} id={selected} revision={revision} onLoaded={Detail}/>:<Empty title="还没有指数档案" action={<Button primary onClick={()=>Dialog(true)}>添加指数</Button>}>输入指数代码或名称，添加到指数库。</Empty>}{running&&<p role="status" className="muted small"><LoaderCircle size={14} className="spin"/>采集正在执行，已有有效快照可继续查看。</p>}{dialog&&catalog&&<CollectionDialog catalog={catalog} profile={dialog===true?null:dialog} onClose={()=>Dialog(null)} onSaved={async(v:Obj)=>{Dialog(null);select(v.id);R(v.research_revision);await load();}}/>}</div>;
}

function IndexDetail({id,revision,onLoaded}:Obj){
 const [basis,Basis]=useState('price'),[method,Method]=useState('mcw'),[window,Window]=useState(3),[tab,Tab]=useState('studio'),[d,D]=useState<Obj|null>(null),[error,E]=useState(''),[loading,L]=useState(false),[retry,Retry]=useState(0);
 useEffect(()=>{let active=true;L(true);E('');api('/research/indices/'+encodeURIComponent(id)+'?'+query({basis,method,window})).then(v=>{if(active){D(v);onLoaded(v);}}).catch(e=>{if(active)E(e.message);}).finally(()=>{if(active)L(false);});return()=>{active=false;};},[id,basis,method,window,revision,retry]);
 const current=d;
 return <><ErrorBox error={error}/>{!current?<Panel><p className="muted">{loading?'读取本地指数档案…':'暂无有效档案'}</p></Panel>:<>{loading&&<p role="status" className="muted small">正在读取所选口径，暂显示上次数据…</p>}<div className="research-detail-header"><div><h2>{current.profile.name} <small>{current.series_code}</small></h2><p className="muted small">{current.profile.provider==='csi'?'中证指数':'国证指数'} · {current.profile.kind} · CNY · 行情 {current.latest?.date||'-'} · 估值 {current.valuation.pe?.date||'-'} · 成分 {current.membership_date||'-'}</p></div><div className="segmented research-basis" aria-label="指数主口径">{[['price','价格指数'],['total_return','全收益指数']].map(([k,l])=><button key={k} aria-pressed={current.basis===k} className={current.basis===k?'active':''} onClick={()=>Basis(k)}>{l}</button>)}</div></div><div className="instrument-tabs research-tabs" role="tablist" aria-label="指数详情">{[['studio','观察台'],['composition','成分与财务'],['funds','资金与持仓'],['sources','数据来源']].map(([k,l])=><button key={k} role="tab" id={'research-tab-'+k} aria-controls="research-content" aria-selected={tab===k} onClick={()=>Tab(k)}>{l}</button>)}</div><div role="tabpanel" id="research-content" aria-labelledby={'research-tab-'+tab}>{tab==='studio'?<StudioOverview d={current} method={current.method} setMethod={Method} window={current.window} setWindow={(w:number)=>{Window(w);Retry(n=>n+1);}}/>:tab==='composition'?<CompositionPage d={current}/>:tab==='funds'?<CapitalFlows d={current}/>:<><IndexUpdate key={id} indexId={id} onUpdated={()=>Retry(n=>n+1)}/><IndexModuleStatus d={current}/><IndexSources d={current}/></>}</div></>}</>;
}
function IndexInformation({d}:Obj){
 const [open,Open]=useState(false);
 if(!d)return null;
 const p=d.profile||d;
 return <div className="index-info-anchor" onMouseEnter={()=>Open(true)} onMouseLeave={e=>{if(!e.currentTarget.contains(document.activeElement))Open(false);}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))Open(false);}} onKeyDown={e=>{if(e.key==='Escape')Open(false);}}>
  <button className="index-info-button" aria-expanded={open} aria-haspopup="dialog" onFocus={()=>Open(true)} onClick={()=>Open(true)}>ⓘ 基本资料</button>
  {open&&<div className="index-info-popover" role="dialog" aria-label="指数基本资料">
   <h3>{p.name} · 基本资料</h3><dl className="key-values">
    <dt>价格 / 全收益代码</dt><dd>{p.code} / {p.return_code || '理杏仁全收益口径'}</dd>
    <dt>编制机构 / 币种</dt><dd>{p.provider==='csi'?'中证指数':'国证指数'} / CNY</dd>
    <dt>成分数量</dt><dd>{d.companies?.length||'尚未采集'}</dd>
    <dt>价格指数发布日期</dt><dd>{p.launch}</dd>
    <dt>全收益独立发布日期</dt><dd>{p.return_launch||'待核验'}</dd>
    <dt>{p.lixinger_identity?'身份核对来源':'编制方案与规则'}</dt><dd><a href={p.method_url} target="_blank" rel="noopener noreferrer">{p.lixinger_identity?'理杏仁指数接口 ↗':'官方资料 ↗'}</a></dd>
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

function IndexSources({d}:Obj){return <><SourceReview d={d}/><Panel title="增量与15天校对"><p className="muted small">{d.collection.subscription.enabled?'采集已启用':'采集已暂停，历史保留'}；仅应用运行且本机自动采集开关开启时执行。</p><div className="table-wrap"><table><thead><tr><th>模块</th><th>最近增量</th><th>最近完整校对 / 下次</th><th>进度</th></tr></thead><tbody>{Object.entries(d.collection.modules).map(([k,value])=>{const v=value as Obj;return <tr key={k}><td>{moduleLabels[k]}</td><td>{v.last_incremental||'-'}</td><td>{v.last_reconciled||'-'}<small>{v.next_reconciliation||'-'}</small></td><td>{v.completed} / {v.planned} · {v.phase==='complete'?'完成':v.phase==='validation_failed'?'校验失败，等待手动核对':'分片采集中'}</td></tr>;})}</tbody></table></div></Panel><Panel title="数据版本与来源"><dl className="key-values"><dt>分析截止</dt><dd>{d.asof}</dd><dt>研究版本</dt><dd>{d.research_revision}</dd><dt>方法版本</dt><dd>{d.method_version}</dd><dt>分析摘要</dt><dd><code className="research-hash">{d.analysis_id}</code></dd></dl>{d.sources.map((s:Obj)=><details key={s.module} className="research-source"><summary>{labels[s.module]||s.module} · {s.effective_date||'日期待核验'}</summary><p className="muted small">抓取 {s.fetched_at}<br/>快照 {s.snapshot_id}<br/>SHA-256 <code className="research-hash">{s.sha256}</code></p>{s.limitation&&<p>{s.limitation}</p>}{s.evidence.map((e:Obj,i:number)=><p key={i}><a href={e.url} target="_blank" rel="noopener noreferrer">来源 {i+1} ↗</a><small className="muted research-source-digest">{e.sha256}</small></p>)}</details>)}</Panel><Notice>缺失、陈旧与覆盖不足分别保留；财报为当前可得版本，不能据此声称严格历史时点可得。当前指数可在本页更新与重试；全部更新记录在全局设置查看。</Notice></>;}

export function DataUpdateSettings(){
 const [d,D]=useState<Obj|null>(null),[draft,Draft]=useState<Obj[]>([]),[error,E]=useState(''),[busy,Busy]=useState(false),[review,Review]=useState(false),[message,Message]=useState('');
 async function load(preserve=false){const v=await api('/settings/data-updates');D(v);if(!preserve)Draft(v.policies);Review(false);}
 useEffect(()=>{load().catch(e=>E(e.message));},[]);
 async function save(){Busy(true);E('');try{const v=await api('/settings/data-updates',{policies:draft,expected_settings_revision:d?.settings_revision},'PUT');D(v);Draft(v.policies);Message('更新策略已保存');}catch(e:any){E(e.message);if(e.status===409||!e.status)Review(true);}finally{Busy(false);}}
 async function run(scopes:string[],targets?:string[],resources?:Obj[]){Busy(true);E('');try{await api('/data-updates/run',{scopes,targets,resources,request_id:crypto.randomUUID()});Message('已提交更新任务');}catch(e:any){E(e.message);}finally{Busy(false);}}
 return <><Panel title="数据更新" right={<span className="pill">全局统一管理</span>}><ErrorBox error={error}/><p className="muted small">仅在应用运行时执行，休眠后在下一次调度检查补跑；交易日slot按工作日安排，唤醒可补跑；节假日沿用来源最近有效日。</p>{d?<><div className="table-wrap"><table className="data-update-table"><thead><tr><th scope="col">数据范围</th><th scope="col">自动更新</th><th scope="col">频率</th><th scope="col">北京时间</th><th scope="col">操作</th></tr></thead><tbody>{draft.map((r:Obj,n:number)=><tr key={r.scope}><td>{labels[r.scope]}{(r.enabled!==d.policies[n].enabled||r.cadence!==d.policies[n].cadence||r.time!==d.policies[n].time)&&<small className="policy-saved-value">当前已保存：{d.policies[n].enabled?'开启':'关闭'} · {({daily:'每日',trade_day:'交易日',weekly:'每周一',manual:'仅手动'} as Obj)[d.policies[n].cadence]} {d.policies[n].time}</small>}</td><td><input type="checkbox" aria-label={labels[r.scope]+'自动更新'} checked={r.enabled} disabled={busy} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,enabled:e.target.checked}:x))}/></td><td><select aria-label={labels[r.scope]+'频率'} value={r.cadence} disabled={busy} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,cadence:e.target.value}:x))}><option value="daily">每日</option><option value="trade_day">交易日</option><option value="weekly">每周一</option><option value="manual">仅手动</option></select></td><td><input aria-label={labels[r.scope]+'时刻'} type="time" value={r.time} disabled={busy||r.cadence==='manual'} onChange={e=>Draft(draft.map((x,i)=>i===n?{...x,time:e.target.value}:x))}/></td><td><Button small disabled={busy} onClick={()=>run([r.scope])}>立即更新</Button></td></tr>)}</tbody></table></div><div className="research-settings-actions">{review&&<Button disabled={busy} onClick={()=>load(true).catch(e=>E(e.message))}>重新读取并核对（保留草稿）</Button>}<Button primary disabled={busy||review} onClick={save}>保存更新策略</Button><Button disabled={busy} onClick={()=>run(['quotes','fx','index_prices','memberships','valuations','financials'])}><RefreshCw size={14}/>全部更新</Button>{message&&<small role="status">{message}</small>}</div></>:<p className="muted">读取更新策略…</p>}</Panel></>;
}

function IndexModuleStatus({d}:Obj){return <Panel title="数据覆盖情况"><div className="table-wrap"><table><thead><tr><th>模块</th><th>状态</th><th>数据日期 / 原因</th></tr></thead><tbody>{Object.entries(d.module_status||{}).filter(([key])=>key!=='tracking_funds').map(([key,value])=>{const v=value as Obj;return <tr key={key}><td>{labels[key]||key}</td><td>{({available:'可查看',unsupported:'暂不支持',error:'更新失败',missing:'待更新'} as Obj)[v.status]}</td><td>{v.reason||v.date||'尚未取得有效数据'}</td></tr>;})}</tbody></table></div></Panel>;}
