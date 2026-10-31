import React, {useEffect, useRef, useState} from 'react';
import {X, AlertCircle, ArrowUpRight} from 'lucide-react';
import {CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {amount, units, money, Obj, percent, signedMoney, tone} from './api';

export function Panel({title, right, children, className=''}: {title?:React.ReactNode;right?:React.ReactNode;children:React.ReactNode;className?:string}) {
 return <section className={'panel '+className}>{(title||right)&&<header className="panel-head"><h2>{title}</h2>{right}</header>}{children}</section>;
}
export function ListSection({title,right,children,className=''}: {title:React.ReactNode;right?:React.ReactNode;children:React.ReactNode;className?:string}) {
 return <section className={'list-section '+className}><header className="list-section-head"><h2>{title}</h2>{right}</header>{children}</section>;
}
export function Button({children,primary=false,small=false,className='',...props}:React.ButtonHTMLAttributes<HTMLButtonElement>&{primary?:boolean;small?:boolean}) {return <button type="button" className={'button '+(primary?'primary ':'')+(small?'small ':'')+className} {...props}>{children}</button>;}
export function Field({label,hint,children,wide=false}: {label:string;hint?:React.ReactNode;children:React.ReactNode;wide?:boolean}) {return <label className={'field '+(wide?'wide':'')}><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>;}
export function ErrorBox({error}: {error:any}) {return error?<div className="error-box" role="alert"><AlertCircle size={16}/><span>{String(error)}</span></div>:null;}
export function Notice({children}: {children:React.ReactNode}) {return <div className="notice">{children}</div>;}
export function Metric({label,value,note,positive=false}: {label:string;value:any;note?:React.ReactNode;positive?:boolean}) {return <div className="metric"><div>{label}</div><strong className={positive?tone(value):''}>{positive&&value!==null&&Number(value)>0?'+':''}{amount(value)}</strong>{note&&<small>{note}</small>}</div>;}
export function XirrBasis({performance:a}: {performance:Obj}) {return <details className="xirr-basis" data-idle-cash="excluded"><summary>年化口径：仅实际投入资金，不含未投入现金</summary><p>{a?.xirr_basis?.description||'按实际投入与回收现金流计算 XIRR，未平仓持仓以期末市值计入；按实际天数/365年化。未投入的预算及现金余额不作为投资本金。'}</p></details>;}
export function ReturnPerformance({performance:a}: {performance?:Obj|null}) {
 if(!a)return <span className="muted">-</span>;
 return <><div className="transaction-return-main"><b className={tone(a.return_on_cost)}>累计 {percent(a.return_on_cost,true)}</b><span className="transaction-return-amount">· {signedMoney(a.profit)} CNY</span></div><small className="transaction-return-secondary" title={a.xirr_status}>年化（XIRR） <span className={tone(a.xirr)}>{percent(a.xirr,true)}</span>{' · '+(a.closed?'已平仓':'持有中')}{a.provisional?' · 待核对':''}{a.xirr==null&&a.xirr_status?' · '+a.xirr_status:''}</small></>;
}
export function Empty({title,children,action}: {title:string;children?:React.ReactNode;action?:React.ReactNode}) {return <div className="empty"><div className="empty-mark"><ArrowUpRight size={24}/></div><h2>{title}</h2>{children&&<p>{children}</p>}{action}</div>;}
export function Chips({values}: {values:any[]}) {return <span className="chips">{values.filter(Boolean).map((t,i)=><span key={String(t)+i}>{String(t)}</span>)}</span>;}
export function Modal({title,subtitle,children,onClose,wide=false,busy=false,className=''}: {title:string;subtitle?:string;children:React.ReactNode;onClose:()=>void;wide?:boolean;busy?:boolean;className?:string}) {
 const ref=useRef<HTMLElement>(null);
 const busyRef=useRef(busy),closeRef=useRef(onClose);busyRef.current=busy;closeRef.current=onClose;
 useEffect(()=>{const old=document.activeElement as HTMLElement;const overflow=document.body.style.overflow;document.body.style.overflow='hidden';const timer=setTimeout(()=>{const dialog=ref.current;if(dialog&&!dialog.contains(document.activeElement))dialog.querySelector<HTMLElement>('input,select,textarea,button')?.focus();},40);
 const key=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!busyRef.current)closeRef.current();if(e.key==='Tab'){const nodes=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')||[]);if(!nodes.length)return;if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes[nodes.length-1].focus();}else if(!e.shiftKey&&document.activeElement===nodes[nodes.length-1]){e.preventDefault();nodes[0].focus();}}};window.addEventListener('keydown',key);
 return()=>{clearTimeout(timer);document.body.style.overflow=overflow;window.removeEventListener('keydown',key);old?.focus({preventScroll:true});};},[]);
 return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose();}}><section ref={ref} className={'drawer '+(wide?'wide-drawer':'')+' '+className} role="dialog" aria-modal="true" aria-label={title}><header className="drawer-head"><div>{subtitle&&<small>{subtitle}</small>}<h2>{title}</h2></div><button className="icon-button" onClick={onClose} disabled={busy} aria-label="关闭"><X size={20}/></button></header>{children}</section></div>;
}
const stamp=(d:string)=>Date.parse(d+'T12:00:00Z');
const dateLabel=(v:any)=>new Date(Number(v)).toISOString().slice(0,10);
export function PerformanceChart({curve,benchmark,target}: {curve:Obj[];benchmark?:Obj|null;target?:Obj|null}) {
 const refs=new Map<string,any>((benchmark?.curve||[]).map((r:Obj)=>[r.date,r.return]));
 const rows=curve.filter(r=>r.portfolio_return!==null||refs.get(r.date)!=null||r.target_return!=null).map(r=>({x:stamp(r.date),['portfolio_'+r.segment]:r.portfolio_return===null?null:Number(r.portfolio_return)*100,benchmark:refs.get(r.date)==null?null:Number(refs.get(r.date))*100,target:r.target_return==null?null:Number(r.target_return)*100}));
 const segments=Array.from(new Set(curve.map(r=>r.segment)));
 if(!rows.length)return <Empty title="还没有可计算的收益曲线">记录买入或期初资产后，补齐估值数据。</Empty>;
 return <div className="chart-wrap" aria-label="组合、基准与目标累计收益率曲线" data-target-annual={target?.annual_rate} data-target-start={target?.anchor_date} data-target-end={target?.end_date} data-target-return={target?.return}><ResponsiveContainer width="100%" height={300} minWidth={0}><LineChart data={rows} margin={{top:16,right:22,bottom:4,left:2}} accessibilityLayer><CartesianGrid stroke="var(--line)" vertical={false}/><XAxis dataKey="x" type="number" scale="time" domain={['dataMin','dataMax']} tickFormatter={v=>dateLabel(v).slice(5)} minTickGap={40} tick={{fontSize:11}} axisLine={false} tickLine={false}/><YAxis width={52} tickFormatter={v=>money(v,1)+'%'} tick={{fontSize:11}} axisLine={false} tickLine={false}/><Tooltip labelFormatter={v=>dateLabel(v)} formatter={(v:any,name:any)=>[money(v,2)+'%',String(name).startsWith('portfolio_')?'累计收益率':name==='target'?'目标路径（年化'+percent(target?.annual_rate)+'）':benchmark?.instrument?.name||'基准']} contentStyle={{background:'var(--panel)',border:'1px solid var(--line)',borderRadius:8,fontSize:12}}/>{segments.map(segment=><Line key={segment} type="linear" dataKey={"portfolio_"+segment} stroke="var(--blue)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false}/>)}{benchmark&&<Line type="linear" dataKey="benchmark" stroke="var(--muted)" strokeDasharray="4 4" dot={false} isAnimationActive={false} connectNulls={false}/>}<Line className="goal-line" type="linear" dataKey="target" stroke="#a67b38" strokeWidth={2} strokeDasharray="7 4" dot={false} isAnimationActive={false} connectNulls={false}/></LineChart></ResponsiveContainer></div>;
}
export function PriceChart({rows,trades,selected,onSelect,currency,kind}: {rows:Obj[];trades:Obj[];selected:string;onSelect:(id:string)=>void;currency:string;kind:string}) {
 const [openDate,OpenDate]=useState('');
 const data=rows.map(r=>({x:stamp(r.date),date:r.date,price:Number(r.price)})).filter(r=>Number.isFinite(r.price)&&r.price>0).sort((a,b)=>a.x-b.x);
 const byDate=new Map(data.map(r=>[r.date,r]));const groups=new Map<string,Obj>();
 trades.forEach((t,n)=>{const date=t.trade_date||t.date,quote=byDate.get(date);if(!quote)return;if(!groups.has(date))groups.set(date,{...quote,trades:[],numbers:[]});groups.get(date)!.trades.push(t);groups.get(date)!.numbers.push(n+1);});
 const missing=trades.filter(t=>!byDate.has(t.trade_date||t.date));const open=groups.get(openDate);
 useEffect(()=>{OpenDate('');},[rows,kind]);
 if(!data.length)return <Empty title="尚无价格曲线">同步行情成功后，图上显示当日收盘位置的买卖标记。</Empty>;
 const vals=data.map(r=>r.price),min=Math.min(...vals),max=Math.max(...vals),pad=(max-min)*.13||max*.03;
 return <><div className="chart-wrap price-chart" aria-label="当前组合标的价格趋势和买卖标记"><ResponsiveContainer width="100%" height={320} minWidth={0}><LineChart data={data} margin={{top:28,right:26,bottom:6,left:0}} accessibilityLayer>
 <CartesianGrid stroke="var(--line)" vertical={false}/><XAxis dataKey="x" type="number" scale="time" domain={['dataMin','dataMax']} tickFormatter={v=>dateLabel(v).slice(5)} tick={{fontSize:11}} minTickGap={42} axisLine={false} tickLine={false}/><YAxis width={60} domain={[Math.max(0,min-pad),max+pad]} tickFormatter={v=>money(v,kind==='total_return'?1:3)} tick={{fontSize:11}} axisLine={false} tickLine={false}/>
 <Tooltip labelFormatter={v=>dateLabel(v)} formatter={(v:any)=>[money(v,6),kind==='raw'?'收盘价 / 单位净值 · '+currency:kind==='total_return'?'含息指数':'复权价格参考']} contentStyle={{background:'var(--panel)',border:'1px solid var(--line)',borderRadius:8,fontSize:12}}/>
 <Line type="monotoneX" dataKey="price" stroke="var(--blue)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false}/>
 {Array.from(groups.values()).map(g=>{const chosen=g.trades.some((t:Obj)=>t.id===selected),only=g.trades[0],buy=only.kind!=='SELL';return <ReferenceDot key={g.date} x={g.x} y={g.price} ifOverflow="extendDomain" shape={(point:any)=><g role="button" tabIndex={0} aria-label={g.trades.length>1?g.date+' 共'+g.trades.length+'笔买卖':(buy?'买入':'卖出')+' '+g.date+' '+units(only.quantity)+'份'} className="trade-dot" data-plot-date={g.date} data-plot-price={g.price} data-event-ids={g.trades.map((t:Obj)=>t.id).join(',')}
 onClick={()=>{if(g.trades.length>1)OpenDate(g.date);else{OpenDate('');onSelect(only.id);}}} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();if(g.trades.length>1)OpenDate(g.date);else onSelect(only.id);}}}>
 <title>{g.date+' · 曲线点位 '+money(g.price,6)+'；'+g.trades.map((t:Obj)=>(t.kind==='SELL'?'卖出':t.kind==='REINVEST'?'再投':'买入')+' '+units(t.quantity)+'份，实际单价 '+money(t.price,6)+' '+currency).join('；')}</title>
 <circle cx={point.cx} cy={point.cy} r={16} fill="transparent"/>{chosen&&<circle cx={point.cx} cy={point.cy} r={11} fill="none" stroke="var(--blue)" strokeWidth={1.5}/>}
 <path d={buy?`M${point.cx},${point.cy} l-5,8 h10 Z`:`M${point.cx},${point.cy} l-5,-8 h10 Z`} fill={buy?'var(--red)':'var(--teal)'}/><circle className="marker-anchor" cx={point.cx} cy={point.cy} r={2.5} fill={buy?'var(--red)':'var(--teal)'}/>
 <text x={point.cx} y={point.cy-13} fontSize={11} textAnchor="middle" fill={buy?'var(--red)':'var(--teal)'}>{g.trades.length>1?g.trades.length+'笔':g.numbers[0]}</text></g>}/>;})}
 </LineChart></ResponsiveContainer>{open&&<div className="marker-details" role="dialog" aria-label="同日交易详情"><header><b>{open.date} · {open.trades.length}笔交易</b><button className="icon-button" aria-label="关闭同日交易详情" onClick={()=>OpenDate('')}><X size={15}/></button></header><small>当日曲线点位 {money(open.price,6)}</small>{open.trades.map((t:Obj)=><button key={t.id} className="marker-trade" onClick={()=>{onSelect(t.id);OpenDate('');}}>{t.kind==='SELL'?'卖出':t.kind==='REINVEST'?'再投':'买入'} {units(t.quantity)}份 · 实际单价{money(t.price,6)} {currency}<small>{t.id.slice(0,8)} · 点击定位流水</small></button>)}</div>}</div>
 {missing.length>0&&<p className="muted small">{missing.length}笔交易缺少当日行情，暂未标在曲线上；可重新同步数据。成交记录完整保留在下表。</p>}</>;
}
