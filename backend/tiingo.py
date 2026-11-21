"""Independent US EOD prices and cash distributions from the same daily series."""
from collections import OrderedDict
from datetime import datetime, timedelta
import os
import subprocess
import sys
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from . import market
from .common import day, dec, ds

BASE = 'https://api.tiingo.com/tiingo/daily/'
ZONE = ZoneInfo('America/New_York')
_CACHE = OrderedDict()
_BLOCKED_UNTIL = 0
US_EXCHANGES = {'NASDAQ', 'NYSE', 'NYSE ARCA', 'NYSEARCA', 'NYSE MKT', 'NYSE AMERICAN',
                'AMEX', 'BATS', 'CBOE', 'ARCA', 'IEX', 'PINK', 'OTC', 'NYSE TEXAS'}


def api_token():
    token = os.environ.get('TIINGO_API_TOKEN', '').strip()
    if not token and sys.platform == 'darwin':
        try:
            found = subprocess.run(
                ['/usr/bin/security', 'find-generic-password', '-s', 'zhixing-wealth-tiingo', '-w'],
                capture_output=True, text=True, timeout=5, check=False)
            if found.returncode == 0:
                token = found.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not token:
        raise market.ProviderError('未配置 Tiingo Token；可设置 TIINGO_API_TOKEN 或 macOS 钥匙串项目 zhixing-wealth-tiingo')
    return token


def request(path, params, token):
    global _BLOCKED_UNTIL
    try:
        with requests.get(BASE + path, params=params, headers={'Authorization': 'Token ' + token},
                          timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code == 429:
                _BLOCKED_UNTIL = time.monotonic() + 3600
            if response.status_code != 200:
                raise market.ProviderHTTPError(response.status_code)
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise market.ProviderError('Tiingo 日线响应超过大小限制')
        return market.parse_json(body.decode('utf-8'))
    except requests.RequestException:
        raise market.ProviderError('Tiingo 网络请求失败，请稍后重试') from None


def history(inst):
    if inst['market'] != 'US' or inst['currency'] != 'USD':
        raise market.ProviderError('Tiingo 备用源仅支持美元美股')
    code = inst['code'].upper().replace('.', '-')
    current = datetime.now(ZONE).date()
    cached = _CACHE.get(code)
    if cached and cached[0] == current and time.monotonic() < cached[1]:
        return cached[2]
    if time.monotonic() < _BLOCKED_UNTIL:
        raise market.ProviderError('Tiingo HTTP 429，正在等待额度恢复')
    token = api_token()
    path = quote(code, safe='')
    meta = request(path, {}, token)
    if (not isinstance(meta, dict) or meta.get('ticker') != code
            or str(meta.get('exchangeCode')).upper() not in US_EXCHANGES
            or meta.get('currencyCode', 'USD') != 'USD'):
        raise market.ProviderError('Tiingo 代码、交易所或币种与标的不符')
    first, last = day(meta.get('startDate')), day(meta.get('endDate'))
    if last < current - timedelta(days=7) or last > current or first > last:
        raise market.ProviderError('Tiingo 元数据的行情覆盖日期异常或已过期')
    records = request(path + '/prices', {
        'startDate': first.isoformat(), 'endDate': last.isoformat(),
        'resampleFreq': 'daily', 'columns': 'date,close,adjClose,splitFactor,divCash',
    }, token)
    if not isinstance(records, list) or not records:
        raise market.ProviderError('Tiingo 未返回日线')
    daily = []
    for record in records:
        # Tiingo labels the trading day at UTC midnight. A timezone conversion
        # here would move US sessions onto the previous calendar date.
        trade_date = day(record['date'].split('T')[0]).isoformat()
        if daily and trade_date <= daily[-1][0]:
            raise market.ProviderError('Tiingo 日线日期重复或乱序')
        daily.append((trade_date, ds(dec(record['close'], positive=True)),
                      ds(dec(record['adjClose'], positive=True)), ds(dec(record['splitFactor'], positive=True)),
                      ds(dec(record['divCash'], nonnegative=True))))
    if daily[0][0] != str(first) or daily[-1][0] != str(last):
        raise market.ProviderError('Tiingo 响应未覆盖声明的历史范围')
    result = (meta.get('name'), daily)
    _CACHE[code] = (current, time.monotonic() + 600, result)
    _CACHE.move_to_end(code)
    if len(_CACHE) > 16:
        _CACHE.popitem(last=False)
    return result


def quotes(inst, start, end, adjusted_start=None):
    name, daily = history(inst)
    current = datetime.now(ZONE).date().isoformat()
    rows, actions = [], []
    for date, close, adj_close, split, _ in daily:
        if not (adjusted_start or start) <= date <= min(current, end):
            continue
        if split != '1':
            actions.append(dict(date=date, factor=split, kind='split_notice', source='Tiingo·拆股因子',
                                source_url='https://www.tiingo.com/ticker/' + quote(inst['code'], safe='')))
        if date == current:
            continue
        if date >= start:
            rows.append(market.quote_record(date, close, 'Tiingo·名义收盘'))
        rows.append(market.quote_record(date, adj_close, 'Tiingo·复权参考', 'adjusted'))
    if not any(row['kind'] == 'raw' for row in rows):
        raise market.ProviderError('该区间没有已结束交易日的名义收盘价')
    return dict(rows=rows, actions=actions, name=name, source='Tiingo EOD',
                notes=['按美国交易日归日，名义收盘价用于估值，复权收盘价仅供参考'])


def dividends(inst):
    _, daily = history(inst)
    code = inst['code']
    return [dict(code=code, source_ref=f'cash:US:{code}:{date}:USD', record_date=None, ex_date=date,
                 pay_date=None, per_share=cash, currency='USD', source='Tiingo·现金分派参考',
                 source_url='https://www.tiingo.com/ticker/' + quote(code, safe=''),
                 original_rate=cash, original_unit='每股USD', status='published',
                 coverage_note='交易日现金分派字段提供除息日及每股金额，到账日和税费按凭证核对。')
            for date, _, _, _, cash in reversed(daily) if cash != '0']
