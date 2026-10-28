"""Read-only plan funding checks. Plans never reserve or move cash."""
from .common import ZERO, DomainError, dec


def plan_percentage(value):
    pct = dec(value or 0, '计划比例', nonnegative=True)
    if pct > 100:
        raise DomainError('单个标的计划比例须在0至100%之间')
    return pct


def plan_funding(snapshot, *, instrument_id=None, target_pct=None):
    budget = dec(snapshot['portfolio']['planned_amount'])
    available = snapshot['available_cash']
    required = ZERO; total_pct = ZERO; unknown = []
    holdings = list(snapshot['holdings'])
    if instrument_id and not any(h['instrument_id'] == instrument_id for h in holdings):
        holdings.append({'instrument_id': instrument_id, 'target_pct': ZERO, 'net_invested_base': ZERO})
    for holding in holdings:
        pct = target_pct if holding['instrument_id'] == instrument_id else dec(holding['target_pct'])
        total_pct += pct
        if pct <= 0:
            continue
        net = holding['net_invested_base']
        if net is None:
            unknown.append(holding['instrument_id'])
        else:
            # An overfunded holding does not make that money available elsewhere.
            required += max(ZERO, budget * pct / 100 - dec(net))
    complete = not unknown and available is not None
    gap = max(ZERO, required - available) if complete else None
    surplus = max(ZERO, available - required) if complete else None
    return {'currency': 'CNY', 'status': 'unknown' if not complete else 'shortfall' if gap > 0 else 'surplus' if surplus > 0 else 'balanced',
            'available_amount': available, 'required_amount': required if complete else None,
            'known_required_amount': required, 'shortfall': gap, 'surplus': surplus,
            'total_target_pct': total_pct, 'unknown_instrument_ids': unknown,
            'basis': '剩余计划需投入＝各已设置计划标的 max(计划金额−净投入, 0) 之和；已超投不抵扣其他标的需求，未到账回款不计可投金额。'}
