from __future__ import annotations

import calendar
import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, getcontext
from zoneinfo import ZoneInfo

getcontext().prec = 34
ZERO = Decimal('0')
ONE = Decimal('1')
CURRENCIES = ('CNY', 'HKD', 'USD')
ZONE = ZoneInfo('Asia/Shanghai')


class DomainError(ValueError):
    pass


class ConflictError(DomainError):
    """The caller's read/preview no longer describes the current ledger."""
    pass


def today() -> date:
    return datetime.now(ZONE).date()


def now() -> str:
    return datetime.now(ZONE).isoformat(timespec='seconds')


def dec(value, field='金额', *, positive=False, nonnegative=False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise DomainError(f'{field}必须是有效数字') from None
    if not result.is_finite() or abs(result) > Decimal('1e18'):
        raise DomainError(f'{field}不是可接受的有限数字')
    if positive and result <= 0:
        raise DomainError(f'{field}必须大于0')
    if nonnegative and result < 0:
        raise DomainError(f'{field}不能为负数')
    return result


def ds(value) -> str:
    v = dec(value)
    if v == 0:
        return '0'
    return format(v.normalize(), 'f')


def day(value, field='日期') -> date:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise DomainError(f'{field}须为YYYY-MM-DD') from None


def currency(value):
    if value not in CURRENCIES:
        raise DomainError('仅支持CNY、HKD、USD')
    return value


def month_after(value: date, count: int) -> date:
    year = value.year + (value.month - 1 + count) // 12
    month = (value.month - 1 + count) % 12 + 1
    return value.replace(year=year, month=month,
                         day=min(value.day, calendar.monthrange(year, month)[1]))


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def wire(value):
    if isinstance(value, Decimal):
        return ds(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    return value


REGIONS = ('A股', '美股', '港股', '海外其他', '混合', '未分类')


def canonical_region(value):
    # Display/read compatibility only; never infer investment exposure from listing market.
    return {'国内': 'A股', '海外': '海外其他'}.get(value, value) if value else '未分类'


def tags(payload):
    asset = payload.get('asset_class') or '未分类'
    region = canonical_region(payload.get('region'))
    if asset not in ('权益', '债券', '其他', '未分类'):
        raise DomainError('资产类别须为权益、债券、其他或未分类')
    if region not in REGIONS:
        raise DomainError('地区须为A股、美股、港股、海外其他、混合或未分类')
    themes = payload.get('tags', [])
    if isinstance(themes, str):
        themes = themes.replace('，', ',').replace('|', ',').split(',')
    if not isinstance(themes, list):
        raise DomainError('标签须为数组或逗号分隔文字')
    themes = list(dict.fromkeys(str(t).strip() for t in themes if str(t).strip()))
    if len(themes) > 24 or any(len(t) > 30 for t in themes):
        raise DomainError('最多24个主题标签，每个不超过30字')
    return {'asset_class': asset, 'region': region, 'tags': themes}


def ratio(numerator, denominator):
    return numerator / denominator if denominator and denominator > 0 else None
