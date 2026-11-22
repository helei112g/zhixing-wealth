"""Comparison of source references, separate from the confirmed ledger facts."""
from decimal import Decimal, ROUND_HALF_UP

from .common import dec, ds


# Both caps must hold, measured from the last reviewed value (never a rolling
# source value). A sub-cent provider reference is not a new receipt fact.
# Only Yahoo's reference rate gets this tolerance; quantities, dates, currency
# and split restoration factors never do. Confirmed ledger amounts stay exact.
YAHOO_RELATIVE_TOLERANCE = Decimal('0.0001')  # 0.01%, one basis point
YAHOO_PER_SHARE_TOLERANCE = Decimal('0.005')  # half a currency cent per share
DATE_FIELDS = ('record_date', 'notice_record_date', 'pay_date')
CHANGE_LABELS = dict(eligible_quantity='权益份额变化', record_date='权益计算日期变化',
                     notice_record_date='来源登记日变化', pay_date='来源派息日变化',
                     ex_date='除息日变化', currency='派息币种变化',
                     split_restore_factor='来源拆分还原因子变化', per_share='每份分红实质变化',
                     event_date='原流水发生日期变化',
                     missing_basis='原记录缺少核对依据')


def reference_basis(notice, quantity, cutoff, baseline=None):
    basis = dict(eligible_quantity=quantity, per_share=notice['per_share'], record_date=cutoff,
                 notice_record_date=notice.get('record_date'), pay_date=notice.get('pay_date'),
                 ex_date=notice['ex_date'], currency=notice['currency'], source=notice['source'],
                 split_restore_factor=notice.get('split_restore_factor'))
    # A missing source field is not a correction of previously reviewed facts.
    for key in (*DATE_FIELDS, 'split_restore_factor'):
        if basis[key] is None and baseline:
            basis[key] = baseline.get(key)
    if basis['split_restore_factor'] is not None:
        basis['split_restore_factor'] = ds(dec(basis['split_restore_factor']))
    return basis


def minor_yahoo_change(before, after):
    if not after.get('source', '').startswith('Yahoo'):
        return False
    # Legacy bases did not store the provider; require explicit review once if
    # the rate changed. Never assume that an unknown old provider was Yahoo.
    if not before.get('source', '').startswith('Yahoo'):
        return False
    old, new = dec(before['per_share']), dec(after['per_share'])
    delta = abs(new - old)
    return old > 0 and delta <= YAHOO_PER_SHARE_TOLERANCE and delta <= old * YAHOO_RELATIVE_TOLERANCE


def us_yahoo_source_switch(before, after):
    # Yahoo publishes some US distributions rounded to a thousandth of a dollar.
    # Compare the two observations at that precision only across a known source switch.
    yahoo, detailed = (before, after) if before.get('source') == 'Yahoo现金分派参考' else (after, before)
    return (yahoo.get('source') == 'Yahoo现金分派参考' and yahoo.get('currency') == 'USD'
            and detailed.get('currency') == 'USD' and detailed.get('source') in {
                'Tiingo·现金分派参考', 'Nasdaq现金派息表', 'State Street·ETF分派表', 'Vanguard·ETF分派表'})


def same_us_payout_precision(before, after):
    yahoo, detailed = (before, after) if before.get('source') == 'Yahoo现金分派参考' else (after, before)
    # Split restoration can scale the published rounding unit. Do not infer a
    # conversion for such distributions.
    if any(dec(b.get('split_restore_factor') or '1') != 1 for b in (yahoo, detailed)):
        return False
    return dec(detailed['per_share']).quantize(Decimal('.001'), rounding=ROUND_HALF_UP) == dec(yahoo['per_share'])


def review_changes(before, after, *, keep_yahoo_hkd=False, us_market=False):
    if not before or 'eligible_quantity' not in before or before.get('per_share') is None:
        return ['missing_basis']
    changes = []
    us_switch = us_market and us_yahoo_source_switch(before, after)
    for key in ('eligible_quantity', 'record_date', 'notice_record_date', 'pay_date', 'ex_date', 'currency', 'split_restore_factor'):
        # Quantity is a computed entitlement, not an optional source field.
        # Losing a previously known quantity must keep the review open.
        if key not in before or (key!='eligible_quantity' and after.get(key) is None):
            continue
        old, new = before[key], after[key]
        if us_switch and key == 'split_restore_factor' and old is None and dec(new) == 1:
            continue
        if key in ('eligible_quantity', 'split_restore_factor') and old is not None and new is not None:
            equal = dec(old) == dec(new)
        else:
            equal = old == new
        if not equal:
            changes.append(key)
    yahoo_hkd_reference = keep_yahoo_hkd and all(
        basis.get('currency') == 'HKD' and basis.get('source', '').startswith('Yahoo')
        for basis in (before, after)
    )
    if dec(before['per_share']) != dec(after['per_share']) and not yahoo_hkd_reference and not minor_yahoo_change(before, after):
        if not (us_switch and same_us_payout_precision(before, after)):
            changes.append('per_share')
    return changes
