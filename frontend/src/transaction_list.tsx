import {useEffect,useRef,useState} from 'react';
import {api,marketNames,Obj,query} from './api';
import {Button,Empty,ErrorBox,ListSection,Panel} from './components';
import {TransactionTable} from './transactions_ui';

// One state owner for filtering/pagination; keep the data viewport mounted on refresh.
export function TransactionList({pid,instrumentId='',version,action,selected='',onSelect,openDetail}:Obj){
 const [filters,Filters]=useState({market:'ALL',tag:'',asset_class:'',region:'',page:1,page_size:20});
 const [result,Result]=useState<Obj|null>(null),[error,Error]=useState(''),[loading,Loading]=useState(true),[retry,Retry]=useState(0);
 const viewport=useRef<HTMLDivElement>(null),key=pid+':'+instrumentId,params=query({...filters,instrument_id:instrumentId});
 useEffect(()=>{Filters(f=>({...f,market:'ALL',tag:'',asset_class:'',region:'',page:1}));Result(null);},[key]);
 useEffect(()=>{let active=true;Loading(true);Error('');api('/portfolios/'+pid+'/transactions?'+params).then(v=>{if(active){Result({...v,key,params});if(viewport.current)viewport.current.scrollTop=0;}}).catch(e=>{if(active)Error(e.message);}).finally(()=>{if(active)Loading(false);});return()=>{active=false;};},[key,params,version,retry]);
 const data=result?.key===key?result:null,busy=loading||(!error&&data?.params!==params),visible=!!data&&!busy&&!error;
 const change=(name:string,value:string|number)=>Filters(f=>({...f,[name]:value,page:1}));
 const total=data?.total||0,page=data?.page||1,pages=data?.page_count||1,size=filters.page_size;
 const Container=instrumentId?Panel:ListSection;
 return <Container className={'transaction-list'+(!instrumentId?' ledger-list':'')} title={<>{instrumentId?'本标的全部记录':'流水明细'} <small>{data?`筛选后 ${total} / 共 ${data.unfiltered_total||0} 笔`:'正在读取…'}</small></>} right={!instrumentId&&<Button small onClick={()=>action('csv')}>CSV导入 / 导出</Button>}>
 <div className="transaction-filters">
 {!instrumentId&&<label>市场<select aria-label="流水市场筛选" value={filters.market} onChange={e=>change('market',e.target.value)}>{[['ALL','全部市场'],...Object.entries(marketNames)].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>}
 {!instrumentId&&[['tag','主题标签','tags'],['asset_class','资产类别','asset_classes'],['region','地区','regions']].map(([name,label,facet])=><label key={name}>{label}<select aria-label={'流水'+label+'筛选'} value={filters[name as keyof typeof filters]} onChange={e=>change(name,e.target.value)}><option value="">全部{label}</option>{Array.from(new Set([...(data?.facets?.[facet]||[]),filters[name as keyof typeof filters]])).filter(Boolean).map(v=><option key={String(v)} value={String(v)}>{String(v)}</option>)}</select></label>)}
 </div>
 <div className="transaction-query-status" aria-live="polite">{error?<><ErrorBox error={error}/><Button small onClick={()=>Retry(n=>n+1)}>重试读取</Button><small>以下保留此前结果，尚未应用新筛选。</small></>:<small className="muted">{busy?'正在更新列表…':instrumentId?'仅显示当前组合、当前标的的记录。':'按本组合当前计划标签筛选；各条件同时生效。'}</small>}</div>
 <div className="transaction-viewport" ref={viewport} aria-busy={busy}>
 <div className={'transaction-data'+(busy?' updating':'')} inert={busy||!!error||undefined}>
 {data?.items.length?<TransactionTable rows={data.items} action={action} context={instrumentId?'instrument':'ledger'} selected={selected} onSelect={onSelect} openDetail={openDetail}/>:!busy&&!error?<Empty title="没有符合筛选条件的流水">可调整筛选条件，或记录一笔交易。</Empty>:<div className="transaction-placeholder">正在读取交易收益…</div>}
 </div></div>
 <nav className="transaction-pagination" aria-label="流水分页"><span>{data&&total?`第 ${(page-1)*size+1}–${Math.min(page*size,total)} 笔 · `:''}第 {page} / {pages} 页</span><label>每页<select aria-label="每页流水数量" value={size} onChange={e=>change('page_size',Number(e.target.value))}>{[20,50,100].map(n=><option key={n} value={n}>{n} 笔</option>)}</select></label><Button small disabled={!visible||page<=1} onClick={()=>Filters(f=>({...f,page:page-1}))}>上一页</Button><Button small disabled={!visible||page>=pages} onClick={()=>Filters(f=>({...f,page:page+1}))}>下一页</Button></nav>
 </Container>;
}
