import {useEffect, useState} from 'react';
import {ChevronRight, Edit3, LoaderCircle, Plus} from 'lucide-react';
import {api, money, Obj, percent, signedMoney, tone} from './api';
import {Button, Empty, ErrorBox, Panel} from './components';
import './portfolio_overview.css';

function portfolioHref(id: string) {
 return '/?page=portfolio&portfolio=' + encodeURIComponent(id);
}

function ReturnMetric({label, value, note}: Obj) {
 return <div className="portfolio-return-metric">
  <dt>{label}</dt>
  <dd><div><strong className={tone(value.amount)}>{signedMoney(value.amount)}</strong>
   <span className={tone(value.rate)}>{percent(value.rate, true)}</span></div>
   {note && <small>{note}</small>}</dd>
 </div>;
}

export function PortfolioOverview({boot, version, onCreate, onEdit, onOpen}: Obj) {
 const [data, setData] = useState<Obj | null>(null);
 const [error, setError] = useState('');
 const [retry, setRetry] = useState(0);
 const {revision, today} = boot;

 useEffect(() => {
  if (!boot.portfolios.length) {setData(null); return;}
  let active = true;
  setError('');
  api('/portfolios/overview').then(value => {if (active) setData(value);})
   .catch(e => {if (active) setError(e.message);});
  return () => {active = false;};
 }, [revision, today, version, retry, boot.portfolios.length]);

 if (!boot.portfolios.length) return <Panel><Empty title="从第一个组合开始"
  action={<Button primary onClick={onCreate}><Plus size={16}/>创建第一个组合</Button>}>
  设置组合预算后，即可记录交易并查看组合收益。
 </Empty></Panel>;

 const summary = data?.revision === revision && data?.date === today ? data : null;
 const byId = new Map<string, Obj>((summary?.portfolios ?? []).map((row: Obj) => [row.id, row]));
 const day = summary?.latest_trading_day;
 return <>
  {error && <div className="portfolio-overview-error"><ErrorBox error={error}/>
   <Button onClick={() => setRetry(retry + 1)}>重新读取汇总</Button></div>}
  {summary ? <section className="panel portfolio-overview-summary" aria-label="全部组合汇总">
   <div className="portfolio-overview-total">
    <span>全部组合总资产 · CNY</span>
    <strong>{money(summary.total_assets)}</strong>
    <small>{summary.portfolios.length} 个组合</small>
    {(summary.incomplete || summary.estimated) && <small role="status">{summary.incomplete?'部分数据待补齐':'部分收益含暂估'}</small>}
   </div>
   <div className="portfolio-overview-returns">
    <dl>
     <ReturnMetric label="累计收益" value={summary.cumulative}/>
     <ReturnMetric label="年化收益（XIRR）" value={summary.annualized}
      note={summary.annualized.rate == null ? summary.annualized.status : '一年折算金额 · 不含未投入现金'}/>
     <ReturnMetric label="今年涨跌" value={summary.year_to_date} note={summary.year_to_date.estimated && '含暂估'}/>
     <ReturnMetric label="本月涨跌" value={summary.month_to_date} note={summary.month_to_date.estimated && '含暂估'}/>
     <ReturnMetric label="最近交易日涨跌" value={day}
      note={day.end_date ? day.end_date + (day.estimated ? ' · 含暂估' : '') : '暂无可用行情日期'}/>
    </dl>
    <p>累计及期间收益率以对应期末累计买入成本为分母。年化金额为累计买入成本 × XIRR。最近交易日以已同步行情为准。</p>
   </div>
  </section> : !error && <div className="portfolio-overview-loading" role="status"><LoaderCircle size={18} className="spin"/>正在汇总组合收益…</div>}
  <Panel title="投资组合" className="portfolio-overview-list">
   <ul>
    {boot.portfolios.map((portfolio: Obj) => {
     const row = byId.get(portfolio.id);
     return <li key={portfolio.id} data-portfolio-id={portfolio.id}>
      <div className="portfolio-overview-identity">
       <a href={portfolioHref(portfolio.id)} onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onOpen(portfolio.id);
       }}>{portfolio.name}<ChevronRight size={16}/></a>
       <small>运行 {portfolio.running_days} 天 · 始于 {portfolio.start_date}{portfolio.archived && ' · 已归档'}</small>
      </div>
      <div className="portfolio-overview-row-value"><small>总资产 · CNY</small><b>{money(row?.total_assets)}</b></div>
      <div className="portfolio-overview-row-value"><small>累计收益 · CNY</small>
       <b className={tone(row?.cumulative.amount)}>{signedMoney(row?.cumulative.amount)} <span>{percent(row?.cumulative.rate, true)}</span></b></div>
      <Button small aria-label={'编辑组合 '+portfolio.name} onClick={() => onEdit(portfolio)}><Edit3 size={14}/>编辑</Button>
     </li>;
    })}
   </ul>
  </Panel>
 </>;
}
