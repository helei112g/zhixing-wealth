"""One factual projection for both transaction tables; no browser money arithmetic."""
from .common import ZERO, dec, today
from .trade_analytics import dividend_segments
from .ledger import Ledger


def split_effects(state, portfolio):
    """Read-only, current-revision projection using the accounting replay itself."""
    if not any(e['portfolio_id'] == portfolio['id'] and e['kind'] == 'SPLIT' for e in state['events']):
        return {}
    ledger = Ledger(state, portfolio)
    for op in ledger.ops:
        if op[0] > str(today()): break
        ledger.apply(op)
    return ledger.split_effects


def transaction_facts(event, market, events, as_of=None, *, split=None):
    end=str(as_of or today());kind=event['kind'];currency=event['currency']
    quantity=event.get('reinvest_quantity') if kind=='REINVEST' else event.get('quantity')
    unit=event.get('reinvest_price') if kind=='REINVEST' else event.get('price')
    fx=dec(event.get('reinvest_fx_rate') or event['fx_rate']) if kind=='REINVEST' else dec(event['fx_rate'])
    fees=dec(event.get('fee') or 0)+dec(event.get('tax') or 0)
    native=dec(event.get('gross_amount') or event.get('amount') or 0)
    label='金额';pending=False;distribution=None
    if kind in ('BUY','OPENING_POSITION'):
        native+=fees;label='总成本'
    elif kind=='SELL':
        native-=fees;label='净回款';pending=bool(event.get('payment_date') and event['payment_date']>end)
    elif kind=='REINVEST':
        native=dec(event.get('reinvest_gross_amount') or dec(quantity)*dec(unit))+dec(event.get('fee') or 0);label='再投成本'
    elif kind=='DIVIDEND':
        native=dec(event['net_amount']);label='净分红';pending=event['pay_date']>end
        rate=market.fx(currency,end) if pending else {'rate':event['pay_fx_rate']}
        fx=dec(rate['rate']) if rate else None
    elif kind in ('FEE','TAX'):label='费税支出'
    elif kind in ('DEPOSIT','OPENING_CASH'):label='历史资金'
    amount=native*fx if fx is not None else None
    if kind in ('DIVIDEND','REINVEST'):
        parts=dividend_segments(event,market,end)
        distribution=sum((p['amount'] for p in parts),ZERO) if all(p['amount'] is not None for p in parts) else None
        if distribution is not None:
            distribution-=sum((dec(e['amount'])*dec(e['fx_rate']) for e in events if e['kind'] in ('FEE','TAX') and e.get('related_event_id')==event['id'] and e['date']<=end),ZERO)
    unit_after_costs=native/dec(quantity) if kind in ('BUY','SELL','REINVEST') and quantity and dec(quantity)>0 else None
    return {'split':split,'quantity':quantity,'unit_price':unit,'unit_after_costs':unit_after_costs,
            'unit_after_costs_label':'扣费税净回款' if kind=='SELL' else '含费税成本价',
            'currency':currency,'fees_native':fees,
            'native_total':native,'base_total':amount,'amount_label':label,'fx_rate':fx,
            'pending':pending,'distribution_base':distribution,
            'dividend_gross_native':dec(event['gross_amount']) if kind in ('DIVIDEND','REINVEST') else None,
            'dividend_tax_native':dec(event['tax']) if kind in ('DIVIDEND','REINVEST') else None,
            'dividend_net_native':dec(event['net_amount']) if kind in ('DIVIDEND','REINVEST') else None,
            'dividend_tax_differs':dec(event['gross_amount'])!=dec(event['net_amount']) if kind in ('DIVIDEND','REINVEST') else False}
