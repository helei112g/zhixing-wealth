import {useEffect, useState} from 'react';
import {ArrowLeft, Bell, Edit3, Link2, Plus} from 'lucide-react';
import {Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {api, money, Obj, percent, query, signedMoney} from './api';
import {Button, Empty, ErrorBox, Field, Modal, Notice, Panel} from './components';
import './family.css';
import {FamilyLedger, SnapshotEditor} from './family_ledger_ui';
import {FamilyAnalysis} from './family_analysis_ui';
import {FamilyOverview} from './family_overview_ui';

const categories:Obj={cash:'现金',fixed:'固定资产',investment:'投资理财',receivable:'应收款（别人欠我的钱）',insurance:'保单现金价值',debt:'负债'};
const colors:Obj={cash:'#76aaa4',fixed:'#8d9fbd',investment:'#b5a5c7',receivable:'#9ead75',insurance:'#d9b27c'};
const planNames:Obj={none:'暂不设置',once:'一次付清',annual:'按年缴费',fixed:'每月固定金额',annuity:'等额本息',equal_principal:'等额本金'};
const pick=(row:Obj,fields:string)=>Object.fromEntries(fields.split(' ').filter(k=>row[k]!==undefined).map(k=>[k,row[k]]));
const dueLabel=(r:Obj)=>r.days<0?'逾期 '+(-r.days)+' 天':r.days===0?'今日到期':r.days+' 天后';
const kindLabel:Obj={debt:'还款提醒',debt_end:'预计还清日',receivable:'预计收回日',premium:'缴保费',renewal:'保险到期 / 续保'};
const amountLabels:Obj={cash:'当前余额',fixed:'现在大约值多少',investment:'当前金额',debt:'目前还欠多少',receivable:'还有多少没收回',insurance:'保单现金价值'};


export function FamilyPage({version,today,tab,setTab,ledgerState,setLedgerState,onReload,openPortfolio}:Obj){
 const[data,S]=useState<Obj|null>(null),[catalog,C]=useState<Obj|null>(null),[error,E]=useState(''),[modal,M]=useState<Obj|null>(null);
 const {filter,selected}=ledgerState;
 const F=(filter:string)=>setLedgerState((s:Obj)=>({...s,filter})),Select=(selected:string)=>setLedgerState((s:Obj)=>({...s,selected}));
 useEffect(()=>{let active=true;Promise.all([api('/family/overview'),api('/family/catalog')]).then(([d,c])=>{if(active){S(d);C(c);E('');}}).catch(e=>E(e.message));return()=>{active=false;};},[version]);
 useEffect(()=>{if(['debt','receivable'].includes(tab)){F(tab);Select('');setTab('assets');}},[tab]);
 if(!data||!catalog)return <><ErrorBox error={error}/><p>读取资产…</p></>;
 const open=(operation:string,row?:Obj)=>M({operation,row});
 const table=(rows:Obj[])=> <div className="table-wrap"><table className="family-asset-table"><thead><tr><th>名称</th><th>金额 · CNY</th><th>说明</th><th className="family-action-cell">操作</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td><b>{r.name}</b><small>{r.linked?'来自投资组合 · 自动更新':categories[r.category]}</small></td><td><b>{money(r.value)}</b>{r.currency!=='CNY'&&<small>{money(r.household_amount)} {r.currency}</small>}</td><td>{r.category==='insurance'&&<small>{r.insured||'未填写被保险人'} · {r.coverage_type||'保险'}{r.coverage_amount!=null&&' · 保额 '+money(r.coverage_amount)+' '+r.currency}</small>}{r.category==='debt'&&<small>借款日期：{r.started_on||'未填写'}</small>}{r.payment_plan&&r.payment_plan.mode!=='none'&&<small>{planNames[r.payment_plan.mode]} · {r.payment_plan.first_due} 起 · {r.payment_plan.periods} {r.payment_plan.mode==='annual'?'年':'期'} · {r.payment_end} 结束</small>}<small>金额更新：{r.source_date||'尚未填写'}</small>{r.currency!=='CNY'&&<small>汇率 {r.fx?.rate||'未知'} · {r.fx?.date||'缺失'}</small>}</td><td className="family-action-cell"><div className="family-row-actions">{r.linked?<><button className="text-button" onClick={()=>openPortfolio(r.portfolio_id)}>查看组合</button><button className="text-button" onClick={()=>open('unlink',r)}>解除关联</button></>:<button className="text-button" onClick={()=>open('item',r)}>编辑</button>}</div></td></tr>)}</tbody></table>{!rows.length&&<Empty title="还没有记录">点击“新增记录”填写名称和金额。</Empty>}</div>;
 return <div className="family-page"><ErrorBox error={error}/><div className="family-intro"><span>资产 · 统一换算为人民币 · 截至 {data.as_of}</span><div className="inline-actions">{tab==='overview'&&<Button small onClick={()=>setTab('assets')}><Edit3 size={14}/>资产更新</Button>}{tab==='assets'&&<Button small onClick={()=>setTab('overview')}><ArrowLeft size={14}/>返回总览</Button>}<Button small onClick={()=>open('links')}><Link2 size={14}/>关联投资组合</Button><Button primary small onClick={()=>tab==='income'?open('income'):open('item',(tab==='assets'&&filter!=='all'?{category:filter}:tab==='insurance'?{category:'insurance'}:undefined))}><Plus size={14}/>新增记录</Button></div></div>
 {data.incomplete&&<Notice>{data.missing_items.join('；')}。请编辑对应记录补充金额，或在全局设置更新汇率。</Notice>}
 {data.reconciliation_todos.length>0&&<p className="muted small">组合资金有变化，请同时检查银行卡等手工现金余额，避免重复计入。</p>}
 {tab==='overview'&&<FamilyOverview version={version} today={today} bounds={catalog.bounds}/>}
 {tab==='assets'&&<FamilyLedger data={data} catalog={catalog} today={today} filter={filter} setFilter={F} selected={selected} setSelected={Select} onEdit={(r:Obj)=>open('item',r)} onUnlink={(r:Obj)=>open('unlink',r)} openPortfolio={openPortfolio} onReload={onReload}/> }
 {tab==='analysis'&&<FamilyAnalysis version={version} today={today} bounds={catalog.bounds}/>}
 {tab==='insurance'&&<><Panel title="保单与保障">{table(data.items.filter((r:Obj)=>r.category==='insurance'))}</Panel><p className="muted small">在保单中设置一次付清或按年缴费，填写金额与首次缴费日期即可自动提醒。</p></>}
 {tab==='income'&&<FamilyIncome version={version} today={today} catalog={catalog} data={data} onReload={onReload}/>}
 {modal&&(modal.operation==='snapshot'?<Modal title={modal.row.name+' · 更新余额'} onClose={()=>M(null)}><SnapshotEditor row={modal.row} catalog={catalog} today={today} onSaved={async()=>{M(null);F(modal.row.category);Select(modal.row.id);setTab('assets');await onReload();}}/></Modal>:<FamilyForm key={modal.operation+':'+(modal.row?.id||'new')} operation={modal.operation} row={modal.row} catalog={catalog} data={data} today={today} onUseExisting={(row:Obj,amount:string,date:string)=>open('snapshot',{...row,draftAmount:amount,draftDate:date})} onClose={()=>M(null)} onSaved={async()=>{M(null);await onReload();}}/>)}
 </div>;
}

