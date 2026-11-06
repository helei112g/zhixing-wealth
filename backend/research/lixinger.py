"""Lixinger's documented index contracts. No credentials, networking or UI state here."""
import calendar
import hashlib
import re
from datetime import timedelta
from ..common import DomainError, day, dec, ds, json_dumps
from .metrics import years_before

SOURCE = '理杏仁'
VOLATILITY_DAYS = 250
METHODS = ('mcw', 'ew', 'ewpvo', 'median')
METRICS = {'pe': 'pe_ttm', 'pb': 'pb', 'ps': 'ps_ttm', 'dividend_yield': 'dyr'}
ACTIVITY = ('mc', 'cmc', 'ecmc', 'fet_as_ma', 'fet_snif_ma', 'to_r', 'cp', 'r_cp')
MODULES = ('index_info', 'price', 'total_return', 'valuations', 'memberships',
           'historical_memberships', 'index_financials', 'market_activity',
           'stock_valuations', 'financials', 'historical_financials')
DEFAULT_MODULES = ('index_info', 'price', 'total_return', 'valuations', 'memberships')
MODULE_SOURCES = {
    'index_info': '理杏仁 · 指数信息',
    'price': '理杏仁 · 日线与250日年化波动率；已支持的跨市场指数使用编制机构官方数据',
    'total_return': '理杏仁 · 全收益日线；已支持的跨市场指数使用编制机构官方数据',
    'valuations': '理杏仁 · 基础数据（估值、场内基金金额、市值）',
    'memberships': '理杏仁 · 成分与权重；已支持的跨市场指数使用编制机构官方名单',
    'historical_memberships': '理杏仁 · 历史成分与权重（最多近十年）',
    'index_financials': '理杏仁 · 指数财报（按指数类型选择财报端点）',
    'market_activity': '理杏仁 · 融资融券、互联互通',
    'stock_valuations': '东方财富 · 指定交易日公司历史估值',
    'financials': '东方财富 · 公司利润表、现金流量表',
    'historical_financials': '理杏仁历史名单 + 东方财富公司财报及历史行业',
}
FINANCIAL_FIELDS = {
    'revenue': 'ps.oi', 'profit': 'ps.npatoshopc', 'adjusted_profit': 'ps.npadnrpatoshaopc',
    'operating_cf': 'cfs.ncffoa', 'assets': 'bs.ta', 'liabilities': 'bs.tl', 'equity': 'bs.toe', 'goodwill': 'bs.gw',
}
FINANCIAL_RATIO_FIELDS = ('m.roe', 'm.roa', 'm.np_s_r', 'bs.gw_toe_r', 'bs.tl_ta_r')
FINANCIAL_METRICS = tuple(
    f'q.{field}.{expression}' for field in FINANCIAL_FIELDS.values()
    for expression in (('t', 't_r', 't_y2y') if field.startswith('bs.') else ('t', 't_r', 't_y2y', 'ttm', 'ttm_y2y', 'c', 'c_y2y'))
) + tuple(f'q.{field}.{expression}' for field in FINANCIAL_RATIO_FIELDS for expression in ('t', 't_r'))
MARGIN_RATIO_FIELDS = ('financingBalanceToMarketCap', 'securitiesBalanceToMarketCap')


def numeric(v):
    return None if v is None else ds(dec(v))


def iso(v):
    return str(day(str(v)[:10]))


def name(v):
    return str(v.get('cmn_hans_cn') or v.get('en') or '') if isinstance(v, dict) else str(v or '')


def periods(asof, launch):
    """Completed calendar quarter ends only, capped at the last forty quarters."""
    end = day(asof)
    values = [f'{y}-{m:02d}-{calendar.monthrange(y, m)[1]}'
              for y in range(end.year-11, end.year+1) for m in (3, 6, 9, 12)]
    return [p for p in values if launch <= p < asof][-40:]


def ranges(start, end):
    cursor = day(start); stop = day(end)
    while cursor <= stop:
        # Under the documented ten-year ceiling, including leap years.
        finish = min(cursor + timedelta(days=3650), stop)
        yield str(cursor), str(finish)
        cursor = finish + timedelta(days=1)


