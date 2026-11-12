import {money, Obj, percent} from './api';
import './index_valuation.css';

export const primaryValuations = [
 {key:'pe', short:'PE TTM', label:'PE 市盈率'},
 {key:'pb', short:'PB', label:'PB 市净率'},
 {key:'dividend_yield', short:'股息率', label:'DY 股息率'},
 {key:'ps', short:'PS TTM', label:'PS 市销率'},
] as const;
export const valuationGroups = [...primaryValuations, {key:'unclassified', label:'未分类'}] as const;
export type ValuationGroup = 'all' | typeof valuationGroups[number]['key'];

const bands = [
 {key:'low', label:'低估', range:'≤20%'},
 {key:'fair_low', label:'合理偏低', range:'>20% 至 50%'},
 {key:'fair_high', label:'合理偏高', range:'>50% 且 <80%'},
 {key:'high', label:'高估', range:'≥80%'},
] as const;
const windows = [5,10] as const;

export function valuationLabel(metric: string) {
 return valuationGroups.find(group=>group.key===metric)?.label ?? '未分类';
}

function currentValue(metric: string, value: string | null | undefined) {
 return metric==='dividend_yield' ? percent(value) : money(value);
}

export function ValuationLegend() {
 return <div className="index-valuation-legend" aria-label="估值分级">
  {bands.map(band=><span key={band.key} className={'valuation-grade '+band.key}>{band.label} {band.range}</span>)}
 </div>;
}

export function IndexValuationPosition({position, valuations}: Obj) {
 if (!position) return <span className="muted">待选主估值指标</span>;
 const metric = primaryValuations.find(item=>item.key===position.metric);
 const observation = valuations[position.metric];
 const points = windows.map(years=>({years,...position.windows[years]}));
 const available = points.some(point=>point.rank!=null);

 return <div className="index-valuation-position" data-primary-valuation={position.metric}>
  <div className="index-primary-value"><span>{metric?.short}</span><b>{currentValue(position.metric,observation?.current)}</b></div>
  {available&&<div className="index-valuation-track" aria-hidden="true">
   {points.map(({years,rank})=>rank!=null&&<i key={years} className={'valuation-marker window-'+years}
    style={{left:(Number(rank)*100)+'%'}}/>)}</div>}
  <div className="index-valuation-windows">{points.map(({years,rank,grade})=>{
   const band = bands.find(item=>item.key===grade);
   return <div key={years} data-valuation-window={years} title={rank==null?'暂无满足窗口要求的有效估值历史':undefined}>
    <span className={'index-window-symbol window-'+years} aria-hidden="true"/>
    <span>{years}年</span><b>{percent(rank)}</b>
    {band&&<span className={'valuation-grade '+band.key}>{band.label}</span>}
   </div>;
  })}</div>
 </div>;
}

export function IndexValuationCell({metric, value}: Obj) {
 const ranks = windows.map(years=>value?.windows?.[years]?.rank);
 return <td data-valuation-metric={metric}>
  <b>{currentValue(metric,value?.current)}</b>
  <small title={`原始指标分位 · 5 年 ${percent(ranks[0])} · 10 年 ${percent(ranks[1])}`}>
   {ranks.map(rank=>rank==null?'—':money(Number(rank)*100,0)+'%').join(' / ')}
  </small>
  {value?.date&&<small><time dateTime={value.date}>{value.date}</time></small>}
 </td>;
}
