"""Verified 930955 daily valuation source; CSI is an independent PE check only."""
from datetime import timedelta
from urllib.parse import urlencode
from .. import market
from ..common import day, dec, ds

API = 'https://ai2.jiucaishuo.com/api/index-valuation/'
CSI = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'
THRESHOLD = '0.1'
PB_REASON = '尚无已核验可接入的中证 PB 接口，PB 未校验'


def fetch(profile, code, asof, metrics, request):
    if profile['code'] != '930955' or code != '930955.CSI':
        raise market.ProviderError('韭圈儿估值身份尚未核验')
    try:
        detail, proof = request(API + 'detail?' + urlencode(dict(code=code)))
        header = detail.get('header') or {}
        if detail.get('na') or header.get('code') != code or header.get('name') != profile['name']:
            raise ValueError('身份不匹配')
        series, evidence = {}, [proof]
        for metric in metrics:
            obj, proof = request(API + 'history?' + urlencode(dict(code=code, metric=metric)))
            if obj.get('code') != code or obj.get('metric') != metric:
                raise ValueError('历史指标或代码不匹配')
            raw = obj.get('series')
            if not isinstance(raw, list) or not 2 <= len(raw) <= 12000:
                raise ValueError('历史观测缺失或超限')
            rows = []; dates = []
            for row in raw:
                d = day(row['date']).isoformat()
                if d != row['date']: raise ValueError('日期格式无效')
                dates.append(d)
                value = dec(row['value'])  # Reject missing/non-finite values, not zero-fill.
                if profile['launch'] <= d <= asof:
                    rows.append(dict(date=d, value=ds(value)))
            if dates != sorted(set(dates)): raise ValueError('日期重复或乱序')
            if not rows: raise ValueError('截止日之前无有效观测')
            dec(rows[-1]['value'], positive=True)
            series[metric] = dict(overall=dict(rows=rows, source='韭圈儿', field='pe_ttm' if metric == 'pe' else 'pb_lf',
                                               frequency='daily', allow_partial_window=True))
            evidence.append(proof)
        ends = {v['overall']['rows'][-1]['date'] for v in series.values()}
        if len(ends) != 1: raise ValueError('PE、PB 最新日期不一致')
    except Exception:
        raise market.ProviderError('韭圈儿估值请求失败或身份、指标、历史观测校验未通过；保留旧数据，请稍后重试') from None
    result = dict(series=series, evidence=evidence, effective_date=ends.pop(),
                  methodology_note='韭圈儿整体法 PE TTM、PB LF；亏损处理与加权细节未独立复原。使用当前可得历史版本，分位由本系统重算，不采用供应商分位字段。')
    if set(metrics) == {'pe', 'pb'}:
        reference, proof = csi_reference(profile, asof, request)
        result['cross_check'] = reference
        if proof: evidence.append(proof)
    return result


def csi_reference(profile, asof, request):
    # A bounded recent window permits same-date matching when sources update at different times.
    start = (day(asof) - timedelta(days=45)).strftime('%Y%m%d')
    url = CSI + '?' + urlencode(dict(indexCode=profile['code'], startDate=start, endDate=asof.replace('-', '')))
    reference = dict(source='中证指数', field='peg', metric='pe', threshold=THRESHOLD,
                     pb_reason=PB_REASON, url=url)
    try:
        obj, proof = request(url)
        raw = obj.get('data')
        if not isinstance(raw, list) or not 2 <= len(raw) <= 100: raise ValueError('官方样本缺失或超限')
        rows = []
        for r in raw:
            if r.get('indexCode') != profile['code']: raise ValueError('官方代码不匹配')
            d = day(str(r['tradeDate'])).isoformat()
            # Exclude query-start synthetic anchor, weekends and holiday carry-forward rows.
            if d.replace('-', '') <= start or d > asof or day(d).weekday() > 4: continue
            if dec(r.get('tradingVol', 0)) <= 0 or dec(r.get('tradingValue', 0)) <= 0: continue
            if r.get('peg') is not None and dec(r['peg']) > 0: rows.append(dict(date=d, value=ds(dec(r['peg']))))
        rows.sort(key=lambda r:r['date'])
        if not rows or len({r['date'] for r in rows}) != len(rows): raise ValueError('官方有效日期缺失或重复')
        return dict(reference, status='available', rows=rows), proof
    except Exception:
        return dict(reference, status='unavailable', rows=[], reason='中证 PE 请求失败或有效观测不足，PE 未校验；韭圈儿估值仍可使用'), None


def compare(valuations, reference, method='overall'):
    if reference is None: return None
    pe = valuations.get('pe', {})
    result = dict(source='中证指数', field='peg', threshold=THRESHOLD, primary_source='韭圈儿',
                  pb=dict(status='unavailable', reason=PB_REASON),
                  pe=dict(status='unavailable', date=pe.get('date'), primary=pe.get('current')))
    check = result['pe']
    if method != 'overall':
        check['reason'] = '仅整体法 PE 参与中证同日交叉校验'; return result
    if reference.get('status') != 'available':
        check['reason'] = reference.get('reason', '中证 PE 未校验'); return result
    rows = reference.get('rows', [])
    official = next((r for r in rows if r['date'] == pe.get('date')), None)
    if not official or pe.get('current') is None or dec(pe['current']) <= 0:
        check.update(status='date_mismatch', reference_date=rows[-1]['date'] if rows else None,
                     reason='无同日有效 PE，未作差异判断'); return result
    difference = dec(pe['current']) / dec(official['value'], positive=True) - 1
    check.update(status='warning' if abs(difference) >= dec(THRESHOLD) else 'within_threshold',
                 official=official['value'], reference_date=official['date'], relative_difference=ds(difference))
    return result
