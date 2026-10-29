"""Read-only causal deletion plans from the original accounting replay.

Shared distributions and sales are indivisible records: list them explicitly,
never silently scale their amounts or delete their other purchase parents.
"""
from collections import defaultdict

from .common import DomainError
from .ledger import Ledger
from .trade_analytics import dividend_entitlement


def deletion_plan(state, event_id):
    events = {e['id']: e for e in state['events']}
    if event_id not in events: raise DomainError('流水不存在或已删除')
    parents = defaultdict(set)
    transfers = defaultdict(set)
    for e in events.values():
        if e.get('transfer_id'): transfers[e['transfer_id']].add(e['id'])
        if e.get('related_event_id') in events:
            ref = events[e['related_event_id']]
            if ref['portfolio_id'] == e['portfolio_id']: parents[e['id']].add(ref['id'])
    for members in transfers.values():
        for eid in members: parents[eid].update(members - {eid})
    scope = {events[event_id]['portfolio_id']}
    if events[event_id].get('transfer_id'):
        scope.update(events[eid]['portfolio_id'] for eid in transfers[events[event_id]['transfer_id']])
    for portfolio in state['portfolios']:
        if portfolio['id'] not in scope: continue
        ledger = Ledger(state, portfolio)
        lot_splits = defaultdict(list)
        for op in ledger.ops:
            d, _, _, eid, kind, e = op
            if kind in ('SELL', 'REDEEM_PENDING', 'SPLIT', 'ADJUSTMENT'):
                before = {r['event_id']: r['quantity'] for r in ledger.position(e['instrument_id'])['lots'] if r['quantity'] > 0}
                ledger.apply(op)
                after = {r['event_id']: r['quantity'] for r in ledger.position(e['instrument_id'])['lots']}
                affected = [bid for bid, quantity in before.items()
                            if kind in ('SPLIT', 'ADJUSTMENT') or after.get(bid, 0) < quantity]
                for bid in affected:
                    parents[eid].add(bid)
                    parents[eid].update(sid for _, sid in lot_splits[bid])
                    if kind in ('SPLIT', 'ADJUSTMENT'): lot_splits[bid].append((d, eid))
            else:
                ledger.apply(op)
        for e in ledger.events:
            if e['kind'] not in ('DIVIDEND', 'REINVEST') or e.get('instrument_id') not in ledger.instruments: continue
            cutoff, owned = dividend_entitlement(state, portfolio, e)
            for lot in owned:
                bid = lot['event_id']
                parents[e['id']].add(bid)
                parents[e['id']].update(sid for d, sid in lot_splits[bid] if d <= cutoff)
    children = defaultdict(set)
    for child, dependencies in parents.items():
        for parent in dependencies:
            if child != parent: children[parent].add(child)
    ids, pending = {event_id}, [event_id]
    while pending:
        for child in children[pending.pop()] - ids:
            ids.add(child); pending.append(child)
    instruments = {i['id']: i for i in state['instruments']}
    portfolios = {p['id']: p for p in state['portfolios']}
    items = []
    for eid in sorted(ids, key=lambda key: (events[key]['date'], events[key]['sequence'], key)):
        e = events[eid]
        external_lots = [bid for bid in parents[eid] - ids
                         if events[bid]['kind'] in ('BUY', 'OPENING_POSITION', 'REINVEST')]
        items.append({'event': e, 'instrument_name': instruments.get(e.get('instrument_id'), {}).get('name', '资金'),
                      'portfolio_name': portfolios[e['portfolio_id']]['name'], 'is_root': eid == event_id,
                      'dependency_ids': sorted(parents[eid] & ids),
                      'shared_with_other_lots': bool(external_lots) and (eid != event_id or len(external_lots) > 1),
                      'other_buy_event_ids': sorted(external_lots)})
    return {'event_id': event_id, 'revision': state['revision'], 'event_ids': sorted(ids), 'items': items,
            'total': len(ids), 'audit_preserved': True}
