"""Comparison of source references, separate from the confirmed ledger facts."""
from decimal import Decimal

from .common import dec, ds


# Both caps must hold, measured from the last reviewed value (never a rolling
# source value). Only Yahoo's reference rate gets this tolerance; quantities,
# dates, currency and split restoration factors never do.
YAHOO_RELATIVE_TOLERANCE = Decimal('0.0001')  # 0.01%, one basis point
YAHOO_PER_SHARE_TOLERANCE = Decimal('0.0001')
DATE_FIELDS = ('record_date', 'notice_record_date', 'pay_date')
CHANGE_LABELS = dict(eligible_quantity='权益份额变化', record_date='权益计算日期变化',
                     notice_record_date='来源登记日变化', pay_date='来源派息日变化',
                     ex_date='除息日变化', currency='派息币种变化',
                     split_restore_factor='来源拆分还原因子变化', per_share='每份分红实质变化',
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


def review_changes(before, after):
    if not before:
        return ['missing_basis']
    changes = []
    for key in ('eligible_quantity', 'record_date', 'notice_record_date', 'pay_date', 'ex_date', 'currency', 'split_restore_factor'):
        if key not in before or after.get(key) is None:
            continue
        old, new = before[key], after[key]
        if key in ('eligible_quantity', 'split_restore_factor') and old is not None:
            equal = dec(old) == dec(new)
        else:
            equal = old == new
        if not equal:
            changes.append(key)
    if dec(before['per_share']) != dec(after['per_share']) and not minor_yahoo_change(before, after):
        changes.append('per_share')
    return changes
