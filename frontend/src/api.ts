export type Obj = Record<string, any>;
let csrf = '';
export class ApiError extends Error {constructor(message:string, public status:number){super(message);}}
export async function api(path: string, body?: unknown, method?: string): Promise<any> {
  const response = await fetch('/api' + path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    credentials: 'same-origin',
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json().catch(() => ({error: '本地服务返回了无法读取的响应'}));
  if (!response.ok) throw new ApiError(value.error || value.detail || '操作未完成，请重试',response.status);
  if (value.csrf_token) csrf = value.csrf_token;
  return value;
}
export const money = (v: any, digits = 2) => v === null || v === undefined || v === '' ? '—' : (Number(v) === 0 ? 0 : Number(v)).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
export const units = (v: any) => v === null || v === undefined || v === '' ? '—' : Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 12});
export const amount = (v: any) => money(v, Number(v) % 1 === 0 ? 0 : 2);
export const percent = (v: any, signed = false) => v === null || v === undefined ? '—' : (signed && Number(v) > 0 ? '+' : '') + money(Number(v) * 100, 2) + '%';
export const signedMoney = (v: any) => v === null || v === undefined ? '—' : (Number(v) > 0 ? '+' : '') + money(v);
// Preserve native-currency dividend precision so gross - tax = net stays readable.
export function exactMoney(v:any):string {if(v===null||v===undefined||v==='')return '—';const s=String(v),m=s.match(/^(-?)(\d+)(?:\.(\d+))?$/);if(!m)return money(v);const fraction=(m[3]||'').replace(/0+$/,'').padEnd(2,'0');return m[1]+m[2].replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+fraction;}
export const tone = (v: any) => v === null || v === undefined || Number(v) === 0 ? '' : Number(v) > 0 ? 'up' : 'down';
export const kindNames: Record<string,string> = {DEPOSIT:'入金',WITHDRAW:'出金',BUY:'买入',SELL:'卖出',DIVIDEND:'现金分红',REINVEST:'红利再投',FX:'换汇',FEE:'费用',TAX:'税费',INTEREST:'利息/其他收入',OPENING_CASH:'期初现金',OPENING_POSITION:'期初持仓',OPENING_RECEIVABLE:'期初应收',SUBSCRIBE_PENDING:'基金申购待确认',REDEEM_PENDING:'基金赎回待确认',SPLIT:'送转股/拆股/合股',ADJUSTMENT:'企业行动人工调整'};
export function query(values: Obj): string { const q = new URLSearchParams(); Object.entries(values).forEach(([k,v])=>{if(v!==''&&v!==null&&v!==undefined)q.set(k,String(v));}); return q.toString(); }
export function downloadText(text: string, name: string) {const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);}

// Decimal-string addition for editing historical fee+tax without binary rounding.
export function decimalSum(...values:any[]):string{const rows=values.map(v=>String(v??'0')).map(v=>{const m=v.match(/^([+-]?)(\d*)(?:\.(\d*))?$/);return m?{negative:m[1]==='-',whole:m[2]||'0',fraction:m[3]||''}:{negative:false,whole:'0',fraction:''};});const places=Math.max(0,...rows.map(r=>r.fraction.length));let total=0n;for(const r of rows){const n=BigInt(r.whole+r.fraction.padEnd(places,'0'));total+=r.negative?-n:n;}const negative=total<0n;let digits=(negative?-total:total).toString().padStart(places+1,'0');let result=places?digits.slice(0,-places)+'.'+digits.slice(-places):digits;result=result.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');return(negative?'-':'')+result;}
export const marketKey=(i?:Obj)=>i?.market==='SH'||i?.market==='SZ'?'CN':i?.market==='HK'?'HK':i?.market==='US'?'US':i?.market==='FUND'?'FUND':'OTHER';
export const marketNames:Record<string,string>={CN:'A股',HK:'港股',US:'美股',FUND:'场外基金',CASH:'资金',OTHER:'其他'};

export const recordMarketKey=(event:Obj,instrument?:Obj)=>['DEPOSIT','OPENING_CASH','WITHDRAW','FX'].includes(event.kind)?'CASH':marketKey(instrument);

export const regionOptions=['A股','美股','港股','海外其他','混合','未分类'];
export const regionValue=(v?:string)=>v==='国内'?'A股':v==='海外'?'海外其他':v||'未分类';

// Old job logs remain intact, but must not recommend the retired price-import path.
export const syncErrorText=(v:any)=>String(v||'').replace('可稍后更新或导入CSV','请稍后重试同步');

// Presentation only: all API/accounting values remain precise decimal strings.
export function readableMoney(value:any,signed=false):string {
 if(value===null||value===undefined||value==='')return '—';
 const n=Number(value);if(!Number.isFinite(n))return '—';
 const magnitude=Math.abs(n),scale=magnitude>=99999950?100000000:magnitude>=10000?10000:1;
 const rounded=Number((n/scale).toFixed(2));
 return (signed&&rounded>0?'+':'')+money(rounded)+(scale===100000000?' 亿':scale===10000?' 万':'');
}
