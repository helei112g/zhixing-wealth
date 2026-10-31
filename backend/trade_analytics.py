"""Explicit purchase-lot matching and actual dated cash-flow returns.

Portfolio accounting keeps its weighted average cost. This projection attributes
actual purchase cash, sale cash and confirmed dividends to the selected lots.
It never persists a market-dependent profit or substitutes an index return.
"""
import math
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from .common import ZERO, ONE, DomainError, day, dec, ratio, today
from .ledger import Ledger, Market


XIRR_BASIS = {
    'method': 'XIRR', 'day_count': 'ACT/365', 'base_currency': 'CNY', 'includes_idle_cash': False,
    'description': '仅按实际投入与回收现金流计算，不含未投入现金。买入含费税为支出，卖出净回款和净分红为回收；未平仓持仓以期末市值计入，按实际天数/365年化。',
}


def target_path(history, portfolio):
    """Compound an annual target over exactly the plotted comparison interval."""
    if not history: return None, []
    anchor = max(day(portfolio['start_date']), day(history[0]['date']) - timedelta(days=1))
    rate = dec(portfolio.get('target_annual_pct', '5')) / 100
    points = []
    for row in history:
        elapsed = max(0, (day(row['date']) - anchor).days)
        value = Decimal(str(math.expm1(math.log1p(float(rate)) * elapsed / 365)))
        points.append(value)
    return {'annual_rate': rate, 'anchor_date': str(anchor), 'return': points[-1],
            'end_date': history[-1]['date']}, points


def annual_summary(portfolio, performance, as_of):
    target = dec(portfolio.get('target_annual_pct', '5')) / 100
    dates = [f['date'] for f in performance['cash_flows'] if dec(f['amount']) != 0]
    start, end = min(dates, default=portfolio['start_date']), max(dates, default=str(as_of))
    rate = performance['xirr']
    return {'return': rate, 'target': target, 'gap': rate - target if rate is not None else None,
            'start_date': start, 'end_date': end, 'days': (day(end) - day(start)).days,
            'status': performance['xirr_status'], 'xirr_basis': XIRR_BASIS, 'provisional': performance['provisional'] or performance['pending_settlement']}


def xirr(flows):
    """Isolate exponential-polynomial roots in log(1+r), including tangent roots.

    ACT/365; coalesce same-day cash flows, find derivative critical points before
    bisection. Multiple roots and rates outside the documented range stay N/A.
    """
    by_date=defaultdict(lambda:ZERO)
    for d,v in flows:by_date[str(d)]+=dec(v)
    rows=sorted((d,v) for d,v in by_date.items() if v)
    if len(rows)<2:return {'rate':None,'status':'同日或缺少跨日现金流，不计算年化'}
    if not any(v<0 for _,v in rows) or not any(v>0 for _,v in rows):return {'rate':None,'status':'须同时有投入和回收现金流'}
    start=day(rows[0][0]);terms=[((day(d)-start).days/365,float(v)) for d,v in rows]
    scale=max(abs(c) for _,c in terms);terms=[(t,c/scale) for t,c in terms]
    def value(ts,y):
        es=[-t*y for t,_ in ts];m=max(es)
        vals=[c*math.exp(e-m) for e,(_,c) in zip(es,ts)]
        result=math.fsum(vals);limit=math.fsum(abs(v) for v in vals)*1e-13
        return 0.0 if abs(result)<=limit else result
    def roots(ts,lo=-30.,hi=30.):
        if len(ts)<2:return []
        t0=ts[0][0];ts=[(t-t0,c) for t,c in ts]
        derivative=[(t,-t*c) for t,c in ts[1:]]
        points=[lo]+roots(derivative,lo,hi)+[hi];result=[]
        for a,b in zip(points,points[1:]):
            fa,fb=value(ts,a),value(ts,b)
            if fa==0:result.append(a)
            if fb==0:result.append(b)
            if fa*fb>=0:continue
            for _ in range(100):
                mid=(a+b)/2;fm=value(ts,mid)
                if fm==0 or b-a<1e-12:a=b=mid;break
                if fa*fm<0:b=mid
                else:a=mid;fa=fm
            result.append((a+b)/2)
        out=[]
        for y in sorted(result):
            if not out or abs(y-out[-1])>1e-7:out.append(y)
        return out
    found=roots(terms)
    if len(found)!=1:return {'rate':None,'status':'现金流存在多个年化解，暂不展示' if found else '无可核验年化解或超出计算范围'}
    return {'rate':Decimal(str(math.expm1(found[0]))),'status':'ACT/365 · 实际日期现金流'}