const incomeNames:Obj={salary:'工资',rent:'租金',fund:'公积金',custom:'自定义'};
const incomeColors:Obj={salary:'#7b9dbc',rent:'#76aaa4',fund:'#c2a36b',custom:'#ae94be'};
function FamilyIncome({version,today,catalog,data:overview,onReload}:Obj){
 const bounds=catalog.bounds;
 const[year,Y]=useState(String(bounds.income_years[0]||today.slice(0,4))),[data,D]=useState<Obj|null>(null),[error,E]=useState(''),[month,M]=useState(''),[edit,Edit]=useState<Obj|null>(null);
 const selectedYear=bounds.income_years.includes(Number(year))?year:String(bounds.income_years[0]||today.slice(0,4));
 useEffect(()=>{let active=true;D(null);E('');api('/family/income?year='+selectedYear).then(v=>{if(active)D(v);}).catch(e=>{if(active)E(e.message);});return()=>{active=false;};},[selectedYear,version]);
 const rows=(data?.rows||[]).filter((r:Obj)=>(!bounds.income_first_date||r.month>=bounds.income_first_date.slice(0,7))&&r.month<=today.slice(0,7)).map((r:Obj)=>({...r,...Object.fromEntries(Object.keys(incomeNames).map(k=>[k,r.recorded&&r.total!==null?Number(r.groups[k]):null]))}));
 const records=(data?.records||[]).filter((r:Obj)=>r.month===month);
 return <><Panel title="每月收入 · CNY" right={<select className="income-year" aria-label="收入年份" value={selectedYear} disabled={!bounds.income_years.length} onChange={e=>{Y(e.target.value);M('');}}>{(bounds.income_years.length?bounds.income_years:[Number(today.slice(0,4))]).map((y:number)=><option key={y}>{y}</option>)}</select>}>
 <ErrorBox error={error}/><p className="muted small">点击柱状图或月份查看、修改该月明细。收入按工资、租金、公积金和自定义分类，不自动增加现金余额。</p>
 {!data?<p>读取收入…</p>:<>{data.summary?<div className="income-summary"><div className="income-year-total"><span>{selectedYear} 年已记录收入累计 · CNY</span><strong>{money(data.summary.total)}</strong><small>已记录 {data.summary.recorded_months} 个月；未记录不等于零。</small>{data.summary.missing_count>0&&<small>有 {data.summary.missing_count} 条缺少汇率；已知收入 {money(data.summary.known_total)} CNY，占比暂不计算。</small>}</div><div className="income-composition" aria-label="年度收入组成">{Object.keys(incomeNames).map(k=><div key={k}><span><i style={{background:incomeColors[k]}}/>{incomeNames[k]}</span><b>{money(data.summary.groups[k].amount)} CNY</b><span>{percent(data.summary.groups[k].share)}</span><div className="income-share-track"><i style={{width:(data.summary.groups[k].share===null?0:Number(data.summary.groups[k].share)*100)+'%',background:incomeColors[k]}}/></div></div>)}</div></div>:<Notice>当前服务尚未提供收入累计统计，请重启本环境服务后刷新。已有收入明细仍可查看和编辑。</Notice>}<div className="chart-legend income-legend">{Object.keys(incomeNames).map(k=><span key={k}><i style={{background:incomeColors[k],height:8}}/>{incomeNames[k]}</span>)}</div>
 <div className="family-history-chart income-stacked-chart"><ResponsiveContainer width="100%" height={300}><ComposedChart data={rows}><CartesianGrid vertical={false} strokeDasharray="3 3"/><XAxis dataKey="month" tickFormatter={v=>v.slice(5)+'月'}/><YAxis tickFormatter={v=>v<10000?money(v,0):money(v/10000,1)+'万'}/><Tooltip formatter={(v:any,name:any)=>[money(v)+' CNY',name]}/>{Object.keys(incomeNames).map(k=><Bar key={k} dataKey={k} name={incomeNames[k]} stackId="income" onClick={(entry:any)=>{if(entry?.payload?.month)M(entry.payload.month);}} fill={incomeColors[k]} maxBarSize={60} isAnimationActive={false} cursor="pointer"/>)}</ComposedChart></ResponsiveContainer></div>
 <div className="table-wrap"><table className="income-months"><thead><tr><th>月份</th>{Object.values(incomeNames).map(v=><th key={String(v)}>{String(v)}</th>)}<th>合计 · CNY</th></tr></thead><tbody>{rows.map((r:Obj)=><tr key={r.month} onClick={()=>M(r.month)}><td><button className="text-button" aria-label={'查看 '+r.month+' 收入'} onClick={()=>M(r.month)}>{r.month}</button></td>{Object.keys(incomeNames).map(k=><td key={k}>{r.recorded?money(r.groups[k]):'—'}</td>)}<td>{r.recorded?money(r.total):'未记录'}{r.recorded&&r.total===null&&<small>缺少汇率</small>}</td></tr>)}</tbody></table></div></>}
 </Panel>
 {month&&!edit&&<Modal title={month+' 收入明细'} subtitle="更正保留历史；不会自动修改现金余额。" wide onClose={()=>M('')}><ErrorBox error={error}/>{!data?<p>读取明细…</p>:!records.length?<Empty title="这个月还没有收入记录">使用页面顶部“新增记录”填写收入。</Empty>:<div className="table-wrap"><table><thead><tr><th>类别 / 名称</th><th>金额</th><th>操作</th></tr></thead><tbody>{records.map((r:Obj)=><tr key={r.id}><td>{r.name}<small>{r.date}</small></td><td>{money(r.amount)} {r.currency}{r.currency!=='CNY'&&<small>{money(r.value)} CNY · 汇率 {r.fx?.rate||'未知'} · {r.fx?.date||'缺失'}</small>}</td><td><button className="text-button" onClick={()=>Edit(r)}>编辑</button></td></tr>)}</tbody></table></div>}</Modal>}
 {edit&&<FamilyForm operation="income" row={edit} catalog={catalog} data={overview} today={today} onClose={()=>Edit(null)} onSaved={async()=>{Edit(null);await onReload();}}/>}
 </>;
}

