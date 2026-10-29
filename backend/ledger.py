from __future__ import annotations

import bisect
import copy
from math import prod
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .trading_calendar import no_expected_session
from .common import CURRENCIES, DomainError, ONE, ZERO, currency, day, dec, ds, month_after, ratio, tags, today, wire

KINDS = {'DEPOSIT', 'WITHDRAW', 'BUY', 'SELL', 'DIVIDEND', 'REINVEST', 'FX', 'FEE', 'TAX',
         'INTEREST', 'OPENING_CASH', 'OPENING_POSITION', 'OPENING_RECEIVABLE',
         'SUBSCRIBE_PENDING', 'REDEEM_PENDING', 'SPLIT', 'ADJUSTMENT'}
TRADE_KINDS = {'BUY', 'SELL', 'OPENING_POSITION', 'REDEEM_PENDING', 'SPLIT', 'ADJUSTMENT'}


def recorded_split_factor(events,instrument_id,date):
    return prod((dec(e['factor']) for e in events if e['kind']=='SPLIT' and e.get('instrument_id')==instrument_id and e['date']==date),start=ONE)


def split_notice_records(events, action):
    """Find confirmations, or conservative legacy candidates needing review.

    A registry-day candidate is never treated as confirmed: surface a correction
    instead of offering a second split. Callers pass one portfolio's events.
    """
    records = [e for e in events if e['kind']=='SPLIT' and e.get('instrument_id')==action['instrument_id']]
    matched = [e for e in records if e.get('source_split_id')==action['id'] or e['date']==action['date']]
    if matched: return matched
    return [e for e in records if not e.get('source_split_id') and e['date']==action.get('record_date')
            and dec(e['factor'])==dec(action['factor'])]


def split_notice_confirmed(records, action):
    return bool(records) and all(e['date']==action['date'] for e in records) and recorded_split_factor(records,action['instrument_id'],action['date'])==dec(action['factor'])