def dividend_segments(event,market,end):
    """Early DRIP spends a dividend receivable, not a later CNY cash receipt.

    Match the funded portion at reinvestment FX so its positive/negative flows
    cancel. Only the unpaid remainder follows receipt/current valuation FX.
    """
    net=dec(event['net_amount']);used=ZERO;parts=[]
    if event['kind']=='REINVEST' and event['reinvest_date']<event['pay_date'] and event['reinvest_date']<=end:
        used=dec(event.get('reinvest_gross_amount') or dec(event['reinvest_quantity'])*dec(event['reinvest_price']))+dec(event['fee'])
        parts.append({'date':event['reinvest_date'],'native':used,'amount':used*dec(event['reinvest_fx_rate']),'pending':False})
    remainder=net-used
    if remainder>0 or not parts:
        pay=event['pay_date'];pending=pay>end and remainder>0
        rate=market.fx(event['currency'],end) if pending else {'rate':event['pay_fx_rate']}
        amount=ZERO if remainder==0 else remainder*dec(rate['rate']) if rate else None
        parts.append({'date':pay,'native':remainder,'amount':amount,'pending':pending})
    return parts


def dividend_entitlement(state, portfolio, event):
    """The same registry-day lots for profit attribution and deletion planning."""
    ledger = Ledger(state, portfolio)
    inst = ledger.instruments[event['instrument_id']]
    notices = [n for n in state['corporate_actions'] if n.get('kind') == 'dividend_notice']
    notice = next((n for n in notices if n['id'] == event.get('source_dividend_id')), None)
    notice = notice or next((n for n in notices if n['instrument_id'] == inst['id'] and n['ex_date'] == event['date']), None)
    cutoff = event.get('record_date') or (notice or {}).get('record_date')
    if inst['market'] in ('HK', 'US') or not cutoff:
        cutoff = (day(event['date']) - timedelta(days=1)).isoformat()
    for op in ledger.ops:
        if op[0] > cutoff: break
        ledger.apply(op)
    owned = [r for r in ledger.position(inst['id'])['lots'] if r['quantity'] > 0
             and (inst['market'] != 'FUND' or r['available_on'] <= cutoff)]
    return cutoff, owned