export function NotificationSettings({version}:Obj){const[data,D]=useState<Obj|null>(null),[error,E]=useState(''),[busy,B]=useState(false),[message,M]=useState('');useEffect(()=>{let active=true;api('/notifications').then(v=>{if(active)D(v);}).catch(e=>E(e.message));return()=>{active=false;};},[version]);async function act(action:string){B(true);E('');try{const value=await api('/notifications',{action});D(value);M(value.test_scheduled?'已向系统提交测试通知，请确认是否实际看到；未据此标记已送达。':'通知状态已刷新');}catch(e:any){E(e.message);}finally{B(false);}}return <Panel title="macOS 系统通知" right={<Bell size={18}/>}><ErrorBox error={error}/>{data&&<><div className="record-line"><div><b>{data.enabled?'已启用':'尚未启用'} · {{authorized:'系统已授权',denied:'系统已拒绝',not_requested:'待授权',provisional:'临时授权',unknown:'状态未知'}[data.authorization as string]||data.authorization}</b><small>系统待发 {data.pending_count||0} 条 · 计划覆盖至 {data.coverage_until?.slice(0,16).replace('T',' ')||'暂无'} · {{dev:'开发',test:'测试',production:'本机'}[data.environment as string]||data.environment} 环境</small></div><div className="inline-actions">{data.enabled?<><Button small disabled={busy} onClick={()=>act('refresh')}>刷新通知计划</Button><Button small disabled={busy} onClick={()=>act('test')}>发送测试通知</Button><Button small disabled={busy} onClick={()=>act('disable')}>关闭系统通知</Button></>:<Button primary disabled={busy||!data.supported} onClick={()=>act('enable')}>{busy?'等待系统授权…':'启用 macOS 通知'}</Button>}</div></div>{data.error&&<ErrorBox error={data.error}/>}<p className="muted small">提前 7 天、1 天及当天 09:00（上海时区）提醒；还款、保费和续保按计划提醒；分红需要核对时另行通知。通知默认隐藏账户名称与金额。{data.note}</p>{message&&<Notice>{message}</Notice>}</>}</Panel>}