def normalize_event(payload, portfolio, instruments, defaults=None):
    p = dict(payload)
    kind = str(p.get('kind', '')).upper()
    if kind not in KINDS:
        raise DomainError('未知流水类型')
    p['kind'] = kind
    p['portfolio_id'] = portfolio['id']
    d = day(p.get('date'))
    if d < day(portfolio['start_date']) or d > today():
        raise DomainError('发生日期须在组合记账起始日与今天之间')
    p['date'] = d.isoformat()
    try: p['sequence'] = int(p.get('sequence') or 0)
    except (TypeError, ValueError): raise DomainError('同日顺序必须是整数') from None
    if p['sequence'] < 0 or p['sequence'] > 1000000000:
        raise DomainError('同日顺序必须是0到10亿之间的整数')
    inst_id = p.get('instrument_id') or None
    if kind in TRADE_KINDS | {'DIVIDEND', 'REINVEST', 'SUBSCRIBE_PENDING'} and inst_id not in instruments:
        raise DomainError('请先添加该组合内的标的')
    if inst_id and inst_id not in instruments:
        raise DomainError('标的不存在')
    p['instrument_id'] = inst_id
    c = instruments[inst_id]['currency'] if inst_id else p.get('currency', 'CNY')
    p['currency'] = currency(c)
    fx_needed = kind not in {'SPLIT', 'ADJUSTMENT'}
    p['fx_rate'] = '1' if c == 'CNY' else ds(dec(p.get('fx_rate', '1' if not fx_needed else ''), '交易折算汇率', positive=True))
    p['fx_type'] = str(p.get('fx_type') or ('CNY' if c == 'CNY' else 'manual'))[:40]
    p['fx_source_date'] = day(p.get('fx_source_date') or d.isoformat(), '汇率源日期').isoformat()
    if day(p['fx_source_date']) > d:
        raise DomainError('交易折算汇率的源日期不能晚于该笔发生日期')
    p['reason'] = str(p.get('reason') or '')[:4000]
    p['external_ref'] = str(p.get('external_ref') or '')[:200]
    if kind in ('BUY','SELL') and p.get('transaction_costs') not in (None,''):
        p['fee']=ds(dec(p['transaction_costs'],'手续费及税费',nonnegative=True));p['tax']='0';p['costs_combined']=True
    p['fee'] = ds(dec(p.get('fee') or 0, '手续费', nonnegative=True))
    p['tax'] = ds(dec(p.get('tax') or 0, '税费', nonnegative=True))
    if p.get('costs_combined') not in (None,''):p['costs_combined']=str(p['costs_combined']).lower() in ('true','1')
    else:p.pop('costs_combined',None)
    for field in ('payment_date', 'confirmation_date', 'pay_date', 'reinvest_date', 'record_date'):
        if p.get(field):
            dt = day(p[field], field)
            if dt < day(portfolio['start_date']):
                raise DomainError('付款、确认与到账日期不能早于记账起始日')
            p[field] = dt.isoformat()
    if kind in {'OPENING_CASH', 'OPENING_POSITION', 'OPENING_RECEIVABLE'} and d != day(portfolio['start_date']):
        raise DomainError('期初资产须记在组合起始日')
    if kind in {'DEPOSIT', 'WITHDRAW', 'FEE', 'TAX', 'INTEREST', 'OPENING_CASH', 'OPENING_RECEIVABLE', 'SUBSCRIBE_PENDING', 'REDEEM_PENDING'}:
        p['amount'] = ds(dec(p.get('amount'), '实际金额', positive=True))
    if kind in ('SELL','REDEEM_PENDING') and p.get('sell_lots') is not None:
        rows=p['sell_lots']
        if not isinstance(rows,list) or not rows:raise DomainError('卖出必须选择关联的买入批次')
        chosen=[];seen=set()
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('buy_event_id'),str) or row['buy_event_id'] in seen:raise DomainError('卖出关联批次无效或重复')
            seen.add(row['buy_event_id']);chosen.append({'buy_event_id':row['buy_event_id'],'quantity':ds(dec(row.get('quantity'),'批次卖出份额',positive=True))})
        p['sell_lots']=sorted(chosen,key=lambda r:r['buy_event_id'])
        p['quantity']=ds(sum((dec(r['quantity']) for r in chosen),ZERO))
    if kind in {'BUY', 'SELL', 'OPENING_POSITION', 'REDEEM_PENDING'}:
        p['quantity'] = ds(dec(p.get('quantity'), '数量/份额', positive=True))
    if kind in {'BUY', 'SELL', 'OPENING_POSITION'}:
        if p.get('gross_amount') not in (None, '') and p.get('price') in (None, ''):
            p['price'] = ds(dec(p['gross_amount'], '成交总价（不含费税）', positive=True) / dec(p['quantity']))
        else: p['price'] = ds(dec(p.get('price'), '价格/确认净值', positive=True))
        calculated = dec(p['quantity']) * dec(p['price'])
        gross = dec(p.get('gross_amount') or calculated, '成交总额', positive=True)
        if abs(gross - calculated) > Decimal('.01'):
            raise DomainError('成交总额与数量×价格相差超过0.01，请核对')
        p['gross_amount'] = ds(gross)
        if kind == 'SELL' and gross <= dec(p['fee']) + dec(p['tax']):
            raise DomainError('卖出实收金额必须大于0')
        if kind == 'OPENING_POSITION' and (dec(p['fee']) or dec(p['tax'])):
            raise DomainError('期初公允价值不含本期新费用；历史成本另填')
    if kind in {'BUY', 'OPENING_POSITION', 'REINVEST'}:
        merged = dict(defaults or {})
        for key in ('asset_class', 'region', 'tags'):
            if key in p:
                merged[key] = p[key]
        p.update(tags(merged))
    else:
        for key in ('asset_class', 'region', 'tags'):
            p.pop(key, None)
    if kind not in {'BUY','SELL','REINVEST','FX','OPENING_POSITION'} and dec(p['fee']) != 0:
        raise DomainError('此类流水的手续费请单独记录为费用，不能隐含扣除')
    if kind not in {'BUY','SELL','DIVIDEND','REINVEST','OPENING_POSITION'} and dec(p['tax']) != 0:
        raise DomainError('此类流水的税费请单独记录为税费')
    if kind == 'BUY':
        if p.get('payment_date') and day(p['payment_date']) > d:
            raise DomainError('申购付款日不能晚于净值归属日；普通买入留空即可')
        if p.get('confirmation_date') and day(p['confirmation_date']) < d:
            raise DomainError('确认日不能早于净值归属日')
    if kind in {'SELL', 'REDEEM_PENDING'} and p.get('payment_date') and day(p['payment_date']) < d:
        raise DomainError('卖出/赎回到账日不能早于成交日')
    if kind in {'DIVIDEND', 'REINVEST'}:
        net_value=p.get('net_amount')
        if net_value in (None,'') and p.get('gross_amount') not in (None,''):
            net_value=dec(p['gross_amount'],'税前分红',positive=True)-dec(p['tax'])
        net = dec(net_value, '实际净分红', nonnegative=True)
        known = str(p.get('tax_known', True)).lower() not in ('false', '0')
        gross = dec(p.get('gross_amount') or (net + dec(p['tax'])), '税前分红', positive=True)
        if known and gross - dec(p['tax']) != net:
            raise DomainError('税前分红 − 税费必须等于实际净分红')
        if gross < net:
            raise DomainError('税前分红不能低于净分红')
        if not known:
            p['tax'] = ds(gross - net)
        p.update(gross_amount=ds(gross), net_amount=ds(net), tax_known=known,
                 ex_date_known=str(p.get('ex_date_known', True)).lower() not in ('false', '0'))
        if p.get('record_date') and instruments[inst_id]['market'] not in ('HK','US') and day(p['record_date'])>d:raise DomainError('登记日不能晚于除息日，港股过户截止日不是权益登记日')
        p['pay_date'] = p.get('pay_date') or d.isoformat()
        if day(p['pay_date']) < d:
            raise DomainError('分红到账日不能早于除息/暂定入账日')
        p['pay_fx_rate'] = '1' if c == 'CNY' else ds(dec(p.get('pay_fx_rate') or p['fx_rate'], '到账折算汇率', positive=True))
        for prefix,date_field in (('pay_fx','pay_date'),('reinvest_fx','reinvest_date')):
            if prefix=='reinvest_fx' and kind!='REINVEST':continue
            if p.get(prefix+'_source_date'):
                source=day(p[prefix+'_source_date'],'汇率源日期')
                if source>day(p.get(date_field) or p['pay_date']):raise DomainError('汇率源日期不能晚于对应到账日或再投日')
                p[prefix+'_source_date']=source.isoformat()
            if p.get(prefix+'_type'):p[prefix+'_type']=str(p[prefix+'_type'])[:40]
        if kind == 'REINVEST':
            p['reinvest_quantity'] = ds(dec(p.get('reinvest_quantity'), '再投确认份额', positive=True))
            if p.get('reinvest_gross_amount') not in (None,''):
                p['reinvest_gross_amount']=ds(dec(p['reinvest_gross_amount'],'再投总价',positive=True))
                p['reinvest_price']=ds(dec(p['reinvest_gross_amount'])/dec(p['reinvest_quantity']))
            else:p['reinvest_price'] = ds(dec(p.get('reinvest_price'), '再投确认净值', positive=True))
            p['reinvest_date'] = p.get('reinvest_date') or p['pay_date']
            if day(p['reinvest_date']) < d:
                raise DomainError('再投净值归属日不能早于除息日')
            p['reinvest_fx_rate'] = '1' if c == 'CNY' else ds(dec(p.get('reinvest_fx_rate') or p['pay_fx_rate'], '再投折算汇率', positive=True))
            applied = (dec(p['reinvest_gross_amount']) if p.get('reinvest_gross_amount') else dec(p['reinvest_quantity']) * dec(p['reinvest_price'])) + dec(p['fee'])
            if applied > net:
                raise DomainError('再投实付不能超过净分红；补充投入请另记普通买入')
    else:
        p.pop('tax_known', None); p.pop('ex_date_known', None)
    if kind == 'FX':
        p['from_currency'] = currency(p.get('from_currency', 'CNY'))
        p['to_currency'] = currency(p.get('to_currency', 'HKD'))
        if p['from_currency'] == p['to_currency']:
            raise DomainError('换汇两端币种必须不同')
        p['currency'] = p['from_currency']
        p['from_amount'] = ds(dec(p.get('from_amount'), '真实付出金额', positive=True))
        p['to_amount'] = ds(dec(p.get('to_amount'), '真实收到金额', positive=True))
        p['from_fx_rate'] = '1' if p['from_currency'] == 'CNY' else ds(dec(p.get('from_fx_rate') or p['fx_rate'], '付出币种折算汇率', positive=True))
        p['to_fx_rate'] = '1' if p['to_currency'] == 'CNY' else ds(dec(p.get('to_fx_rate'), '收到币种折算汇率', positive=True))
        p['fx_rate'] = p['from_fx_rate']
    if kind == 'SPLIT':
        p['factor'] = ds(dec(p.get('factor'), '拆分倍数', positive=True))
    if kind == 'ADJUSTMENT':
        if not p['reason'].strip():
            raise DomainError('人工企业行动调整必须填写原因')
        p['new_quantity'] = ds(dec(p.get('new_quantity'), '调整后份额', nonnegative=True))
        p['new_cost'] = ds(dec(p.get('new_cost'), '调整后总成本 · '+p['currency'], nonnegative=True))
    for key in ('historical_cost', 'initial_net_invested'):
        if p.get(key) not in (None, ''):
            p[key] = ds(dec(p[key], key, nonnegative=key == 'historical_cost'))
        else:
            p[key] = None
    p['locked_base_amount'] = ds((dec(p.get('gross_amount') or p.get('amount') or 0)
                                  + (dec(p['fee']) + dec(p['tax']) if kind == 'BUY' else
                                     -(dec(p['fee']) + dec(p['tax'])) if kind == 'SELL' else 0)) * dec(p['fx_rate']))
    if kind not in ('SELL','REDEEM_PENDING'):p.pop('sell_lots',None)
    if kind not in ('FEE','TAX'):p.pop('related_event_id',None)
    if kind not in ('DIVIDEND','REINVEST'):
        p.pop('dividend_basis',None);p.pop('record_date',None)
    allowed = {'kind','portfolio_id','date','sequence','instrument_id','currency','fx_rate','fx_type','fx_source_date',
               'reason','external_ref','fee','tax','payment_date','confirmation_date','pay_date','reinvest_date',
               'amount','quantity','price','gross_amount','net_amount','tax_known','ex_date_known','pay_fx_rate',
               'reinvest_quantity','reinvest_price','reinvest_gross_amount','reinvest_fx_rate','pay_fx_source_date','pay_fx_type','reinvest_fx_source_date','reinvest_fx_type','from_currency','to_currency','from_amount',
               'to_amount','from_fx_rate','to_fx_rate','factor','new_quantity','new_cost','historical_cost',
               'initial_net_invested','asset_class','region','tags','locked_base_amount','reservation_id','transfer_id','source_dividend_id','source_split_id','sell_lots','dividend_basis','record_date','related_event_id','costs_combined'}
    return {k: v for k, v in p.items() if k in allowed and v != ''}


