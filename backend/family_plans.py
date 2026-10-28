"""Payment forecasts, separate from balances and actual cash movements."""
import calendar
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from .common import DomainError, day, dec, ds, json_dumps


def shifted_date(first, months):
    d = day(first); index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12); month += 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def payment_plan(raw, category, balance=None, previous=None):
    if not isinstance(raw, dict): raise DomainError('缴费或还款设置无效')
    allowed = {'mode', 'amount', 'first_due', 'periods', 'annual_rate'}
    if set(raw) - allowed: raise DomainError('缴费或还款设置包含未知字段')
    mode = raw.get('mode', 'none')
    modes = ('none', 'once', 'annual') if category == 'insurance' else ('none', 'once', 'fixed', 'annuity', 'equal_principal') if category == 'debt' else ('none',)
    if mode not in modes: raise DomainError('所选类别不支持这种付款方式')
    if mode == 'none': return {'mode': 'none'}
    first = str(day(raw.get('first_due'), '首次付款日期'))
    n = raw.get('periods', 1)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 600: raise DomainError('期数须为1至600的整数')
    if mode == 'once': n = 1
    if category == 'insurance' and n > 100: raise DomainError('缴费年数不能超过100年')
    try: shifted_date(first, (n - 1) * (12 if mode == 'annual' else 1))
    except (ValueError, OverflowError): raise DomainError('付款日期超出支持范围') from None
    plan = dict(mode=mode, first_due=first, periods=n)
    if mode in ('fixed', 'annual') or (mode == 'once' and category == 'insurance'):
        plan['amount'] = ds(dec(raw.get('amount'), '每次付款金额', positive=True))
    else:
        # Keep the original calculation base when only changing unrelated metadata.
        rate = dec(raw.get('annual_rate', '0'), '年利率', nonnegative=True)
        if rate > 100: raise DomainError('年利率不能超过100%')
        if mode != 'once': plan['annual_rate'] = ds(rate)
        comparable = {k:v for k,v in (previous or {}).items() if k != 'principal'}
        if comparable == plan and previous.get('principal') is not None: plan['principal'] = previous['principal']
        else: plan['principal'] = ds(dec(balance, '用于计算的当前欠款', positive=True))
    return plan


def installments(plan, item_id, name, currency, start, end):
    if not plan or plan.get('mode') == 'none': return []
    mode, n = plan['mode'], plan['periods']; rows = []
    principal = dec(plan.get('principal', '0')); remaining = principal
    rate = dec(plan.get('annual_rate', '0')) / 1200
    regular = principal / n if not rate else principal * rate / (1 - (1 + rate) ** (-n))
    fingerprint = hashlib.sha256(json_dumps(plan).encode()).hexdigest()[:16]
    for i in range(n):
        due = str(shifted_date(plan['first_due'], i * (12 if mode == 'annual' else 1)))
        if mode == 'annuity':
            interest = (remaining * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            amount = regular.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            repaid = min(remaining, amount - interest) if i < n-1 else remaining
            amount = repaid + interest; remaining -= repaid
        elif mode == 'equal_principal':
            interest = (remaining * rate).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            repaid = min(remaining, (principal/n).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if i < n-1 else remaining
            amount = repaid + interest; remaining -= repaid
        else: amount = dec(plan.get('amount', plan.get('principal')))
        if start <= due <= end:
            identity = hashlib.sha256(f'{item_id}:{fingerprint}:{due}'.encode()).hexdigest()
            rows.append(dict(id=identity, item_id=item_id, name=name, currency=currency, share='1',
                             kind='premium' if mode == 'annual' or 'amount' in plan and mode == 'once' else 'debt',
                             due_date=due, amount=ds(amount), household_amount=ds(amount), status='pending',
                             days=(day(due)-day(start)).days, version=1, generated=True, period=i+1, periods=n,
                             estimated=mode in ('annuity','equal_principal')))
    return rows
