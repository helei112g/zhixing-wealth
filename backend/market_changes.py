"""Recent quote changes, independent of portfolio cash flows and position size."""
from datetime import timedelta

from .common import ONE, day, dec
from .ledger import Market


def instrument_market_changes(state, instrument, as_of, *, market=None):
    at = day(as_of); iid = instrument['id']
    market = market or Market(state)
    def rows(kind):
        series = market.price_series.get((iid, kind))
        return [r for r in series.rows if r['date'] <= str(at)] if series else []
    raw, adjusted = rows('raw'), rows('adjusted')
    basis = 'adjusted' if adjusted and instrument['market'] in ('SH', 'SZ', 'HK', 'US') else 'raw'
    quotes = adjusted if basis == 'adjusted' else raw
    latest = quotes[-1] if quotes else None
    monday = at - timedelta(days=at.weekday())
    expected_date = max([r['date'] for r in (latest, raw[-1] if raw else None) if r], default=None)
    label = '复权价格参考（非已核验含息）' if basis == 'adjusted' else '单位净值（不含分红再投）' if instrument['market'] == 'FUND' else '收盘价（未复权，不含分红）'

    def change(base, weekly=False):
        item = dict(ratio=None, from_date=base['date'] if base else None,
                    to_date=latest['date'] if latest else None,
                    from_price=base['price'] if base else None,
                    to_price=latest['price'] if latest else None,
                    from_source=base.get('source') if base else None,
                    to_source=latest.get('source') if latest else None)
        def unavailable(status, reason):
            return dict(item, status=status, status_label=reason)
        if not latest:
            return unavailable('missing_quote', '暂无有效行情，请更新此标的')
        if latest['date'] != expected_date:
            return unavailable('missing_quote', '最新交易日的复权行情尚未同步（最新收盘 '+expected_date+'）')
        if weekly and day(latest['date']) < monday:
            return unavailable('pending', '本周尚无行情，等待本周首个交易日数据')
        if not base:
            return unavailable('missing_base', '缺少上周末基准行情' if weekly else '缺少前一交易日行情')
        boundary = monday if weekly else day(latest['date'])
        if (boundary - day(base['date'])).days > 15:
            return unavailable('missing_base', '基准行情间隔超过15日，暂不可比')
        if dec(base['price']) <= 0 or dec(latest['price']) <= 0:
            return unavailable('missing_quote', '价格无效，暂不可比')
        if basis == 'raw' and (
            any(e.get('instrument_id') == iid and e['kind'] in ('SPLIT', 'ADJUSTMENT')
                and base['date'] < e['date'] <= latest['date'] for e in state['events']) or
            any(a.get('instrument_id') == iid and a.get('kind') == 'split_notice'
                and base['date'] < a['date'] <= latest['date'] for a in state['corporate_actions'])
        ):
            return unavailable('corporate_action', '期间有拆分或企业行动，缺复权行情，暂不可比')
        return dict(item, ratio=dec(latest['price']) / dec(base['price']) - ONE,
                    status='available', status_label='最新行情 ÷ 基准行情 − 1')

    before = [r for r in quotes if r['date'] < str(monday)]
    return dict(as_of=str(at), currency=instrument['currency'], basis=basis, basis_label=label,
                latest_date=expected_date, week_start=str(monday),
                daily=change(quotes[-2] if len(quotes) > 1 else None),
                weekly=change(before[-1] if before else None, weekly=True))
