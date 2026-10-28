import {useEffect,useRef,useState} from 'react';
import {api,Obj,query} from './api';
import {Button,Field} from './components';

// Rates belong to a currency + economic date, never to the last request to finish.
export function useDatedRate(currency:string,date:string,enabled:boolean,initial?:Obj):Obj{
 const original=useRef(initial),first=useRef(true),generation=useRef(0);
 const [attempt,Attempt]=useState(0),[state,State]=useState<Obj>({key:'',rate:'',loading:false});
 const key=enabled&&date?currency+':'+date:'';
 useEffect(()=>{const ticket=++generation.current;const current=()=>ticket===generation.current;
  if(!key){State({key,rate:'',loading:false});return;}
  const saved=original.current,preserve=first.current&&!attempt&&saved?.currency===currency&&saved?.date===date&&Number(saved?.rate)>0;
  first.current=false;
  if(preserve){State({key,rate:currency==='CNY'?'1':saved.rate,sourceDate:saved.sourceDate||date,type:saved.type||'manual',hint:'保留该记录已锁定汇率；修改日期或币种后自动重新获取。',loading:false});return;}
  if(currency==='CNY'){State({key,rate:'1',sourceDate:date,type:'CNY',hint:'人民币固定为1。',loading:false});return;}
  State({key,rate:'',loading:true,hint:'正在获取 '+date+' 的汇率…'});
  api('/reference-rate?'+query({currency,date})).then(v=>{if(!current())return;const r=v.reference;
   if(!r||!(Number(r.rate)>0))throw new Error('该日期之前暂无可用汇率，请更新数据后重试，或填写已核对汇率。');
   State({key,rate:r.rate,sourceDate:r.date,type:'reference',loading:false,hint:'参考 '+r.date+' · '+r.source+(r.date!==date?'（所选日期 '+date+'，使用此前最近可得记录）':'')+'；确认后锁定。'});
  }).catch(e=>{if(current())State({key,rate:'',loading:false,error:e.message,hint:'获取失败，未沿用其他日期的汇率。'});});
  return()=>{++generation.current;};
 },[key,attempt]);
 const active=state.key===key?state:{key,rate:'',loading:!!key};
 function manual(rate:string){++generation.current;State({key,rate,sourceDate:date,type:'manual',loading:false,hint:'使用已核对的手工汇率；修改日期或币种后重新获取。'});}
 return {...active,ready:!enabled||!!key&&!active.loading&&Number(active.rate)>0,manual,retry:()=>Attempt(n=>n+1)};
}

export function DatedRateField({label,currency,rate,onChange,wide=false}:Obj){return <Field label={label+' · 1 '+currency+' = x CNY'} wide={wide} hint={rate.hint}>
 <input type="number" step="any" min="0.000000001" required readOnly={currency==='CNY'} aria-busy={!!rate.loading} value={rate.rate||''} onChange={e=>{rate.manual(e.target.value);onChange();}}/>
 {rate.error&&<small role="alert" className="danger">{rate.error}</small>}
 {currency!=='CNY'&&<Button small disabled={rate.loading||!rate.key} onClick={()=>{rate.retry();onChange();}}>{rate.error?'重试获取汇率':'按日期重新获取'}</Button>}
 </Field>;}