def lot_projection(state,portfolio,as_of=None):
    end=str(as_of or today());ledger=Ledger(state,portfolio);market=ledger.market
    source={e['id']:e for e in ledger.events};lots={};sales={};legacy=False
    # Replay exactly the accounting operation order, including confirmation and splits.
    for op in ledger.ops:
        d,seq,order,eid,kind,e=op
        if d>end:break
        if kind in ('SELL','REDEEM_PENDING'):
            pos=ledger.position(e['instrument_id']);before={r['event_id']:r['quantity'] for r in pos['lots']}
            ledger.apply(op);after={r['event_id']:r['quantity'] for r in pos['lots']}
            sale={'event':source[eid],'parts':[],'legacy':not bool(e.get('sell_lots')),'pending':kind=='REDEEM_PENDING'}
            qty=dec(e['quantity']);net=(dec(e['gross_amount'])-dec(e['fee'])-dec(e['tax'])) if kind=='SELL' else dec(e['amount'])
            pay_date=e.get('payment_date') or d
            valuation_rate=ledger.market.fx(e['currency'],end) if pay_date>end else {'rate':e['fx_rate']}
            proceeds=net*dec(valuation_rate['rate']) if valuation_rate else None
            for bid,was in before.items():
                used=was-after.get(bid,ZERO)
                if used<=0:continue
                lot=lots[bid];fraction=used/lot['remaining'];cost=lot['remaining_cost']*fraction
                part={'buy_event_id':bid,'buy_date':lot['date'],'sell_event_id':eid,'quantity':used,'cost':cost,'proceeds':proceeds*used/qty if proceeds is not None else None,
                      'date':d,'sequence':e['sequence'],'pay_date':e.get('payment_date') or d,'fraction_start':lot['disposed_fraction'],
                      'fraction_end':lot['disposed_fraction']+(ONE-lot['disposed_fraction'])*fraction,'dividends':[]}
                lot['disposed_fraction']=part['fraction_end'];lot['remaining']-=used;lot['remaining_cost']-=cost
                lot['parts'].append(part);sale['parts'].append(part)
            sales[eid]=sale;legacy|=sale['legacy'];continue
        ledger.apply(op)
        if kind in ('BUY','OPENING_POSITION','REINVEST_BUY','REINVEST_EARLY'):
            reinvest=kind.startswith('REINVEST');qty=dec(e['reinvest_quantity'] if reinvest else e['quantity'])
            gross=dec(e.get('reinvest_gross_amount') or qty*dec(e['reinvest_price'])) if reinvest else dec(e['gross_amount'])
            cost=(gross+dec(e['fee'])+(ZERO if reinvest else dec(e['tax'])))*dec(e['reinvest_fx_rate'] if reinvest else e['fx_rate'])
            payment=(e['reinvest_date'] if reinvest else e.get('payment_date') or d)
            lots[eid]={'event':source[eid],'date':d,'payment_date':payment,'remaining':qty,'remaining_cost':cost,
                       'original_cost':cost,'disposed_fraction':ZERO,'parts':[],'dividends':[],'ambiguous':False}
        elif kind in ('SPLIT','ADJUSTMENT'):
            pos=ledger.position(e['instrument_id'])
            for row in pos['lots']:
                if row['event_id'] in lots:
                    lot=lots[row['event_id']];lot['remaining']=row['quantity']
                    if kind=='ADJUSTMENT':lot['ambiguous']=True
    snap=ledger.snapshot(end);holdings={h['instrument_id']:h for h in snap['holdings']}
    # Entitlement belongs to registry-day owners, including sales before ex/payment.
    unallocated=[]
    for event in ledger.events:
        is_div=event['kind'] in ('DIVIDEND','REINVEST')
        if not is_div or event['date']>end:continue
        if not event.get('instrument_id'):
            unallocated.append('另记的组合级费税未归属具体买入批次');continue
        cutoff, owned = dividend_entitlement(state, portfolio, event)
        total=sum((r['quantity'] for r in owned),ZERO)
        if total==0:
            unallocated.append('存在无法匹配权益批次的分红或费用，请核对登记日及历史记录');continue
        segments=dividend_segments(event,market,end);net=dec(event['net_amount'])
        for row in owned:
            lot=lots.get(row['event_id'])
            if not lot:continue
            def sold_before(p):return p['date']<cutoff or p['date']==cutoff and (is_div or p['sequence']<=event['sequence'])
            prefix=max((p['fraction_end'] for p in lot['parts'] if sold_before(p)),default=ZERO)
            alive=ONE-prefix
            if alive<=0:continue
            def items(fraction):
                weight=row['quantity']/total*fraction/alive
                return [{'date':part['date'],'amount':part['amount']*weight if part['amount'] is not None else None,
                    'event_id':event['id'],'kind':event['kind'],'pending':part['pending'],
                    'entitlement_weight':weight*part['native']/net if net>0 else weight} for part in segments]
            for part in lot['parts']:
                overlap=max(ZERO,part['fraction_end']-max(prefix,part['fraction_start']))
                if overlap:part['dividends'].extend(items(overlap))
            remaining_share=max(ZERO,ONE-max(prefix,lot['disposed_fraction']))
            if remaining_share:lot['dividends'].extend(items(remaining_share))
    # Later withheld tax must follow the original distribution, never a new owner.
    for event in ledger.events:
        if event['kind'] not in ('FEE','TAX') or event['date']>end:continue
        ref=source.get(event.get('related_event_id'));targets=[]
        if ref and ref['id'] in lots and not (ref['kind']=='REINVEST' and event['kind']=='TAX'):
            lot=lots[ref['id']]
            targets=[(p['dividends'],p['fraction_end']-p['fraction_start']) for p in lot['parts']]
            targets.append((lot['dividends'],ONE-lot['disposed_fraction']))
        elif ref and ref['id'] in sales:
            parts=sales[ref['id']]['parts'];total=sum((p['quantity'] for p in parts),ZERO)
            targets=[(p['dividends'],p['quantity']/total) for p in parts] if total else []
        elif ref and ref['kind'] in ('DIVIDEND','REINVEST'):
            for lot in lots.values():
                for container in [lot['dividends']]+[p['dividends'] for p in lot['parts']]:
                    weight=sum((v['entitlement_weight'] for v in container if v['event_id']==ref['id']),ZERO)
                    if weight:targets.append((container,weight))
        if not targets:
            unallocated.append('存在未关联的独立费税，未计入逐笔盈利；请更正费用并关联原交易或分红');continue
        amount=-dec(event['amount'])*dec(event['fx_rate'])
        for container,weight in targets:
            if weight:container.append({'date':event['date'],'amount':amount*weight,'event_id':event['id'],'kind':event['kind'],'pending':False})
    metrics={}
    def is_dividend_income(v):
        original=source.get(v.get('event_id'),{})
        return v['kind'] in ('DIVIDEND','REINVEST') or source.get(original.get('related_event_id'),{}).get('kind') in ('DIVIDEND','REINVEST')
    def part_profit(p):return (p['proceeds'] or ZERO)-p['cost']+sum((v['amount'] for v in p['dividends'] if v['amount'] is not None),ZERO)
    def part_flows(p):
        lot=lots[p['buy_event_id']]
        return [(lot['payment_date'],-p['cost'])]+([(min(p['pay_date'],end),p['proceeds'])] if p['proceeds'] is not None else [])+[(min(v['date'],end),v['amount']) for v in p['dividends'] if v['amount'] is not None]
    for lot in lots.values():
        for part in lot['parts']:
            part['profit']=part_profit(part) if not holdings.get(lot['event']['instrument_id'],{}).get('unconfirmed_split') and part['proceeds'] is not None and all(v['amount'] is not None for v in part['dividends']) else None
    def provisional(inst_id):return any(e['instrument_id']==inst_id and e['kind'] in ('DIVIDEND','REINVEST') and (not e.get('tax_known',True) or not e.get('ex_date_known',True)) for e in ledger.events if e['date']<=end)
    for bid,lot in lots.items():
        h=holdings.get(lot['event']['instrument_id']);remaining=lot['remaining'];closed=remaining==0
        value=ZERO if closed else remaining*h['price']*dec(h['fx']['rate']) if h and h['price'] is not None and h['fx'] else None
        realized=sum((part_profit(p) for p in lot['parts']),ZERO)
        income=lot['dividends']+[v for p in lot['parts'] for v in p['dividends']]
        complete=not (h or {}).get('unconfirmed_split') and all(v['amount'] is not None for v in income) and all(p['proceeds'] is not None for p in lot['parts'])
        pending=any(v['pending'] for v in income) or any(p['pay_date']>end for p in lot['parts'])
        dividends_net=sum((v['amount'] for v in income if is_dividend_income(v) and v['amount'] is not None),ZERO)
        income_total=sum((v['amount'] for v in income if v['amount'] is not None),ZERO)
        profit=value+sum((p['proceeds'] for p in lot['parts'] if p['proceeds'] is not None),ZERO)+income_total-lot['original_cost'] if value is not None and complete else None
        flows=[(lot['payment_date'],-lot['original_cost'])]+[(min(p['pay_date'],end),p['proceeds']) for p in lot['parts'] if p['proceeds'] is not None]+[(min(v['date'],end),v['amount']) for v in income if v['amount'] is not None]
        if value is not None and value: flows.append((end,value))
        annual={'rate':None,'status':'企业行动后现金流需核对'} if lot['ambiguous'] else xirr(flows) if complete and value is not None else {'rate':None,'status':'缺少估值或现金流'}
        if pending: annual['status']+='；含未到账应收估值'
        metrics[bid]={'xirr':annual['rate'],'xirr_status':annual['status'],'xirr_basis':XIRR_BASIS,'cash_flows':[{'date':d,'amount':v} for d,v in sorted(flows)],'profit':profit,'return_on_cost':ratio(profit,lot['original_cost']) if profit is not None else None,
            'cost':lot['original_cost'],'market_value':value,'remaining_quantity':remaining,'realized_profit':None if (h or {}).get('unconfirmed_split') else realized,
            'floating_profit':profit-realized if profit is not None else None,'dividends_net':dividends_net,
            'closed':closed,'closed_date':max((p['date'] for p in lot['parts']),default=None) if closed else None,
            'as_of':end,'provisional':provisional(lot['event']['instrument_id']),'parts':lot['parts'],'currency':'CNY','estimated':bool(h and (h.get('quote') or {}).get('fallback')),'unallocated_notes':list(set(unallocated)),
            'pending_settlement':pending,'valuation_frozen':closed and not pending,
            'status':'送转/拆股份额待核对' if (h or {}).get('unconfirmed_split') else '人工企业行动，逐笔结果需核对' if lot['ambiguous'] else '已平仓，回款或应得分红待结清' if closed and pending else '已平仓，停止行情估值' if closed else '持有中，随最新估值更新'}
    for sid,sale in sales.items():
        parts=sale['parts'];flows=[f for p in parts for f in part_flows(p)]
        incomes=[v for p in parts for v in p['dividends']]
        complete=not holdings.get(sale['event']['instrument_id'],{}).get('unconfirmed_split') and all(v['amount'] is not None for v in incomes) and all(p['proceeds'] is not None for p in parts)
        unsettled=any(p['pay_date']>end for p in parts) or any(v['pending'] for v in incomes)
        ambiguous=any(lots[p['buy_event_id']]['ambiguous'] for p in parts)
        annual={'rate':None,'status':'企业行动后现金流需核对'} if ambiguous else {'rate':None,'status':'缺少应收估值汇率'} if not complete else {'rate':None,'status':'赎回金额待确认'} if sale['pending'] else xirr(flows)
        cost=sum((p['cost'] for p in parts),ZERO);profit=sum((part_profit(p) for p in parts),ZERO) if complete else None
        if unsettled: annual['status']+='；含未到账应收估值'
        if any(lots[p['buy_event_id']]['event']['kind']=='OPENING_POSITION' for p in parts):annual['status']+='；期初份额仅计算记账起收益'
        metrics[sid]={'profit':profit,'return_on_cost':ratio(profit,cost) if profit is not None else None,'cost':cost,'market_value':ZERO,
            'remaining_quantity':ZERO,'realized_profit':profit,'floating_profit':ZERO,'dividends_net':sum((v['amount'] for v in incomes if is_dividend_income(v) and v['amount'] is not None),ZERO),
            'provisional':provisional(sale['event']['instrument_id']),'closed':True,'closed_date':sale['event']['date'],'as_of':sale['event']['date'],'parts':parts,'currency':'CNY',
            'unallocated_notes':list(set(unallocated)),'pending_settlement':unsettled or sale['pending'],'valuation_frozen':not unsettled and not sale['pending'],'xirr':annual['rate'],'xirr_status':annual['status'],'xirr_basis':XIRR_BASIS,'cash_flows':[{'date':d,'amount':v} for d,v in sorted(flows)],
            'status':'送转/拆股份额待核对' if holdings.get(sale['event']['instrument_id'],{}).get('unconfirmed_split') else '旧流水按FIFO推导关联，请核对' if sale['legacy'] else '按所选买入批次结算，行情不再影响结果'}
    return metrics


