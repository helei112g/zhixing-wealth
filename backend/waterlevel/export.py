"""Standalone, script-free HTML from a frozen snapshot; never retrieves data."""
from html import escape


def cell(value, suffix=''):
    return '—' if value is None else escape(str(value)) + suffix


def chart(statistics):
    rows = statistics['chart']
    values = [float(row[key]) for row in rows for key in ('value', 'mean5', 'mean10') if row.get(key) is not None]
    if not values:
        return '<p class="muted">无可绘制数据</p>'
    low, high = min(values), max(values)
    span = high - low or 1
    paths = []
    for key, color in (('value', '#365f91'), ('mean5', '#b88453'), ('mean10', '#268979')):
        segments, segment = [], []
        for index, row in enumerate(rows):
            if row.get(key) is None:
                if segment:
                    segments.append(segment)
                segment = []
                continue
            x = 48 + index / max(1, len(rows) - 1) * 690
            y = 152 - (float(row[key]) - low) / span * 130
            segment.append(f'{x:.2f},{y:.2f}')
        if segment:
            segments.append(segment)
        paths.extend(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.5"/>' for points in segments)
    return (f'<svg viewBox="0 0 760 186" role="img" aria-label="月末观测与5/10年均值">'
            f'<text x="2" y="25">{high:.2f}</text><text x="2" y="155">{low:.2f}</text>'
            + ''.join(paths) + f'<text x="48" y="180">{escape(rows[0]["date"])}</text>'
            f'<text x="640" y="180">{escape(rows[-1]["date"])}</text></svg>')


def metric(label, statistics, unit):
    means = statistics['means']
    ranks = statistics['windows']
    details = []
    for years in ('5', '10'):
        rank = ranks[years]['percentile_ratio']
        rank = round(float(rank) * 100, 2) if rank is not None else None
        details.append(f'<td>{years}年</td><td>{cell(means[years]["value"])}</td>'
                       f'<td>{cell(rank, "%")}</td><td>{means[years]["count"]}/{means[years]["required"]}完整月</td>')
    return (f'<article><h3>{escape(label)} · {escape(unit)}</h3><b>{cell(statistics["current"])}</b>'
            f' <small>{cell(statistics["date"])} · {escape(statistics["status"])}</small>' + chart(statistics)
            + '<table><thead><tr><th>窗口</th><th>均值</th><th>历史分位</th><th>均值样本</th></tr></thead><tbody>'
            + ''.join('<tr>' + row + '</tr>' for row in details) + '</tbody></table></article>')


def comparison_chart(metric, data, method=None):
    rows = data['chart']
    label = '全市场 ' + ('PE TTM' if metric == 'pe' else 'PB') + ' · 历史均值与两市综指'
    value_label = '理杏仁 A股全指 · ' + method if method else '全市场中位数'
    definitions = [('value', value_label, '#477da1', 'valuation', ''),
                   ('mean5', '5年平均', '#b77c59', 'valuation', '6 4'),
                   ('mean10', '10年平均', '#8e759f', 'valuation', '2 3')]
    if any(row.get('historical_mean') is not None for row in rows):
        definitions.append(('historical_mean', '历史平均', '#a75157', 'valuation', ''))
    if metric == 'pe':
        definitions.append(('bond_inverse', '10Y国债收益率倒数', '#a89963', 'valuation', '3 3'))
    definitions.extend([('sh', '上证综指', '#899b69', 'price', ''), ('sz', '深证综指', '#b57e84', 'price', '')])
    scales = {}
    for axis in ('valuation', 'price'):
        values = [float(row[key]) for row in rows for key, _, _, kind, _ in definitions
                  if kind == axis and row.get(key) is not None]
        scales[axis] = (min(values), max(values)) if values else (0, 1)
    lines = []
    for key, _, color, axis, dash in definitions:
        low, high = scales[axis]
        segment = []
        for index, row in enumerate([*rows, {}]):
            if row.get(key) is None:
                if segment:
                    lines.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"/>')
                segment = []
            else:
                x = 55 + index / max(1, len(rows)-1) * 645
                y = 260 - (float(row[key])-low) / (high-low or 1) * 220
                segment.append(f'{x:.2f},{y:.2f}')
    legend = ' · '.join(f'<span style="color:{color}">{name}</span>' for _, name, color, _, _ in definitions)
    left, right = scales['valuation'], scales['price']
    svg = (f'<svg viewBox="0 0 760 290" role="img" aria-label="{label}">'
           '<text x="8" y="15">估值（倍）</text><text x="610" y="15">价格指数（基准100）</text>'
           f'<text x="2" y="43">{left[1]:.2f}</text><text x="2" y="263">{left[0]:.2f}</text>'
           f'<text x="708" y="43">{right[1]:.0f}</text><text x="708" y="263">{right[0]:.0f}</text>'
           + ''.join(lines) + (f'<text x="55" y="285">{escape(rows[0]["date"])}</text>'
                              f'<text x="650" y="285">{escape(rows[-1]["date"])}</text>' if rows else '') + '</svg>')
    return (f'<article><h3>{label}</h3><p>{legend}</p>' + svg
            + f'<p class="muted">两综指共同基准月：{cell(data["base_month"])}；各序列独立取月末最后观测，不前填。'
            + ('国债收益率倒数仅作参照，不是债券PE。' if metric == 'pe' else '') + '</p></article>')


def render(snapshot):
    data = snapshot['data']
    title = f'市场水位 · {data["asof"]} · {data["method"]}'
    cards = []
    for label, item in [('综合', data['scores']['overall']), *[(name, data['scores']['categories'][key])
                          for key, name in (('equity', '股票'), ('rates', '利率债'), ('credit', '信用债'))]]:
        cards.append(f'<article><h3>{label}</h3><strong>{cell(item["score"])}</strong> {escape(item["label"])}'
                     f'<small>覆盖 {item["included"]}/{item["expected"]}</small></article>')
    indices = []
    for index in data['indices']:
        for key, label in (('pe', 'PE TTM'), ('pb', 'PB'), ('dividend_yield', '股息率')):
            unit = '比例（0.01=1%）' if key == 'dividend_yield' else '倍'
            indices.append(metric(index['label'] + ' ' + label, index['metrics'][key], unit))
    source_rows = []
    for source in data['sources']:
        links = ''.join(f'<li><a href="{escape(item["url"], quote=True)}">{escape(item["url"])}</a>'
                        f'<br>抓取 {escape(item["fetched_at"])} · SHA-256 {escape(item["sha256"])}</li>' for item in source['evidence'])
        source_rows.append(f'<details><summary>{escape(source.get("index_id", source.get("group", "")))} · '
                           f'{escape(source["publisher"])}</summary><p>快照 {escape(source["snapshot_id"])}</p><ul>{links}</ul></details>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>{escape(title)}</title><style>body{{font:15px/1.7 system-ui,sans-serif;max-width:1120px;margin:40px auto;padding:0 20px;color:#26323e;background:#fafafa}}'
            '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}article{background:white;border:1px solid #e0e5e9;border-radius:12px;padding:18px;margin:8px 0;overflow:hidden}'
            'h1,h2,h3{line-height:1.35}strong{font-size:30px}small,.muted{color:#687583}small{display:block}svg{width:100%;font-size:11px}table{width:100%;font-size:12px;text-align:left}details{border-top:1px solid #ddd;padding:10px 0}a,li{overflow-wrap:anywhere}</style>'
            f'<h1>{escape(title)}</h1><p class="muted">Leo · {escape(snapshot["created_at"][:10].replace("-", "."))} · 离线冻结报告 · '
            f'方法 {escape(data["method_version"])}</p><p>快照 {escape(snapshot["snapshot_id"])}</p>'
            '<div class="grid">' + ''.join(cards) + '</div><h2>规则摘要</h2><p>' + escape(data['commentary']['overall']) + '</p><ul>'
            + ''.join('<li>' + escape(text) + '</li>' for text in data['commentary']['evidence'])
            + '</ul><h2>全市场估值对照</h2>'
            + ''.join(comparison_chart(key, item, data['method'] if data.get('method_version') in ('waterlevel-v3', 'waterlevel-v4', 'waterlevel-v5') else None)
                      for key, item in data.get('comparisons', {}).items())
            + '<h2>主要指数估值</h2><p>蓝色：月末观测；棕色：5年均值；绿色：10年均值。股息率保持接口比例单位。</p><div class="grid">'
            + ''.join(indices) + '</div><h2>利率、宏观与跨资产参照</h2><div class="grid">'
            + ''.join(metric(item['label'], item['statistics'], item['unit']) for item in data['series'] if item['group'] not in ('full_market', 'composites'))
            + '</div><h2>方法与边界</h2><ul>' + ''.join('<li>' + escape(text) + '</li>' for text in data['limitations'])
            + '</ul><h2>来源证据</h2>' + ''.join(source_rows) + '</html>')