class Series:
    def __init__(self, rows):
        chosen = {}
        for r in rows:
            if r.get('cancelled'):
                continue
            key = r['date']
            previous = chosen.get(key)
            if previous is None or (bool(r.get('manual')), r.get('version_id', 0)) >= (bool(previous.get('manual')), previous.get('version_id', 0)):
                chosen[key] = r
        self.rows = sorted(chosen.values(), key=lambda r: r['date'])
        self.dates = [r['date'] for r in self.rows]

    def at(self, d):
        index = bisect.bisect_right(self.dates, str(d)) - 1
        return self.rows[index] if index >= 0 else None


class Market:
    def __init__(self, state):
        self.price_series = {}
        grouped = defaultdict(list)
        for row in state['prices']:
            if row.get('manual'): continue
            grouped[(row['instrument_id'], row['kind'])].append(row)
        for key, rows in grouped.items():
            self.price_series[key] = Series(rows)
        self.fx_series = {c: Series([r for r in state['fx'] if r['currency'] == c and (not r.get('manual') or r.get('mode') == 'daily')]) for c in CURRENCIES}
        self.manual_fx = {c: sorted([r for r in state['fx'] if r['currency'] == c and r.get('manual') and r.get('mode') != 'daily' and not r.get('cancelled')], key=lambda r: (r['date'], r.get('version_id', 0))) for c in CURRENCIES}

    def price(self, instrument_id, d, kind='raw'):
        series = self.price_series.get((instrument_id, kind))
        return series.at(d) if series else None

    def fx(self, c, d):
        if c == 'CNY':
            return {'rate': '1', 'date': str(d), 'source': 'CNY', 'manual': False}
        candidates = [r for r in self.manual_fx[c] if r['date'] <= str(d)]
        if candidates:
            latest = candidates[-1]
            if latest.get('mode') != 'auto' and (not latest.get('end_date') or str(d) < latest['end_date']):
                return latest
        return self.fx_series[c].at(d)


def operations(events):
    result = []
    for e in events:
        d, kind = e['date'], e['kind']
        seq = e.get('sequence', 0)
        def op(at, name, order=0, **extra):
            result.append((at, seq, order, e['id'], name, dict(e, **extra)))
        if kind == 'BUY':
            payment = e.get('payment_date') or d
            if payment < d:
                op(payment, 'BUY_PREPAY', 0)
                op(d, 'BUY', 1, prepaid=True)
            else:
                op(d, 'BUY')
        elif kind == 'SELL':
            payment = e.get('payment_date') or d
            op(d, 'SELL', 0, deferred=payment > d)
            if payment > d:
                op(payment, 'SELL_RECEIPT', 1)
        elif kind in ('DIVIDEND', 'REINVEST'):
            op(d, 'DIV_ACCRUE', 0)
            pay = e['pay_date']
            early = kind == 'REINVEST' and e['reinvest_date'] < pay
            if early:
                op(e['reinvest_date'], 'REINVEST_EARLY', 1)
                op(pay, 'DIV_RECEIPT', 2, early_reinvest=True)
            else:
                op(pay, 'DIV_RECEIPT', 1)
                if kind == 'REINVEST':
                    op(e['reinvest_date'], 'REINVEST_BUY', 2)
        elif kind == 'OPENING_RECEIVABLE':
            op(d, kind)
            if e.get('pay_date'):
                op(e['pay_date'], 'OPENING_RECEIPT', 1)
        else:
            op(d, kind)
    receipts = {'DEPOSIT','INTEREST','DIV_ACCRUE','DIV_RECEIPT','SELL_RECEIPT','OPENING_RECEIPT'}
    def key(r):
        # A split changes overnight holdings before ex-day purchases/sales.
        # Source insertion order must not multiply newly bought ex-day shares.
        priority = -3 if r[4]=='SPLIT' else -2 if r[4].startswith('OPENING_') and r[4] != 'OPENING_RECEIPT' else -1 if r[4] in receipts else 0
        return (r[0], priority, r[1], r[2])
    return sorted(result, key=key)