def request_plan(profile, module, start, end, info=None):
    code = profile['code']; base = dict(stockCode=code)
    if profile.get('exposure') == 'overseas':
        raise DomainError('该跨市场指数的理杏仁地区、权限与财务币种尚未核验；保留原有快照')
    if module == 'index_info':
        return [dict(endpoint='cn/index', params=dict(stockCodes=[code]), kind='info')]
    if module in ('memberships', 'historical_memberships'):
        ends = periods(end, max(start, profile['launch'], str(years_before(end, 10)))) if module == 'historical_memberships' else [str(day(end).replace(day=1)-timedelta(days=1))]
        calls = []
        for p in ends:
            # A short interval includes the actual last exchange session and holiday boundaries.
            lo = str(day(p)-timedelta(days=40 if module == 'memberships' else 10))
            calls.append(dict(endpoint='cn/index/constituent-weightings', params=dict(base, startDate=lo, endDate=p), kind='weights', period=p))
            # Resolve the actual membership query date from validated weights before executing.
            calls.append(dict(endpoint='cn/index/constituents', params=dict(stockCodes=[code], date=p), kind='members', period=p))
        return calls
    if module == 'index_financials':
        typ = (info or {}).get('fsTableType')
        if typ not in ('hybrid', 'non_financial', 'bank', 'security'):
            raise DomainError('请先采集指数基本信息并确认受支持的财报类型；不猜测财报端点')
        # The ordinary-shareholder fields are common to these documented financial types.
        return [dict(endpoint='cn/index/fs/'+typ, params=dict(stockCodes=[code], startDate=a, endDate=b, metricsList=list(FINANCIAL_METRICS)), kind='finance') for a, b in ranges(start, end)]
    calls = []
    for a, b in ranges(start, end):
        args = dict(base, startDate=a, endDate=b)
        if module in ('price', 'total_return'):
            calls.append(dict(endpoint='cn/index/candlestick', params=dict(args, type='normal' if module == 'price' else 'total_return'), kind='prices'))
        elif module == 'volatility':
            calls.append(dict(endpoint='cn/index/volatility', params=dict(args, volatilityDays=VOLATILITY_DAYS), kind='volatility'))
        elif module == 'valuations':
            fields = [v+'.'+m for v in METRICS.values() for m in METHODS] + list(ACTIVITY)
            calls.append(dict(endpoint='cn/index/fundamental', params=dict(stockCodes=[code], startDate=a, endDate=b, metricsList=fields), kind='fundamental'))
        elif module == 'market_activity':
            calls.extend([dict(endpoint='cn/index/margin-trading-and-securities-lending', params=args, kind='margin'),
                          dict(endpoint='cn/index/mutual-market', params=args, kind='northbound')])
        else:
            raise DomainError('该模块需要已核验的公司级来源')
    return calls


def security(row):
    code = str(row['stockCode']).upper(); area = row.get('areaCode', 'cn')
    if area == 'cn' and re.fullmatch(r'\d{6}', code):
        market = 'SH' if code.startswith('6') else 'BJ' if code.startswith(('4', '8', '9')) else 'SZ'
    else:
        raise DomainError('成分市场身份尚未核验；拒绝按A股规则解析跨市场证券')
    return dict(code=code, market=market, name=name(row.get('stockName')), industry='', weight=None)