def instrument_projection(state,portfolio,holdings,as_of=None,*,market=None):
    """Instrument money-weighted returns, including all related fees and receivables.

    Reinvestment has both dividend income and purchase cash, so internal cash
    cancels. Unsettled receivables are valued today rather than inventing a
    future payment. Closed, settled investments keep their original flow dates.
    """
    end=str(as_of or today());market=market if market is not None else Market(state);result={}
    events=[e for e in state['events'] if e['portfolio_id']==portfolio['id'] and e['date']<=end]
    for h in holdings:
        iid=h['instrument_id'];flows=[];cost=ZERO;dividends_net=ZERO;complete=h['value_base'] is not None;pending=False
        proceeds=ZERO;other_income=ZERO;extra_costs=ZERO;dividends_complete=True
        dividend_parts={'DIVIDEND':ZERO,'REINVEST':ZERO}
        dividend_parts_complete={'DIVIDEND':True,'REINVEST':True}
        for e in events:
            if e.get('instrument_id')!=iid:continue
            k=e['kind'];fx=dec(e['fx_rate'])
            def add(at,amount):
                if at<=end:flows.append((at,amount))
            def receipt(at,amount,locked):
                nonlocal complete,pending
                if at>end:
                    pending=True;r=market.fx(e['currency'],end)
                    if r:
                        value=amount*dec(r['rate']);add(end,value);return value
                    complete=False;return ZERO
                value=amount*locked;add(at,value);return value
            if k in ('BUY','OPENING_POSITION'):
                paid=(dec(e['gross_amount'])+dec(e['fee'])+dec(e['tax']))*fx
                cost+=paid;add(e.get('payment_date') or e['date'],-paid)
            elif k=='SELL':proceeds+=receipt(e.get('payment_date') or e['date'],dec(e['gross_amount'])-dec(e['fee'])-dec(e['tax']),fx)
            elif k in ('DIVIDEND','REINVEST'):
                for part in dividend_segments(e,market,end):
                    pending|=part['pending']
                    if part['amount'] is None:
                        complete=False;dividends_complete=False;dividend_parts_complete[k]=False
                    else:
                        dividends_net+=part['amount'];dividend_parts[k]+=part['amount'];add(min(part['date'],end),part['amount'])
                if k=='REINVEST' and e['reinvest_date']<=end:
                    paid=(dec(e.get('reinvest_gross_amount') or dec(e['reinvest_quantity'])*dec(e['reinvest_price']))+dec(e['fee']))*dec(e['reinvest_fx_rate'])
                    cost+=paid;add(e['reinvest_date'],-paid)
            elif k in ('FEE','TAX'):
                fee=dec(e['amount'])*fx;add(e['date'],-fee)
                dividend=next((d for d in events if d['id']==e.get('related_event_id') and d['kind'] in ('DIVIDEND','REINVEST')),None)
                if dividend:
                    dividends_net-=fee;dividend_parts[dividend['kind']]-=fee
                else:extra_costs+=fee
            elif k=='INTEREST':
                value=dec(e['amount'])*fx;other_income+=value;add(e['date'],value)
            elif k in ('SUBSCRIBE_PENDING','REDEEM_PENDING','OPENING_RECEIVABLE'):
                # Unresolved pending transactions cannot establish investment cash flows.
                complete=False
        if h['value_base'] is not None and h['value_base']!=0:flows.append((end,h['value_base']))
        profit=sum((v for _,v in flows),ZERO) if complete else None
        annual=xirr(flows) if complete else {'rate':None,'status':'有未确认流水或缺少估值，暂不年化'}
        own=[e for e in events if e.get('instrument_id')==iid]
        provisional=any(e['kind'] in ('DIVIDEND','REINVEST') and (not e.get('tax_known',True) or not e.get('ex_date_known',True)) for e in own)
        if any(e['kind']=='OPENING_POSITION' for e in own):annual['status']+='；期初份额仅自记账起计算'
        price_net=dec(h['value_base'])+proceeds-cost-extra_costs if complete else None
        breakdown=profit_breakdown(profit,price_net,dividends_net if dividends_complete else None,other_income,
            cash_dividends_net=dividend_parts['DIVIDEND'] if dividend_parts_complete['DIVIDEND'] else None,
            reinvested_dividends_net=dividend_parts['REINVEST'] if dividend_parts_complete['REINVEST'] else None,
            market_value=h['value_base'],sale_proceeds=proceeds if complete else None,buy_cost=cost,extra_costs=extra_costs)
        result[iid]={'profit':profit,'return_on_cost':ratio(profit,cost) if profit is not None else None,'cost':cost,'profit_breakdown':breakdown,
            'dividends_net':dividends_net,'xirr':annual['rate'],'xirr_status':annual['status'],'xirr_basis':XIRR_BASIS,'provisional':provisional,
            'pending_settlement':pending,'closed':h['quantity']==0,'as_of':end,'cash_flows':[{'date':d,'amount':v} for d,v in sorted(flows)]}
    return result


