import {useEffect,useState} from 'react';
import {api,kindNames,money,Obj,units} from './api';
import {Button,ErrorBox,Field,Modal,Notice} from './components';

export function DeleteEventModal({event,onClose,onSaved}:Obj){
 const[reason,R]=useState(''),[busy,B]=useState(false),[error,E]=useState(''),[preview,P]=useState<Obj|null>(null),[checked,C]=useState(false),[readVersion,V]=useState(0);
 useEffect(()=>{let active=true;P(null);C(false);E('');api('/events/'+event.id+'/deletion-preview').then(v=>{if(active)P(v);}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[event.id,readVersion]);
 async function remove(){if(!preview)return;B(true);E('');try{await api('/events/'+event.id,{reason,expected_revision:preview.revision,cascade:true,event_ids:preview.event_ids},'DELETE');await onSaved();}catch(e:any){E(e.message);P(null);C(false);}finally{B(false);}}
 return <Modal title={event.transfer_id?'删除整笔组合转移':'删除流水及关联记录'} subtitle={event.date+' '+kindNames[event.kind]} onClose={onClose} busy={busy} wide className="event-deletion-drawer">
 <fieldset disabled={busy}><Notice>删除后，下列流水一起退出账务计算，原记录和删除原因保留在修订历史中。共享分红、送转及卖出按整条记录处理，请逐项核对影响范围。</Notice>
 {preview?<><p className="deletion-count">将删除共 <b>{preview.total}</b> 条流水（含所选记录）。</p><div className="deletion-preview table-wrap"><table><thead><tr><th>日期 / 类型</th><th>组合 / 标的</th><th>数量 / 金额</th><th>影响说明</th></tr></thead><tbody>{preview.items.map((item:Obj)=>{const e=item.event;return <tr key={e.id} data-deletion-id={e.id}><td>{e.date}<small>{kindNames[e.kind]} · <span title={e.id}>{e.id.slice(0,8)}</span></small></td><td>{item.instrument_name}<small>{item.portfolio_name}</small></td><td>{e.kind==='SPLIT'?<>倍数 ×{e.factor}<small>不产生现金收支</small></>:<>{e.quantity||e.reinvest_quantity?units(e.quantity||e.reinvest_quantity)+'份':'—'}<small>{money(e.net_amount??e.gross_amount??e.amount)} {e.currency}</small></>}</td><td>{item.is_root?'所选记录':'关联记录'}{item.shared_with_other_lots&&<strong className="deletion-shared">同时涉及其他买入批次，本条也会整体删除</strong>}</td></tr>;})}</tbody></table></div>
 {preview.validation_error&&<ErrorBox error={'账务校验未通过：'+preview.validation_error}/>}
 <label className="check deletion-confirm"><input type="checkbox" checked={checked} onChange={e=>C(e.target.checked)}/>我已核对以上全部 {preview.total} 条流水及共享批次的影响</label></>:!error&&<p role="status">正在读取删除影响清单…</p>}
 <Field label="删除原因"><textarea aria-label="删除原因" rows={3} value={reason} onChange={e=>R(e.target.value)}/></Field><ErrorBox error={error}/>
 <footer className="form-actions"><Button onClick={onClose}>返回</Button><Button disabled={busy} onClick={()=>{P(null);C(false);V(v=>v+1);}}>重新读取删除清单</Button><Button primary disabled={!preview?.can_delete||!checked||!reason.trim()||busy} onClick={remove}>{busy?'删除中…':'确认删除并重算'}</Button></footer></fieldset>
 </Modal>;
}