def normalize(call, rows, profile):
    """Select only documented fields; raw provider messages never reach disk or logs."""
    if not isinstance(rows, list): raise DomainError('理杏仁数据结构不符')
    args = call['params']; kind = call['kind']; result = []
    for row in rows:
        if not isinstance(row, dict): raise DomainError('理杏仁记录结构不符')
        if kind in ('info', 'members', 'fundamental', 'finance') and row.get('stockCode') != profile['code']:
            raise DomainError('理杏仁返回的指数身份与请求不符')
        if kind == 'info':
            if row.get('currency') != 'CNY' or row.get('source') != profile['provider'] or row.get('areaCode') != 'cn':
                raise DomainError('理杏仁指数地区、币种或编制方与档案不符')
            result.append({k: row.get(k) for k in ('stockCode', 'name', 'areaCode', 'market', 'source', 'currency', 'fsTableType', 'launchDate', 'rebalancingFrequency', 'calculationMethod')})
        elif kind == 'members':
            result = [security(v) for v in row.get('constituents', [])]
        else:
            d = iso(row['date'])
            if not args['startDate'] <= d <= args['endDate']: raise DomainError('理杏仁返回日期超出显式请求范围')
            if kind == 'prices': result.append(dict(date=d, close=ds(dec(row['close'], positive=True))))
            elif kind == 'volatility':
                if row.get('stockCode', profile['code']) != profile['code']:
                    raise DomainError('理杏仁波动率的指数身份与请求不符')
                result.append(dict(date=d, value=ds(dec(row.get('value'), nonnegative=True))))
            elif kind == 'fundamental': result.append(dict(date=d, **{k: numeric(row.get(k)) for k in args['metricsList']}))
            elif kind == 'weights': result.append(dict(security(row), date=d, weight=ds(dec(row['weighting'], nonnegative=True)*100)))
            elif kind == 'finance':
                if row.get('currency') != 'CNY': raise DomainError('指数财报币种不符，拒绝混币种汇总')
                values = {}
                for key in args['metricsList']:
                    v = row
                    for part in key.split('.'): v = v.get(part) if isinstance(v, dict) else None
                    values[key] = numeric(v)
                result.append(dict(date=d, currency='CNY', published_at=iso(row['reportDate']) if row.get('reportDate') else None,
                                   report_type=row.get('reportType'), values=values))
            else:
                fields = ('financingPurchaseAmount', 'financingRepaymentAmount', 'financingNetPurchaseAmount', 'financingBalance', 'securitiesSellAmount', 'securitiesRepaymentAmount', 'securitiesNetSellAmount', 'securitiesBalance', *MARGIN_RATIO_FIELDS) if kind == 'margin' else ('shareholdingsMoney', 'netBuyAmount', 'shareholdingsMoneyToMarketCap')
                result.append(dict(date=d, **{k: numeric(row.get(k)) for k in fields}))
    if kind in ('info',) and len(result) != 1: raise DomainError('指数基础信息不唯一或未覆盖')
    if kind == 'members' and len({(r['market'], r['code']) for r in result}) != len(result):
        raise DomainError('成分名单身份重复')
    keys = [(r.get('date'), r.get('code')) if kind == 'weights' else r.get('date') for r in result] if kind not in ('members', 'info') else []
    if len(keys) != len(set(keys)): raise DomainError('理杏仁观测日期或证券身份重复')
    return sorted(result, key=lambda r: (r.get('date', ''), r.get('code', '')))


def full_weights(rows, period, latest=False):
    groups = {}
    for r in rows: groups.setdefault(r['date'], []).append(r)
    valid = {d: group for d, group in groups.items() if (d<=period if latest else d[:7] == period[:7]) and abs(sum(dec(r['weight']) for r in group)-100) <= dec('.5')}
    return valid[max(valid)] if valid else []