class Ledger:
    def __init__(self, state, portfolio):
        self.data = state
        self.portfolio = portfolio
        self.instruments = {i['id']: i for i in state['instruments']}
        self.events = [e for e in state['events'] if e['portfolio_id'] == portfolio['id']]
        self.allocations = {a['instrument_id']: a for a in state['allocations'] if a['portfolio_id'] == portfolio['id']}
        self.reservations = [r for r in state['reservations'] if r['portfolio_id'] == portfolio['id']]
        self.market = Market(state)
        self.budget_mode = portfolio.get('funding_mode') == 'budget'
        self.budget_history = portfolio.get('budget_history') or [{'date':portfolio['start_date'],'amount':portfolio['planned_amount']}]
        effective_events = [e for e in self.events if not self.budget_mode or e['kind'] not in ('DEPOSIT','OPENING_CASH')]
        self.ops = operations(effective_events)
        if self.budget_mode:
            for n, row in enumerate(self.budget_history):
                e = {'date':row['date'],'amount':row['amount'],'currency':'CNY','fx_rate':'1','fee':'0','tax':'0'}
                self.ops.append((row['date'],-10,0,'budget:'+str(n),'BUDGET_OPEN' if n==0 else 'BUDGET_CHANGE',e))
            self.ops.sort(key=lambda op:(op[0],-5 if op[4].startswith('BUDGET_') else -3 if op[4]=='SPLIT' else -2 if op[4].startswith('OPENING_') else -1 if op[4] in ('INTEREST','DIV_ACCRUE','DIV_RECEIPT','SELL_RECEIPT','OPENING_RECEIPT') else 0,op[1],op[2]))
        self._split_entitlements = {}
        self.reset()

    def reset(self):
        self.split_effects = {}
        self.cash = defaultdict(lambda: ZERO)
        self.positions = {}
        self.receivables = {}
        self.capital = ZERO
        self.opening_capital = ZERO
        self.net_flows = ZERO
        self.dividends = ZERO
        self.other_income = ZERO
        self.fees = ZERO
        self.taxes = ZERO
        self.realized_price = ZERO
        self.investment_cost = ZERO
        self.notes = []
        self.last_trades = {}
        self.native_components = {k: {c: ZERO for c in CURRENCIES}
                                  for k in ('price', 'dividends', 'fees', 'taxes', 'other')}

    def position(self, instrument_id):
        if instrument_id not in self.positions:
            self.positions[instrument_id] = {'quantity': ZERO, 'cost_native': ZERO, 'cost_base': ZERO,
                'cost_fee_native': ZERO, 'net_invested': ZERO, 'net_known': True, 'lots': [], 'realized_price': ZERO,
                'reference_cost_native': ZERO, 'reference_cost_known': True}
        return self.positions[instrument_id]

    def reserved(self, c, d, spending_event=None):
        # Kept only for compatibility with old backup fields. Reservations are retired.
        return ZERO

    def spend(self, c, value, e, d, fx_override=None):
        if self.budget_mode:
            value *= dec(fx_override if fx_override is not None else e['fx_rate']) if c != 'CNY' else ONE
            c = 'CNY'
        if value < 0:
            raise DomainError('支出不能为负数')
        available = self.cash[c] - self.reserved(c, d, e)
        if value > available + Decimal('0.000000000001'):
            hint = '请核对成交总价、费税或调整组合预算' if self.budget_mode else '请先记录真实入金或换汇'
            raise DomainError(f"{d} {e.get('kind','预算调整')}：{c}可投入余额不足（可用{ds(available)}，需要{ds(value)}）；{hint}")
        self.cash[c] -= value
        if abs(self.cash[c]) < Decimal('0.000000000001'):
            self.cash[c] = ZERO

    def receive(self, key, c, value, instrument_id, label, pending=False):
        self.receivables[key] = {'currency': c, 'amount': value, 'instrument_id': instrument_id,
                                 'label': label, 'pending': pending}

    def credit(self, c, value, fx=ONE):
        self.cash['CNY' if self.budget_mode else c] += value * fx if self.budget_mode else value

    def settle(self, key, fx=ONE):
        item = self.receivables.pop(key, None)
        if not item:
            raise DomainError('关联应收/在途记录缺失')
        self.credit(item['currency'], item['amount'], fx)
        return item['amount']

    def buy(self, e, d, *, prepaid=False, reinvest=False, early=False):
        inst = e['instrument_id']; c = e['currency']; p = self.position(inst)
        qty = dec(e['reinvest_quantity'] if reinvest else e['quantity'])
        price = dec(e['reinvest_price'] if reinvest else e['price'])
        gross = dec(e['reinvest_gross_amount']) if reinvest and e.get('reinvest_gross_amount') else qty * price if reinvest else dec(e['gross_amount'])
        fee, tax = dec(e['fee']), ZERO if reinvest else dec(e['tax'])
        net = gross + fee + tax
        fx = dec(e.get('reinvest_fx_rate') if reinvest else e['fx_rate'])
        self.investment_cost += net * fx
        if prepaid:
            self.receivables.pop(e['id'] + ':prepay')
        elif early:
            self.receivables[e['id'] + ':div']['amount'] -= net
        else:
            self.spend(c, net, e, d,fx_override=fx)
        p['quantity'] += qty
        p['cost_native'] += gross
        p['reference_cost_native'] += gross
        p['cost_base'] += gross * fx
        p['cost_fee_native'] += fee + tax
        if reinvest:
            # Linked reinvestment is funded by its dividend, not new personal capital.
            funding_fx = dec(e['pay_fx_rate'])
            if early:
                p['net_invested'] -= net * funding_fx
            p['net_invested'] += net * (fx if self.budget_mode and not early else funding_fx)
        elif not prepaid:
            p['net_invested'] += net * fx
        self.fees += fee * fx
        self.taxes += tax * fx
        p['lots'].append({'event_id': e['id'], 'date': str(d), 'available_on': e.get('confirmation_date') or str(d),
                          'quantity': qty, **tags(e)})
        self.last_trades[inst] = {'price': ds(price), 'date': str(d), 'source': '已确认成交价（暂估）', 'kind': 'raw', 'fallback': True}

    def sell_quantity(self, e, d, qty):
        p = self.position(e['instrument_id'])
        available = sum((lot['quantity'] for lot in p['lots'] if lot['available_on'] <= str(d)), ZERO)
        if qty > available + Decimal('0.000000000001'):
            raise DomainError(f"{d}：卖出/赎回超过可用持仓（可用{ds(available)}份，需要{ds(qty)}份）")
        selected=e.get('sell_lots')
        if selected:
            if sum((dec(r['quantity']) for r in selected),ZERO)!=qty:raise DomainError('卖出数量必须等于所选批次份额合计')
            targets={r['buy_event_id']:dec(r['quantity']) for r in selected}
            if len(targets)!=len(selected):raise DomainError('不能重复选择同一买入批次')
            for bid,used in targets.items():
                lot=next((r for r in p['lots'] if r['event_id']==bid),None)
                if not lot or lot['available_on']>str(d) or used<=0 or used>lot['quantity']:
                    raise DomainError(f'{d}：买入批次 {bid[:8]} 不存在、未确认或余额不足（可卖{ds(lot["quantity"]) if lot else 0}份，需要{ds(used)}份），请重新选择')
        else:
            targets={};left=qty
            # Compatibility for immutable historical SELL/REDEEM records only.
            for lot in p['lots']:
                if lot['available_on']>str(d):continue
                used=min(left,lot['quantity']);targets[lot['event_id']]=used;left-=used
                if left<=0:break
        fraction = qty / p['quantity']
        removed_cost = p['cost_native'] * fraction
        p['cost_native'] -= removed_cost
        p['reference_cost_native'] *= ONE - fraction
        p['cost_base'] *= ONE - fraction
        p['cost_fee_native'] *= ONE - fraction
        p['quantity'] -= qty
        if p['quantity'] == ZERO: p['reference_cost_known'] = True
        for lot in p['lots']:lot['quantity']-=targets.get(lot['event_id'],ZERO)
        if abs(p['quantity']) < Decimal('0.000000000001'):
            p.update(quantity=ZERO, cost_native=ZERO, cost_base=ZERO, cost_fee_native=ZERO)
        return removed_cost

    def apply(self, op):
        d, sequence, order, event_id, kind, e = op
        before_components = {'price': self.realized_price, 'dividends': self.dividends,
                             'fees': self.fees, 'taxes': self.taxes, 'other': self.other_income}
        c, fx, inst = e['currency'], dec(e['fx_rate']), e.get('instrument_id')
        fee, tax = dec(e['fee']), dec(e['tax'])
        p = self.position(inst) if inst else None
        if kind in ('BUDGET_OPEN','BUDGET_CHANGE'):
            amount = dec(e['amount'])
            if amount < 0: self.spend('CNY',-amount,e,d)
            else: self.cash['CNY'] += amount
            self.capital += amount
            if kind=='BUDGET_OPEN': self.opening_capital += amount
            else: self.net_flows += amount
        elif kind in {'DEPOSIT', 'OPENING_CASH'}:
            amount = dec(e['amount']); self.cash[c] += amount
            base = amount * fx; self.capital += base
            if kind == 'OPENING_CASH': self.opening_capital += base
            else: self.net_flows += base
        elif kind == 'WITHDRAW':
            amount = dec(e['amount']); self.spend(c, amount, e, d)
            self.capital -= amount * fx; self.net_flows -= amount * fx
        elif kind == 'OPENING_POSITION':
            qty = dec(e['quantity']); value = dec(e['gross_amount']); base = value * fx
            self.investment_cost += base
            p['quantity'] += qty; p['cost_native'] += value; p['cost_base'] += base
            if e.get('historical_cost') is not None:
                p['reference_cost_native'] += dec(e['historical_cost'])
            else:
                p['reference_cost_known'] = False
            if self.budget_mode: self.spend(c,value,e,d)
            else: self.capital += base; self.opening_capital += base
            if e.get('initial_net_invested') is None:
                p['net_known'] = False
            else:
                p['net_invested'] += dec(e['initial_net_invested'])
            p['lots'].append({'event_id': event_id, 'date': d, 'available_on': d, 'quantity': qty, **tags(e)})
            self.last_trades[inst] = {'price': e['price'], 'date': d, 'source': '期初公允价值', 'kind': 'raw', 'fallback': True}
        elif kind == 'OPENING_RECEIVABLE':
            value = dec(e['amount']); self.receive(event_id + ':opening', c, value, inst, '期初应收')
            if self.budget_mode: self.spend(c,value,e,d)
            else: self.capital += value * fx; self.opening_capital += value * fx
        elif kind == 'OPENING_RECEIPT': self.settle(event_id + ':opening',fx)
        elif kind == 'BUY_PREPAY':
            value = dec(e['gross_amount']) + fee + tax
            self.spend(c, value, e, d); self.receive(event_id + ':prepay', c, value, inst, '申购在途')
            p['net_invested'] += value * fx
        elif kind == 'BUY': self.buy(e, d, prepaid=e.get('prepaid', False))
        elif kind == 'SELL':
            gross = dec(e['gross_amount']); qty = dec(e['quantity'])
            removed = self.sell_quantity(e, d, qty)
            gain = (gross - removed) * fx; self.realized_price += gain; p['realized_price'] += gain
            self.fees += fee * fx; self.taxes += tax * fx
            net = gross - fee - tax
            if e.get('deferred'):
                self.receive(event_id + ':sell', c, net, inst, '赎回待到账')
            else:
                self.credit(c,net,fx); p['net_invested'] -= net * fx
            self.last_trades[inst] = {'price': e['price'], 'date': d, 'source': '已确认成交价（暂估）', 'kind': 'raw', 'fallback': True}
        elif kind == 'SELL_RECEIPT':
            value = self.settle(event_id + ':sell',fx); p['net_invested'] -= value * fx
        elif kind == 'DIV_ACCRUE':
            self.dividends += dec(e['gross_amount']) * fx; self.taxes += tax * fx
            self.receive(event_id + ':div', c, dec(e['net_amount']), inst, '分红应收')
            if not e.get('tax_known', True) or not e.get('ex_date_known', True):
                self.notes.append('分红税额或除息日待核对')
        elif kind == 'DIV_RECEIPT':
            value = self.settle(event_id + ':div',dec(e['pay_fx_rate'])); p['net_invested'] -= value * dec(e['pay_fx_rate'])
        elif kind == 'REINVEST_EARLY': self.buy(e, d, reinvest=True, early=True)
        elif kind == 'REINVEST_BUY': self.buy(e, d, reinvest=True)
        elif kind == 'FX':
            source, target = e['from_currency'], e['to_currency']
            if self.budget_mode:
                difference=dec(e['to_amount'])*dec(e['to_fx_rate'])-(dec(e['from_amount'])+fee)*dec(e['from_fx_rate'])
                if difference<0: self.spend('CNY',-difference,e,d)
                else: self.credit('CNY',difference)
            else:
                self.spend(source, dec(e['from_amount']) + fee, e, d); self.cash[target] += dec(e['to_amount'])
            self.fees += fee * dec(e['from_fx_rate'])
        elif kind in {'FEE', 'TAX'}:
            value = dec(e['amount']); self.spend(c, value, e, d)
            if kind == 'FEE': self.fees += value * fx
            else: self.taxes += value * fx
            if p is not None: p['net_invested'] += value * fx
        elif kind == 'INTEREST':
            value = dec(e['amount']); self.credit(c,value,fx); self.other_income += value * fx
        elif kind == 'SUBSCRIBE_PENDING':
            value = dec(e['amount']); self.spend(c, value, e, d)
            self.receive(event_id + ':pending', c, value, inst, '申购待确认', True)
            p['net_invested'] += value * fx
        elif kind == 'REDEEM_PENDING':
            value = dec(e['amount']); self.sell_quantity(e, d, dec(e['quantity']))
            self.receive(event_id + ':pending', c, value, inst, '赎回金额待确认', True)
            self.notes.append('有赎回估计金额待确认，请更正为正式卖出')
        elif kind == 'SPLIT':
            factor = dec(e['factor'])
            # Derive from the replayed remainder, never from original purchases or
            # a saved confirmation-time snapshot. Backfills can change the lots.
            lots = [{'buy_event_id': lot['event_id'], 'date': lot['date'],
                     'quantity_before': lot['quantity'], 'quantity_after': lot['quantity'] * factor,
                     'quantity_change': lot['quantity'] * (factor - ONE)}
                    for lot in p['lots'] if lot['quantity'] > ZERO]
            self.split_effects[event_id] = {
                'event_id': event_id, 'instrument_id': inst, 'date': d, 'factor': factor,
                'quantity_before': p['quantity'], 'quantity_after': p['quantity'] * factor,
                'quantity_change': p['quantity'] * (factor - ONE),
                'cost_native': p['cost_native'], 'currency': c,
                'status': 'applied' if lots else 'no_position', 'lots': lots}
            p['quantity'] *= factor
            for lot in p['lots']: lot['quantity'] *= factor
            if inst in self.last_trades:
                prev = self.last_trades[inst]
                self.last_trades[inst] = dict(prev, price=ds(dec(prev['price']) / factor), date=d,
                                              source='拆分后成交价暂估', fallback=True)
        elif kind == 'ADJUSTMENT':
            new_qty = dec(e['new_quantity']); old_qty = p['quantity']
            if old_qty == 0 and new_qty > 0:
                raise DomainError('空持仓不能用企业行动凭空建仓；请使用期初持仓或买入')
            for lot in p['lots']:
                lot['quantity'] = lot['quantity'] * new_qty / old_qty if old_qty else ZERO
            p['quantity'] = new_qty; p['cost_native'] = dec(e['new_cost'])
            self.notes.append('人工企业行动调整待核对')
        operation_fx = dec(e['reinvest_fx_rate']) if kind in ('REINVEST_EARLY', 'REINVEST_BUY') else fx
        after_components = {'price': self.realized_price, 'dividends': self.dividends,
                            'fees': self.fees, 'taxes': self.taxes, 'other': self.other_income}
        for key in before_components:
            self.native_components[key][c] += (after_components[key] - before_components[key]) / operation_fx

    def has_unconfirmed_split(self, instrument_id, as_of):
        for action in self.data['corporate_actions']:
            if action.get('kind')!='split_notice' or action['instrument_id']!=instrument_id or action['date']>str(as_of):continue
            if split_notice_confirmed(split_notice_records(self.events,action),action):continue
            key=(instrument_id,action['date'])
            if key not in self._split_entitlements:
                # Check overnight ownership, including positions sold since the action.
                prior=Ledger(dict(self.data,corporate_actions=[]),self.portfolio)
                for op in prior.ops:
                    if op[0]>=action['date']:break
                    prior.apply(op)
                self._split_entitlements[key]=prior.position(instrument_id)['quantity']>0
            if self._split_entitlements[key]:return True
        return False

    def snapshot(self, d):
        warnings = list(self.notes)
        missing = []
        assets = ZERO; cash_total = ZERO; available_total = ZERO; unrealized_price = ZERO
        holdings, currencies, receivables = [], [], []
        def converted(value, c, label):
            if value == 0: return ZERO, self.market.fx(c, d)
            r = self.market.fx(c, d)
            if r is None:
                missing.append(f'{label}缺少{c}估值汇率'); return None, None
            return value * dec(r['rate']), r
        for c in CURRENCIES:
            amount = self.cash[c]; reserved = self.reserved(c, d)
            value, fx = converted(amount, c, '现金')
            free_value, _ = converted(amount - reserved, c, '可投现金')
            if reserved > amount + Decimal('0.000000000001'):
                warnings.append(c + '预留高于现金，请释放或核对')
            if value is not None: assets += value; cash_total += value
            if free_value is not None: available_total += free_value
            currencies.append({'currency': c, 'balance': amount, 'reserved': reserved, 'available': amount - reserved,
                               'value_base': value, 'available_base': free_value, 'fx': fx})
        for key, item in self.receivables.items():
            value, fx = converted(item['amount'], item['currency'], item['label'])
            if value is not None: assets += value
            receivables.append(dict(item, id=key, value_base=value, fx=fx))
            if item.get('pending'): warnings.append(item['label'])
        for inst_id in dict.fromkeys(list(self.allocations) + list(self.positions)):
            p = self.position(inst_id); i = self.instruments[inst_id]; qty = p['quantity']
            quote = self.market.price(inst_id, d)
            fallback = self.last_trades.get(inst_id)
            if fallback and (quote is None or fallback['date'] > quote['date']): quote = fallback
            current_price = dec(quote['price']) if quote else None
            unresolved_split=self.has_unconfirmed_split(inst_id,d)
            if unresolved_split:
                current_price=None;missing.append(i['name']+'有待确认送转/拆股，请核对份额')
            rate = self.market.fx(i['currency'], d)
            value = None
            if qty == 0 and not unresolved_split:
                value = ZERO
            elif current_price is None:
                missing.append(i['name'] + '缺少价格/单位净值')
            elif rate is None:
                missing.append(i['name'] + '缺少' + i['currency'] + '估值汇率')
            else:
                value = qty * current_price * dec(rate['rate']); assets += value
                unrealized_price += (qty * current_price - p['cost_native']) * dec(rate['rate'])
                if quote.get('fallback'):
                    warnings.append(i['name'] + '使用已确认成交/期初价格暂估')
                elif (day(d) - day(quote['date'])).days > 4 and not no_expected_session(i['market'], quote['date'], d):
                    warnings.append(i['name'] + '沿用' + quote['date'] + '价格，可能休市或尚未更新')
            allocation = self.allocations.get(inst_id, {})
            lots = [dict(lot, **(tags(allocation) if self.budget_mode else {}), value_base=lot['quantity'] * current_price * dec(rate['rate'])
                         if current_price is not None and rate is not None else None)
                    for lot in p['lots'] if lot['quantity'] > 0]
            holdings.append({'instrument': i, 'instrument_id': inst_id, 'quantity': qty, 'unconfirmed_split': unresolved_split,
                'available_quantity': sum((l['quantity'] for l in p['lots'] if l['available_on'] <= str(d)), ZERO),
                'price': current_price, 'quote': quote, 'fx': rate, 'value_base': value,
                'cost_native': p['cost_native'], 'cost_base': p['cost_base'],
                'cost_including_fees_native': p['cost_native'] + p['cost_fee_native'],
                'reference_cost_native': p['reference_cost_native'] if p['reference_cost_known'] else None,
                'reference_gain_native': qty * current_price - p['reference_cost_native'] if p['reference_cost_known'] and current_price is not None else None,
                'average_cost': ratio(p['cost_native'] + p['cost_fee_native'], qty),
                'average_cost_excluding_fees':ratio(p['cost_native'],qty), 'net_invested_base': p['net_invested'] if p['net_known'] else None,
                'net_invested_since_start': p['net_invested'], 'target_pct': dec(allocation.get('target_pct') or 0),
                'target_amount': dec(self.portfolio['planned_amount']) * dec(allocation.get('target_pct') or 0) / 100,
                'allocation': allocation, 'lots': lots, 'realized_price_base': p['realized_price']})
        incomplete = bool(missing)
        total = None if incomplete else assets
        pnl = total - self.capital if total is not None else None
        price_pnl = self.realized_price + unrealized_price if not incomplete else None
        native_components = {k: dict(v) for k, v in self.native_components.items()}
        for inst_id, p in self.positions.items():
            if p['quantity'] == ZERO: continue
            h = next(h for h in holdings if h['instrument_id'] == inst_id)
            c = self.instruments[inst_id]['currency']
            if h['price'] is None: native_components['price'][c] = None
            elif native_components['price'][c] is not None:
                native_components['price'][c] += p['quantity'] * h['price'] - p['cost_native']
        fx_pnl = pnl - price_pnl - self.dividends - self.other_income + self.fees + self.taxes if pnl is not None else None
        from .instruments import sort_key
        holdings.sort(key=lambda h:sort_key(h['instrument']))
        securities_complete=all(h['value_base'] is not None for h in holdings)
        securities_total=sum((h['value_base'] for h in holdings if h['value_base'] is not None),ZERO) if securities_complete else None
        for h in holdings:
            h['weight'] = ratio(h['value_base'], total) if h['value_base'] is not None else None
            h['holding_weight'] = ratio(h['value_base'],securities_total) if h['value_base'] is not None and securities_total is not None else None
            h['input_weight'] = ratio(max(ZERO, h['net_invested_base']), dec(self.portfolio['planned_amount'])) if h['net_invested_base'] is not None else None
            h['completion'] = ratio(max(ZERO, h['net_invested_base']), h['target_amount']) if h['net_invested_base'] is not None else None
        return {'date': str(d), 'portfolio': self.portfolio, 'total_assets': total, 'known_assets': assets,
                'investment_cost':self.investment_cost,'securities_total':securities_total,'invested_position':ratio(securities_total,total) if securities_total is not None else None,
                'funding_mode': 'budget' if self.budget_mode else 'cash',
                'invested_total':sum((h['net_invested_base'] for h in holdings if h['net_invested_base'] is not None),ZERO),
                'capital': self.capital, 'opening_capital': self.opening_capital, 'net_deposits': self.net_flows,
                'cash_total': None if any(c['value_base'] is None for c in currencies) else cash_total,
                'available_cash': None if any(c['available_base'] is None for c in currencies) else available_total,
                'reserved_total': cash_total - available_total if not any(c['available_base'] is None or c['value_base'] is None for c in currencies) else None,
                'budget_gap': max(ZERO, dec(self.portfolio['planned_amount']) - self.capital),
                'pnl': pnl, 'components': {'price': price_pnl, 'dividends': self.dividends,
                    'fx': fx_pnl, 'fees': self.fees, 'taxes': self.taxes, 'other': self.other_income},
                'holdings': holdings, 'cash': currencies, 'receivables': receivables,
                'warnings': list(dict.fromkeys(warnings + missing)), 'incomplete': incomplete,
                'estimated': bool(warnings), '_native_components': native_components,
                'attribution_basis': '各交易币种每日价差、分红、费税按当日估值汇率折算；其余折算变动为汇率影响，交易原始折算额保持锁定'}

    def run(self, as_of=None, include_history=True):
        end = day(as_of or today())
        start = day(self.portfolio['start_date'])
        if end < start:
            raise DomainError('估值日期不能早于组合起始日')
        if (end - start).days > 36525:
            raise DomainError('估值区间超过100年')
        self.reset(); cursor = 0; history = []; prev_assets = ZERO; prev_capital = ZERO
        prev_components = {k: ZERO for k in ('price', 'dividends', 'fx', 'fees', 'taxes', 'other')}
        prev_native = {k: {c: ZERO for c in CURRENCIES} for k in self.native_components}
        attributed = {k: ZERO for k in self.native_components}; attribution_valid = True
        chain = ONE; segment = 1; chain_valid = True; latest_complete = None; seen_active = False; was_zero = False
        d = start
        while d <= end:
            while cursor < len(self.ops) and self.ops[cursor][0] <= d.isoformat():
                self.apply(self.ops[cursor]); cursor += 1
            snap = self.snapshot(d)
            native = snap.pop('_native_components')
            for key in attributed:
                for c in CURRENCIES:
                    old, current = prev_native[key][c], native[key][c]
                    if old is None or current is None:
                        attribution_valid = False; continue
                    change = current - old
                    rate = self.market.fx(c, d)
                    if change and rate is None: attribution_valid = False
                    elif change: attributed[key] += change * dec(rate['rate'])
            prev_native = native
            if attribution_valid:
                snap['components'].update(attributed)
                snap['components']['fx'] = (snap['pnl'] - attributed['price'] - attributed['dividends']
                    - attributed['other'] + attributed['fees'] + attributed['taxes']) if snap['pnl'] is not None else None
            else:
                snap['components'] = {k: None for k in snap['components']}
                snap['warnings'].append('历史收益归因缺少价格或当日汇率，请补齐历史数据')
                snap['estimated'] = True
            inflow = ZERO; outflow = ZERO
            if self.budget_mode:
                for row in self.budget_history:
                    if row['date']==str(d):
                        value=dec(row['amount']);inflow+=max(ZERO,value);outflow+=max(ZERO,-value)
            for e in self.events:
                if e['date'] == d.isoformat():
                    if not self.budget_mode and e['kind'] in {'DEPOSIT', 'OPENING_CASH', 'OPENING_RECEIVABLE'}:
                        inflow += dec(e['amount']) * dec(e['fx_rate'])
                    elif not self.budget_mode and e['kind'] == 'OPENING_POSITION':
                        inflow += dec(e['gross_amount']) * dec(e['fx_rate'])
                    elif e['kind'] == 'WITHDRAW': outflow += dec(e['amount']) * dec(e['fx_rate'])
            assets = snap['total_assets']
            profit = assets - prev_assets - inflow + outflow if assets is not None and prev_assets is not None else None
            denominator = prev_assets + inflow if prev_assets is not None else None
            r = profit / denominator if profit is not None and denominator and denominator > 0 else None
            if r is not None and ONE + r >= 0:
                if not chain_valid or was_zero:
                    chain = ONE
                    if seen_active: segment += 1
                    chain_valid = True
                chain *= ONE + r
                seen_active = True
            elif assets == ZERO or profit is None:
                chain_valid = False
            twr = chain - ONE if chain_valid and (self.capital != 0 or assets != 0) else None
            delta_components = {k: snap['components'][k] - prev_components[k]
                                if snap['components'][k] is not None and prev_components[k] is not None else None
                                for k in prev_components}
            if not snap['incomplete'] and not snap['estimated']:
                latest_complete = str(d)
            history.append({'date': str(d), 'assets': assets, 'capital': self.capital, 'pnl': snap['pnl'],
                            'investment_cost':self.investment_cost,'day_profit': profit, 'inflow': inflow, 'outflow': outflow, 'return': r,
                            'twr': twr, 'segment': segment, 'components': delta_components,
                            'estimated': snap['estimated'], 'incomplete': snap['incomplete'], 'quality_reasons': snap['warnings'][:]})
            was_zero = assets == ZERO
            prev_assets = assets; prev_capital = self.capital; prev_components = snap['components']; d += timedelta(days=1)
        snap['twr'] = history[-1]['twr']
        snap['return_segment'] = segment
        snap['latest_complete_date'] = latest_complete
        snap['history'] = history if include_history else []
        snap['events'] = self.events
        return snap

    def validate(self):
        self.reset()
        for op in self.ops:
            self.apply(op)
        return True


