import {useEffect, useState} from 'react';
import {money, Obj} from './api';
import {Button, Panel} from './components';

const PAGE_SIZE = 10;

export function SettingsPriceCache({boot,syncing,sync}:Obj){
 const [page,setPage]=useState(1);
 const total=boot.instruments.length;
 const pages=Math.max(1,Math.ceil(total/PAGE_SIZE));
 const current=Math.min(page,pages);
 useEffect(()=>{setPage(p=>Math.min(p,pages));},[pages]);
 const rows=boot.instruments.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE);
 const statuses=new Map<string,Obj>(boot.price_status.map((r:Obj)=>[r.instrument_id,r]));
 return <Panel title="行情与净值缓存" right={<Button small disabled={syncing} onClick={()=>sync(undefined,true)}>同步全部历史</Button>}>
  {total?<>
   <div className="table-wrap"><table className="price-cache-table"><thead><tr><th>标的</th><th>最新价格 / 净值 / 指数点位</th><th>数据日期 / 来源</th><th>历史点数</th><th>操作</th></tr></thead><tbody>{rows.map((i:Obj)=>{const r=statuses.get(i.id);return <tr key={i.id} data-cache-instrument={i.id}><td>{i.name}{i.builtin&&<span className="pill">官方全收益</span>}<small>{i.code}.{i.market} · {i.currency}</small></td><td>{money(r?.latest?.price,4)}</td><td>{r?.latest?.date||'待同步'}<small>{r?.latest?.source||'—'}</small></td><td>{r?.count||0}</td><td><button className="text-button" disabled={syncing} onClick={()=>sync(i.id,true,'manual_instrument')}>重新同步</button></td></tr>;})}</tbody></table></div>
   <nav className="cache-pagination" aria-label="行情缓存分页"><span className="muted small" aria-live="polite">共 {total} 个标的 · 第 {current} / {pages} 页 · 每页 {PAGE_SIZE} 条</span><div className="inline-actions"><Button small disabled={current===1} onClick={()=>setPage(current-1)}>上一页</Button><Button small disabled={current===pages} onClick={()=>setPage(current+1)}>下一页</Button></div></nav>
  </>:<p className="muted">录入交易后，按已启用的全局策略补齐行情。</p>}
  <p className="muted small">价格与净值通过数据源同步；自动更新范围、频率和时刻由上方数据更新策略统一管理。记账后按已开启且非“仅手动”的策略补齐历史。自动更新失败项可在公共数据任务中精确重试；手动历史同步结果见更新记录，旧缓存保留。休市与基金净值晚公布时，价格日期可能不同。</p>
 </Panel>;
}
