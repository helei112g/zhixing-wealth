import {useEffect,useRef,useState} from 'react';
import {api,Obj} from './api';
import {Button,ErrorBox,Field,Modal} from './components';
import './holding_analysis.css';

export function HoldingLabelFields({value,onChange,options,automatic='待确认',suggestions=[]}:Obj){
 const scope=value.scope||automatic,choices=options?.directions?.[scope]||[],id='holding-detail-'+useRef(Math.random().toString(36).slice(2)).current;
 function changeScope(next:string){const allowed=options?.directions?.[next||automatic]||[];onChange({...value,scope:next,...(!allowed.includes(value.direction)?{direction:'',detail:''}:{})});}
 return <div className="holding-label-fields"><Field label="投资范围" hint={value.scope?'手工指定，后续同步不会覆盖':automatic==='待确认'?'投资范围待确认，基金不按上市地或币种推断':'自动识别 · '+automatic}><div className="label-scope-input"><select aria-label="投资范围" value={value.scope} onChange={e=>changeScope(e.target.value)}><option value="">自动识别 · {automatic}</option>{(options?.scopes||[]).map((s:string)=><option key={s}>{s}</option>)}</select>{value.scope&&<button type="button" className="text-button" onClick={()=>changeScope('')}>恢复自动</button>}</div></Field>
 <div className="field"><span>配置方向 <small>可选 · 单选</small></span><div className="holding-direction-options" role="group" aria-label="配置方向">{choices.map((d:string)=><button type="button" key={d} aria-pressed={value.direction===d} onClick={()=>onChange({...value,direction:value.direction===d?'':d,...(value.direction===d?{detail:''}:{})})}>{d}</button>)}{!choices.length&&<span className="muted small">确认投资范围后可选择配置方向</span>}</div></div>
 <Field label="细分标签" hint="可选，输入后自动复用；每个标的只使用一条主路径"><input aria-label="细分标签" list={id} maxLength={40} disabled={!value.direction} value={value.detail} placeholder={value.direction?'例如：医药':'先选择配置方向'} onChange={e=>onChange({...value,detail:e.target.value})}/><datalist id={id}>{suggestions.filter((r:Obj)=>r.scope===scope&&r.direction===value.direction).map((r:Obj)=><option key={r.detail} value={r.detail}/>)}</datalist></Field>
 <div className="holding-label-preview"><span>展示路径</span><b>{[scope,value.direction,value.detail.trim()].filter(Boolean).join(' / ')}</b></div></div>;
}

export function HoldingLabelEditor({pid,holding,onClose,onSaved}:Obj){
 const[data,Data]=useState<Obj|null>(null),[f,F]=useState<Obj|null>(null),[error,E]=useState(''),[busy,B]=useState(false),[retry,R]=useState(0),[review,Review]=useState(false);
 const active=useRef(true);useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 const base='/portfolios/'+encodeURIComponent(pid)+'/instruments/'+encodeURIComponent(holding.instrument_id)+'/labels';
 useEffect(()=>{let active=true;B(true);E('');api(base).then(v=>{if(active){Data(v);F(old=>old||{scope:v.labels.scope,direction:v.labels.direction,detail:v.labels.detail});}}).catch(e=>{if(active)E(e.message);}).finally(()=>{if(active)B(false);});return()=>{active=false;};},[base,retry]);
 async function submit(e:React.FormEvent){e.preventDefault();if(!data||!f)return;B(true);E('');try{const result=await api(base,{...f,expected_revision:data.revision},'PUT');await onSaved(result);if(active.current)onClose();}catch(e:any){if(active.current){E(e.message);Review(true);}}finally{if(active.current)B(false);}}
 return <Modal className="holding-label-drawer" title={'设置标签 · '+holding.instrument.name} onClose={onClose} busy={busy}><ErrorBox error={error}/>{!f?<p className="muted">读取当前标签…</p>:<form onSubmit={submit}><fieldset disabled={busy}><HoldingLabelFields value={f} onChange={F} automatic={data?.labels.automatic_scope} options={data?.options} suggestions={data?.options.suggestions}/>{review&&<p className="muted small">当前已保存：{data?.labels.path.join(' / ')}。草稿已保留，重新读取核对后再保存。</p>}<footer className="holding-label-actions"><button type="button" className="text-button" onClick={()=>F({scope:'',direction:'',detail:''})}>清空手工标签</button><div>{review&&<Button small onClick={()=>R(n=>n+1)}>重新读取并核对</Button>}<Button small onClick={onClose}>取消</Button><button className="button primary" disabled={!data}>保存标签</button></div></footer></fieldset></form>}{!f&&error&&<Button small onClick={()=>R(n=>n+1)}>重新读取</Button>}</Modal>;
}