def investment_summary(state, portfolio, snapshot, performances, as_of):
    """Aggregate investment cash flows, never averages of instrument XIRRs.

    Idle budget is excluded. Closed investment cash flows remain part of the
    portfolio's history. Each open position contributes its terminal value once.
    """
    rows = list(performances.values())
    flows = [(f['date'], dec(f['amount'])) for r in rows for f in r['cash_flows']]
    complete = all(r['profit'] is not None for r in rows)
    cost = sum((dec(r['cost']) for r in rows), ZERO)
    extra_income=ZERO;extra_expenses=ZERO
    # Unassigned historical portfolio expenses/income have no instrument row.
    instrument_ids = {h['instrument_id'] for h in snapshot['holdings']}
    for e in state['events']:
        if e['portfolio_id'] != portfolio['id'] or e['date'] > str(as_of) or e.get('instrument_id') in instrument_ids: continue
        if e['kind'] in ('FEE', 'TAX', 'INTEREST'):
            value = dec(e['amount']) * dec(e['fx_rate'])
            flows.append((e['date'], value if e['kind'] == 'INTEREST' else -value))
            if e['kind']=='INTEREST':extra_income+=value
            else:extra_expenses+=value
        elif e['kind'] in ('SUBSCRIBE_PENDING', 'REDEEM_PENDING', 'OPENING_RECEIVABLE'):
            complete = False
    profit = sum((value for _, value in flows), ZERO) if complete else None
    annual = xirr(flows) if complete and cost > 0 else {'rate': None, 'status': '尚无可核实买入本金，不计算年化' if complete else '有未确认流水或缺少估值，暂不年化'}
    if any(e['portfolio_id'] == portfolio['id'] and e['kind'] == 'OPENING_POSITION' and e['date'] <= str(as_of) for e in state['events']):
        annual['status'] += '；含期初份额，仅计算记账以来'
    planned = dec(snapshot['capital']) if snapshot.get('funding_mode') == 'budget' else dec(portfolio['planned_amount'])
    available = snapshot['available_cash']
    invested = planned - dec(available) if available is not None else None
    def combined(key):
        values=[r['profit_breakdown'][key] for r in rows]
        return sum((dec(v) for v in values),ZERO) if all(v is not None for v in values) else None
    price_net=combined('price_net');dividends=combined('dividends_net');other=combined('other_income')+extra_income
    if price_net is not None:price_net-=extra_expenses
    breakdown=profit_breakdown(profit,price_net,dividends,other,market_value=snapshot['securities_total'],
        cash_dividends_net=combined('cash_dividends_net'),reinvested_dividends_net=combined('reinvested_dividends_net'),
        sale_proceeds=combined('sale_proceeds'),buy_cost=cost,extra_costs=combined('extra_costs')+extra_expenses)
    return {
        'planned_amount': planned, 'invested_amount': invested,
        'invested_weight': ratio(invested, planned) if invested is not None else None,
        'available_amount': available, 'market_value': snapshot['securities_total'],
        'profit': profit, 'return_on_cost': ratio(profit, cost) if profit is not None else None,
        'cost': cost, 'profit_breakdown':breakdown, 'xirr': annual['rate'], 'xirr_status': annual['status'], 'xirr_basis': XIRR_BASIS,
        'provisional': any(r['provisional'] for r in rows),
        'pending_settlement': any(r['pending_settlement'] for r in rows),
        'cash_flows': [{'date': d, 'amount': v} for d, v in sorted(flows)],
    }


def profit_breakdown(total,price_net,dividends_net,other_income,**facts):
    """Reconcile investment profit without relabeling interest as price movement.

    Price is CNY price/FX profit after trading costs; dividend-related costs are
    deducted from dividends exactly once. Values remain unrounded for the API.
    """
    values=[price_net,dividends_net,other_income]
    complete=total is not None and all(v is not None for v in values)
    # The UI can disclose the rare one-cent difference caused only by display rounding.
    cents=lambda v:dec(v).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    rounding=cents(total)-sum((cents(v) for v in values),ZERO) if complete else None
    components=[price_net,facts.get('cash_dividends_net'),facts.get('reinvested_dividends_net'),other_income]
    component_rounding=cents(total)-sum((cents(v) for v in components),ZERO) if complete and all(v is not None for v in components) else None
    return {'total':total,'price_net':price_net,'dividends_net':dividends_net,'other_income':other_income,
            'rounding_adjustment':rounding,'component_rounding_adjustment':component_rounding,'complete':complete,
            'price_basis':'价格及汇兑收益，已扣买卖费税与非分红费用；含已实现及未实现收益',**facts}
