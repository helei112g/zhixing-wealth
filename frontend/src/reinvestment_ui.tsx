import {useEffect,useState} from 'react';
import {api,Obj,query} from './api';
import {Button,Field} from './components';

// Auto values are derived from a complete request key, never copied into the
// manual draft. Changing inputs immediately hides the previous estimate.
export function useReinvestmentEstimate({enabled,portfolioId,instrumentId,date,amount,currency,initialQuantity}:Obj):Obj{
 const [manual,Manual]=useState(Number(initialQuantity)>0),[draft,Draft]=useState(String(initialQuantity||''));
 const [attempt,Attempt]=useState(0),[state,State]=useState<Obj>({key:''});
 const valid=enabled&&instrumentId&&date&&Number.isFinite(Number(amount))&&Number(amount)>0;
 const key=valid?JSON.stringify([portfolioId,instrumentId,date,amount,currency,attempt]):'';
 useEffect(()=>{
  if(manual||!key)return;
  let active=true;
  State({key,loading:true});
  const timer=setTimeout(()=>{
   api('/portfolios/'+encodeURIComponent(portfolioId)+'/instruments/'+encodeURIComponent(instrumentId)+'/reinvestment-estimate?'+query({date,gross_amount:amount}))
    .then(value=>{if(active){if(value.currency!==currency)throw new Error('交易币种与基金币种不一致，请核对后再估算。');State({key,value});}})
    .catch(error=>{if(active)State({key,error:'单位净值读取失败：'+error.message+'。请重新读取或填写实际确认份额。'});});
  },180);
  return()=>{active=false;clearTimeout(timer);};
 },[key,manual]);
 const current=key&&state.key===key?state:null,loading=!manual&&!!key&&(!current||!!current.loading);
 const value=current?.value,quantity=manual?draft:value?.quantity||'';
 let hint=manual?'保留已填写份额；日期、金额变化不会覆盖，请按确认单核对。':!instrumentId?'选择当前组合已有基金后可自动估算；也可填写实际确认份额。':!date?'请先选择再投净值归属日。':!valid?'填写大于0的再投总价后自动估算。':loading?'正在读取所选日单位净值并估算份额…':current?.error||value?.note;
 if(!manual&&value?.reference)hint=`${value.reference.date} 单位净值 ${value.reference.nav} ${value.currency} · ${value.reference.source}。${value.note}`;
 return {quantity,manual,loading,hint,ready:!enabled||!loading&&Number(quantity)>0,
  edit:(quantity:string)=>{Manual(true);Draft(quantity);},
  retry:()=>{State({key:''});Manual(false);Attempt(n=>n+1);},canRetry:!!valid};
}

export function ReinvestmentQuantityField({estimate,onChange}:Obj){
 return <Field label="再投实际确认份额" hint={estimate.hint}>
  <input type="number" min="0.000000000001" step="any" required aria-busy={estimate.loading}
   value={estimate.quantity} onChange={e=>{estimate.edit(e.target.value);onChange();}}/>
  <Button small disabled={!estimate.canRetry||estimate.loading} onClick={()=>{estimate.retry();onChange();}}>
   {estimate.manual?'按净值重新估算':'重新读取净值'}
  </Button>
 </Field>;
}