function FamilyForm({operation,row,catalog,data,today,onClose,onSaved,onUseExisting}:Obj){
 const editing=Boolean(row?.id), initialPlan=row?.payment_plan||{mode:'none'};
 const[form,F]=useState<Obj>(operation==='item'?{category:row?.category||'cash',effective_on:today,currency:row?.currency||'CNY',name:row?.name||'',household_amount:row?.household_amount??'',started_on:row?.started_on||today,insured:row?.insured||'',coverage_type:row?.coverage_type||'',coverage_amount:row?.coverage_amount??'',policy_end:row?.policy_end||'',plan_mode:initialPlan.mode,plan_amount:initialPlan.amount||'',plan_date:initialPlan.first_due||today,plan_periods:initialPlan.periods||1,plan_rate:initialPlan.annual_rate||'0'}:operation==='income'?{kind:row?.kind||'salary',name:row?.name||'',month:row?.month||today.slice(0,7),currency:row?.currency||'CNY',amount:row?.amount||''}:{portfolio_ids:[]});
 const[preview,P]=useState<Obj|null>(null),[error,E]=useState(''),[busy,B]=useState(false),[revision,R]=useState(catalog.revision);
 const set=(k:string,v:any)=>{F(s=>({...s,[k]:v,...(k==='category'?{plan_mode:'none',...(v==='income'?{kind:'salary',month:today.slice(0,7),amount:''}:{})}:{})}));P(null);};
 const input=(k:string,label:string,type='text',hint?:string)=> <Field label={label} hint={hint}><input type={type} value={form[k]??''} min={type==='number'?'0':undefined} step={type==='number'?'any':undefined} onChange={e=>set(k,e.target.value)}/></Field>;
 const select=(k:string,label:string,options:Obj,disabled=false)=> <Field label={label}><select value={form[k]} disabled={disabled} onChange={e=>set(k,e.target.value)}>{Object.entries(options).map(([k,v])=><option key={k} value={k}>{String(v)}</option>)}</select></Field>;
 const linked=new Set(data.links.map((l:Obj)=>l.portfolio_id)),available=catalog.portfolios.filter((p:Obj)=>!linked.has(p.id)&&!p.archived);
 const income=operation==='income'||form.category==='income';
 const duplicate=!editing&&operation==='item'&&['cash','fixed','investment','receivable'].includes(form.category)?data.items.find((r:Obj)=>!r.linked&&r.category===form.category&&r.currency===form.currency&&r.name===form.name.trim()):null;
 async function submit(commit=false){B(true);E('');try{
   let op=operation,payload:Obj={};
   if(income){op='income';payload=pick(form,'kind name month currency amount');if(!payload.kind)payload.kind='salary';if(!payload.month)payload.month=today.slice(0,7);if(row?.date&&payload.month===row.month)payload.date=row.date;if(payload.amount===''||payload.amount==null)throw Error('请填写收入金额');}
   else if(operation==='item'){
     if(form.household_amount===''&&form.category!=='insurance')throw Error('请填写金额，金额为零时请填 0');
     payload={...pick(form,'name category currency household_amount'),effective_on:['cash','fixed','investment','receivable'].includes(form.category)?form.effective_on:today};
     if(form.category==='debt')payload.started_on=form.started_on;
     if(form.category==='insurance')Object.assign(payload,pick(form,'insured coverage_type coverage_amount policy_end'));
     if(['debt','insurance'].includes(form.category)){
       const mode=form.plan_mode;payload.payment_plan={mode};
       if(mode!=='none'){payload.payment_plan={mode,first_due:form.plan_date,periods:Number(form.plan_periods)};
         if(mode==='annual'||mode==='fixed'||(mode==='once'&&form.category==='insurance'))payload.payment_plan.amount=form.plan_amount;
         if(['annuity','equal_principal'].includes(mode))payload.payment_plan.annual_rate=form.plan_rate;
       }
     }
   }else if(operation==='links')payload={portfolio_ids:form.portfolio_ids};
   else if(operation==='unlink')payload={link_ids:[row.link_id||row.id]};
   const command={operation:op,payload,record_id:editing&&['item','income'].includes(op)?row.id:null,expected_revision:preview?.revision??revision};
   const result=await api('/family/'+(commit?'commands':'preview'),command);
   if(commit){await api('/family/overview');await onSaved();}else P(result);
 }catch(e:any){E(e.message);if(e.status===409){P(null);R((await api('/family/catalog')).revision);}}finally{B(false);}}
 const labels:Obj={cash:'现金形式 / 渠道',fixed:'固定资产名称',investment:'理财名称',receivable:'谁欠我 / 款项名称',debt:'借款名称',insurance:'保单名称'};
 return <Modal title={operation==='links'?'关联投资组合':operation==='unlink'?'解除组合关联':editing?(operation==='item'?(form.category==='debt'?'更新负债':'更新信息'):'编辑记录'):'新增记录'} subtitle="保存后更新金额，历史记录保留。" wide onClose={onClose} busy={busy}><div className="form-grid family-form">
 {(operation==='item'||operation==='income')&&<>
 {operation==='item'&&select('category','类别',{...categories,insurance:'保险',income:'收入'},editing)}
 {select('currency','币种',{CNY:'人民币 CNY',HKD:'港币 HKD',USD:'美元 USD'},editing)}
 {income?<>
 {select('kind','收入类型',{salary:'工资',rent:'租金',fund:'公积金',custom:'自定义'})}
 {form.kind==='custom'&&input('name','收入名称')}{input('month','收入月份','month')}{input('amount','收入金额（必填）','number')}
 </>:<>
 {input('name',labels[form.category])}{input('household_amount',form.category==='cash'?'现金金额（必填）':form.category==='insurance'?'保单现金价值':amountLabels[form.category]+'（必填）','number',form.category==='insurance'?'不是保额或已交保费；不清楚可留空。':undefined)}
 {['cash','fixed','investment','receivable'].includes(form.category)&&<Field label="金额日期" hint="这笔金额对应的日期。补录过去的金额会保留后来的记录。"><input type="date" value={form.effective_on} max={today} onChange={e=>set('effective_on',e.target.value)}/></Field>}
 {form.category==='debt'&&input('started_on','借款日期','date')}
 {form.category==='insurance'&&<>{input('insured','被保险人')}{input('coverage_type','保什么（可选）')}{input('coverage_amount','保额（可选）','number')}{input('policy_end','保险到期日期（可选）','date')}</>}
 {['debt','insurance'].includes(form.category)&&<>
 {select('plan_mode',form.category==='debt'?'还款方式':'保费支付方式',form.category==='debt'?{none:'暂不设置',once:'到期一次还清',fixed:'每月固定金额',annuity:'等额本息',equal_principal:'等额本金'}:{none:'暂不设置',once:'一次性付清',annual:'按年付费'})}
 {form.plan_mode!=='none'&&<>{input('plan_date',form.category==='insurance'?'首次缴费日期':'本次计划首期还款日期','date')}{form.plan_mode!=='once'&&input('plan_periods',form.plan_mode==='annual'?'缴费年数':'剩余还款月数','number')}{(form.plan_mode==='fixed'||form.plan_mode==='annual'||(form.plan_mode==='once'&&form.category==='insurance'))&&input('plan_amount',form.category==='insurance'?'每次保费':'每月还款金额','number')}{['annuity','equal_principal'].includes(form.plan_mode)&&input('plan_rate','年利率（%）','number')}<p className="muted small wide">保存后自动生成到期提醒，不会自动扣款或更改资产余额。{form.category==='debt'&&'按当前欠款和剩余期数计算；新建或调整计划请从今天或之后开始。实际金额以银行账单为准。'}</p></>}
 </>}
 </>}
 </>}
 {operation==='links'&&<div className="wide family-link-picker"><div className="family-link-toolbar"><b>选择组合，可多选</b><button className="text-button" onClick={()=>set('portfolio_ids',form.portfolio_ids.length===available.length?[]:available.map((p:Obj)=>p.id))}>全选 / 取消</button></div>{catalog.portfolios.filter((p:Obj)=>!p.archived||linked.has(p.id)).map((p:Obj)=><label className="family-link-option" key={p.id}><input type="checkbox" checked={linked.has(p.id)||form.portfolio_ids.includes(p.id)} disabled={linked.has(p.id)} onChange={e=>set('portfolio_ids',e.target.checked?[...form.portfolio_ids,p.id]:form.portfolio_ids.filter((id:string)=>id!==p.id))}/><span>{p.name}<small>{linked.has(p.id)?'已关联，可在资产列表解除':'金额随组合自动更新'}</small></span></label>)}</div>}
 {operation==='unlink'&&<p className="wide">解除“{row.name}”的关联？组合与交易保留，今天起不再计入资产。</p>}
 </div>{duplicate&&onUseExisting&&<Notice>已有项目“{duplicate.name}”，可以直接补录或更新余额。<Button small onClick={()=>onUseExisting(duplicate,form.household_amount,form.effective_on)}>更新此项目</Button></Notice>}<ErrorBox error={error}/>{preview&&<div className="family-preview">{income?<b>收入记录已检查，保存后计入 {form.month||today.slice(0,7)}，不会自动改变现金余额。</b>:<><b>保存后的资产 · CNY</b><div>资产 {money(preview.summary.provisional_assets)}　欠款 {money(preview.summary.total_debt)}　净资产 {money(preview.summary.provisional_net)}</div></>}</div>}
 <>{preview?.result?.payment_preview&&<p className="notice">预计首期付款：{money(preview.result.payment_preview.first.amount)} {form.currency} · {preview.result.payment_preview.first.due_date}；共 {preview.result.payment_preview.periods} 期，预计总付款 {money(preview.result.payment_preview.total)} {form.currency}，最后一期 {preview.result.payment_preview.last_due}。</p>}</><footer className="form-actions"><Button onClick={onClose} disabled={busy}>返回</Button><Button primary disabled={busy||(operation==='links'&&!form.portfolio_ids.length)} onClick={()=>submit(Boolean(preview))}>{preview?'确认保存':'检查并继续'}</Button></footer></Modal>;
}