def assemble(profile, module, parts, end):
    evidence = [dict(url='https://www.lixinger.com/api/open-api/html-doc/'+p['call']['endpoint'],
                     sha256=hashlib.sha256(json_dumps(p['rows']).encode()).hexdigest(), parameters=p['call']['params']) for p in parts]
    common = dict(source=SOURCE, provider_id='lixinger', evidence=evidence, effective_date=end)
    if module in ('price', 'total_return'):
        rows = [r for p in parts for r in p['rows'] if r['date'] >= profile['launch']]
        if not rows: raise DomainError('该范围内没有有效行情；不以另一口径替代')
        return dict(common, rows=rows, effective_date=max(r['date'] for r in rows), basis=module,
                    series_code=profile['code'] if module == 'price' else profile['return_code'])
    if module == 'volatility':
        rows = [r for part in parts for r in part['rows'] if r['date'] >= profile['launch']]
        if not rows:
            raise DomainError('该范围没有年化波动率观测，保留旧快照')
        return dict(common, rows=rows, effective_date=rows[-1]['date'], series_code=profile['code'],
                    sample_days=VOLATILITY_DAYS, methodology_note='理杏仁按指数代码提供的250日年化波动率；接口没有全收益口径参数')
    if module == 'index_info': return dict(common, info=parts[0]['rows'][0])
    if module == 'valuations':
        rows = [r for p in parts for r in p['rows'] if r['date'] >= profile['launch']]
        if not rows: raise DomainError('该范围没有估值观测；保留旧版本')
        series = {metric: {m: dict(field=field+'.'+m, source=SOURCE, frequency='daily',
                    rows=[dict(date=r['date'], value=r[field+'.'+m]) for r in rows if r.get(field+'.'+m) is not None]) for m in METHODS} for metric, field in METRICS.items()}
        if not all(series[m]['mcw']['rows'] for m in ('pe','pb')):raise DomainError('PE/PB主方法未完整取得，保留旧版本')
        return dict(common, effective_date=max(r['date'] for r in rows), series=series,
                    activity_rows=[{k: r.get(k) for k in ('date', *ACTIVITY)} for r in rows],
                    mapping_verified=False, methodology_note='供应商各估值方法独立保存；mcw与条件点位分母关系待认证核验，试算暂不开放')
    if module == 'market_activity':
        return dict(common, effective_date=max((r['date'] for p in parts for r in p['rows']),default=None),
                    series={k: [r for p in parts if p['call']['kind'] == k for r in p['rows']] for k in ('margin', 'northbound')},
                    margin_ratio_fields=list(MARGIN_RATIO_FIELDS))
    if module == 'index_financials':
        rows = [r for p in parts for r in p['rows'] if not r['published_at'] or r['published_at'] <= end]
        return dict(common, rows=rows, effective_date=max((r['date'] for r in rows),default=None), metrics=list(FINANCIAL_METRICS),methodology_note='历史当期成分汇总；供应商同篮子同比与两期独立总量之差分开，当前重述版本，不是严格PIT')
    baskets = []
    for weight_part, member_part in zip(parts[::2], parts[1::2]):
        period = weight_part['call']['period']; weights = full_weights(weight_part['rows'], period, module=='memberships')
        members = member_part['rows']; by_code = {r['code']: r for r in weights}
        actual = member_part['call']['params']['date']
        complete = bool(weights and {r['code'] for r in members} == set(by_code) and actual == weights[0]['date'])
        conflict=bool(weights and members and not complete)
        if conflict and module=='memberships':raise DomainError('同日成分与全量权重集合冲突；快照未发布')
        rows = [dict(r, weight=by_code[r['code']]['weight'] if complete else None) for r in members]
        baskets.append(dict(period=period, requested_date=actual, effective_date=weights[0]['date'] if complete else None,
                            weight_date=weights[0]['date'] if complete else None, weights_complete=complete,
                            status='conflict' if conflict else 'checked' if complete else 'list_only' if members else 'unavailable', members=[] if conflict else rows,
                            reason='名单与同日权重冲突，该期隔离' if conflict else None if complete else '供应商未覆盖该期名单' if not members else '供应商返回名单；生效日未独立证实、缺全量同日权重'))
    if module == 'memberships':
        b = baskets[0]
        return dict(common, rows=b['members'], effective_date=b['effective_date'], requested_date=b['requested_date'], weights_complete=b['weights_complete'], limitation=b['reason'])
    coverage=[]
    for year in sorted({b['period'][:4] for b in baskets}):
        same=[b for b in baskets if b['period'].startswith(year)]
        known={b['period'][5:7] for b in same if b['members']}
        grain='quarterly' if len(known)==4 else 'semiannual' if {'06','12'}<=known else 'annual' if '12' in known else 'partial'
        coverage.append(dict(year=year,grain=grain,known_quarters=len(known),expected_quarters=len(same)))
    return dict(common, effective_date=max((b['effective_date'] or b['requested_date'] for b in baskets if b['members']),default=None),baskets=baskets, comparison_coverage=coverage)
