"""Post-trade market references, separate from realized lot profit and XIRR."""
from datetime import timedelta
from .common import ONE, day, dec, month_after, today
from .ledger import Market


def forward_performance(state, event, *, as_of=None):
    at = min(day(as_of or today()), today())
    trade_date = day(event['reinvest_date'] if event['kind'] == 'REINVEST' else event['date'])
    trade_price = dec(event['reinvest_price'] if event['kind'] == 'REINVEST' else event['price'])
    iid = event['instrument_id']; market = Market(state)
    # Never use a prior session as if it were the actual trade day's close.
    bases = {kind: market.price(iid, trade_date, kind) for kind in ('total_return', 'adjusted', 'raw')}
    basis = next((kind for kind, row in bases.items() if row and row['date'] == str(trade_date)), 'raw')
    base = bases[basis]
    if base and base['date'] != str(trade_date): base = None
    series = market.price_series.get((iid, basis)); windows = []
    for months in (1, 3):
        target = month_after(trade_date, months)
        item = {'months': months, 'target_date': str(target), 'date': None, 'price': None,
                'return': None, 'relative_trade_return': None, 'source': None}
        if target > at:
            item.update(status='pending', status_label='尚未到观察日')
        elif base is None or series is None:
            item.update(status='missing_base', status_label='缺交易日收盘价 / 净值')
        else:
            quote = next((r for r in series.rows if str(target) <= r['date'] <= str(min(at, target + timedelta(days=7)))), None)
            if quote is None:
                item.update(status='missing_quote', status_label='到期行情待同步，或处于休市 / 停牌')
            else:
                changed = basis == 'raw' and (
                    any(e.get('instrument_id') == iid and e['kind'] in ('SPLIT', 'ADJUSTMENT') and str(trade_date) < e['date'] <= quote['date'] for e in state['events']) or
                    any(a.get('instrument_id') == iid and a.get('kind') == 'split_notice' and str(trade_date) < a['date'] <= quote['date'] for a in state['corporate_actions']))
                if changed:
                    item.update(status='corporate_action', status_label='期间有拆分 / 企业行动，缺复权序列，暂不可比')
                else:
                    value = dec(quote['price']) / dec(base['price']) - ONE
                    item.update(status='available', status_label='已到观察日', date=quote['date'], price=quote['price'], source=quote.get('source'), **{'return': value})
                    raw = bases['raw']
                    if raw and raw['date'] == str(trade_date) and trade_price > 0:
                        item['relative_trade_return'] = (ONE + value) * dec(raw['price']) / trade_price - ONE
        windows.append(item)
    return {'trade_date': str(trade_date), 'as_of': str(at), 'currency': event['currency'], 'basis': basis,
            'basis_label': {'raw': '价格涨跌（未含分红）', 'adjusted': '复权价格参考（非已核验含息）', 'total_return': '全收益序列参考'}[basis],
            'base_date': base['date'] if base else None, 'base_price': base['price'] if base else None,
            'base_source': base.get('source') if base else None, 'windows': windows}
