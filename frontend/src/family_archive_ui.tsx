import {useState} from 'react';
import {api,money,Obj} from './api';
import {Button,Empty,ErrorBox,Field,Modal,Notice,Panel} from './components';

export function ArchiveEditor({row,operation,catalog,today,onClose,onSaved}:Obj){
 const target=row.target||(row.linked?'link':'item'),id=target==='link'?(row.link_id||row.id):row.id;
 const source=target==='item'?catalog.items.find((i:Obj)=>i.id===id):catalog.links.find((l:Obj)=>l.id===id);
 const minimum=operation==='restore'?row.closed_on:target==='item'?source?.versions.map((v:Obj)=>v.effective_on).sort().at(-1):source?.effective_on;
 const[date,D]=useState(today),[reason,R]=useState(''),[preview,P]=useState<Obj|null>(null),[revision,V]=useState(catalog.revision),[busy,B]=useState(false),[error,E]=useState(''),[written,W]=useState(false);
 const restoring=operation==='restore';
 async function submit(){const commit=!!preview;B(true);E('');try{
   const result=await api('/family/'+(commit?'commands':'preview'),{operation,record_id:id,payload:{target,effective_on:date,reason},expected_revision:preview?.revision??revision});
   if(!commit){P(result);return;}
   W(true);const current=await api('/family/catalog');
   const saved=target==='item'?current.items.find((i:Obj)=>i.id===id)?.versions.at(-1):current.links.find((l:Obj)=>l.id===result.result.id);
   if(!saved||(!restoring&&saved.closed_on!==date)||(restoring&&(saved.closed_on||saved.effective_on!==date)))throw Error('保存后状态与预期不一致，请刷新核对。');
   await onSaved();
 }catch(e:any){E(e.message);if(e.status===409){P(null);V((await api('/family/catalog')).revision);}else if(commit&&(!e.status||e.status>=500)){W(true);}}finally{B(false);}}
 return <Modal title={(restoring?'恢复':'归档')+'：'+row.name} onClose={onClose} busy={busy}><p>{target==='link'?'只调整资产统计中的组合关联，组合与交易记录保留。':'保留历史档案和余额，归档生效前的统计不变。'}</p>
 <Notice>{restoring?'恢复后沿用最近记录，请核对当前余额。归档期间不会被补回。':'归档不是出售或收款。实际到账款请在现金项目另行记录。'}</Notice>
 <Field label={restoring?'恢复生效日期':'归档生效日期（当日不再计入）'}><input type="date" min={minimum} max={today} value={date} disabled={written||busy} onChange={e=>{D(e.target.value);P(null);}}/></Field>
 <Field label="说明（可选）"><input value={reason} maxLength={300} disabled={written||busy} onChange={e=>{R(e.target.value);P(null);}}/></Field>
 <ErrorBox error={error}/>{written&&<Notice>已提交保存请求，请回读核对；本入口不会重复提交。可关闭窗口后刷新台账。</Notice>}
 {preview&&<div className="family-preview"><b>{date} 起{restoring?'恢复计入':'退出统计'}</b><div>保存后当前资产 {money(preview.summary.provisional_assets)} · 负债 {money(preview.summary.total_debt)} · 净资产 {money(preview.summary.provisional_net)} CNY</div></div>}
 <footer className="form-actions"><Button onClick={onClose} disabled={busy}>返回</Button><Button primary disabled={busy||written} onClick={submit}>{preview?'确认'+(restoring?'恢复':'归档'):'检查并继续'}</Button></footer></Modal>;
}

export function ArchivedItems({catalog,selected,setSelected,onRestore,openPortfolio}:Obj){
 const rows:Obj[]=catalog.archive_entries||[],chosen=rows.find(r=>r.id===selected),history=chosen?.target==='item'?(catalog.value_history||[]).map((v:Obj,i:number)=>({...v,index:i})).filter((v:Obj)=>v.item_id===chosen.id).sort((a:Obj,b:Obj)=>b.effective_on.localeCompare(a.effective_on)||b.index-a.index):[];
 return <><Panel title="已归档项目"><p className="muted small">这些项目或组合关联已退出当前统计，历史记录保留。恢复会建立新的有效期。</p><div className="table-wrap"><table className="family-asset-table"><thead><tr><th>项目</th><th>归档生效日期</th><th>操作</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td><button className="text-button" onClick={()=>setSelected(r.id)}>{r.name}</button><small>{r.target==='link'?'已结束的组合关联':'资产项目'}</small></td><td>{r.closed_on}</td><td><div className="family-row-actions"><Button small onClick={()=>r.target==='link'?openPortfolio(r.portfolio_id):setSelected(r.id)}>查看历史</Button><Button small disabled={!r.can_restore} onClick={()=>onRestore(r)}>{r.can_restore?'恢复':'已重新关联'}</Button></div></td></tr>)}</tbody></table></div>{!rows.length&&<Empty title="暂无已归档项目"/>}</Panel>
 {chosen?.target==='item'&&<Panel title={chosen.name+' · 历史余额'}><p className="muted small">以下保留原始余额记录；归档期间不计入总览。</p><div className="table-wrap"><table><thead><tr><th>金额日期</th><th>记录余额 · {chosen.currency}</th><th>说明</th></tr></thead><tbody>{history.map((v:Obj)=><tr key={v.id}><td>{v.effective_on}</td><td>{money(v.household_total)}</td><td>{v.effective_on>=chosen.closed_on?'归档生效后记录':'历史余额'}</td></tr>)}</tbody></table></div></Panel>}</>;
}