def periods(history, grain='month', start=None, end=None, *, investment_basis=False):
    grouped = {}
    for row in history:
        d = day(row['date'])
        if start and d < day(start) or end and d > day(end): continue
        if grain == 'year': key = str(d.year)
        elif grain == 'week': key = (d - timedelta(days=d.weekday())).isoformat()
        elif grain == 'day': key = d.isoformat()
        else: key = d.strftime('%Y-%m')
        if key not in grouped:
            grouped[key] = {'period': key, 'from': d.isoformat(), 'to': d.isoformat(), 'profit': ZERO,
                            'factor': ONE, 'return_valid': True, 'has_return': False, 'segments': set(), 'estimated': False, 'incomplete': False,
                            'quality_reasons': {}, 'components': {k: ZERO for k in ('price', 'dividends', 'fx', 'fees', 'taxes', 'other')}}
        item = grouped[key]; item['to'] = d.isoformat()
        if row['day_profit'] is not None: item['profit'] += row['day_profit']
        else: item['incomplete'] = True
        if row['return'] is not None:
            item['factor'] *= ONE + row['return']; item['has_return'] = True; item['segments'].add(row['segment'])
        elif row['assets'] not in (None, ZERO): item['return_valid'] = False
        item['estimated'] |= row['estimated']; item['incomplete'] |= row['incomplete']
        for reason in row.get('quality_reasons', []):
            detail = item['quality_reasons'].setdefault(reason, {'message': reason, 'from': str(d), 'to': str(d), 'days': 0})
            detail['to'] = str(d); detail['days'] += 1
        for k in item['components']:
            if row['components'][k] is None: item['components'][k] = None
            elif item['components'][k] is not None: item['components'][k] += row['components'][k]
        item['assets'] = row['assets']
        item['investment_cost'] = row.get('investment_cost', ZERO)
    for item in grouped.values():
        item['quality_reasons'] = list(item['quality_reasons'].values())
        item['segment_split'] = len(item.pop('segments')) > 1
        return_valid = item.pop('return_valid'); has_return = item.pop('has_return')
        valid = return_valid and has_return and not item['incomplete'] and not item['segment_split']
        item['return'] = item.pop('factor') - ONE if valid else None
        if item['incomplete']: item['profit'] = None
        if investment_basis:
            item['return'] = ratio(item['profit'], item['investment_cost']) if item['profit'] is not None else None
            item['segment_split'] = False
    return list(grouped.values())[::-1]
