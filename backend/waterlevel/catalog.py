"""Explicit series definitions. Source and statistical identities never alias."""
GROUPS = {
    'calendar': dict(label='国家统计局发布日历', cadence='calendar', publisher='国家统计局'),
    'china_rates': dict(label='中国国债收益率', cadence='weekly', publisher='东方财富（原有渠道）'),
    'us_rates': dict(label='美国国债收益率', cadence='weekly', publisher='美国财政部'),
    'credit': dict(label='AAA 3Y中票与3Y国债', cadence='weekly', publisher='中债 / 中国债券信息网'),
    'repo': dict(label='FR007 / FDR007', cadence='weekly', publisher='全国银行间同业拆借中心'),
    'pmi': dict(label='PMI', cadence='release', publisher='东方财富（国家统计局数据）'),
    'prices': dict(label='CPI / PPI', cadence='release', publisher='东方财富（国家统计局数据）'),
    'money': dict(label='M1 / M2同比', cadence='monthly', publisher='东方财富（人民银行数据）'),
    'social_financing': dict(label='社融存量同比', cadence='monthly', publisher='中国人民银行'),
}

SERIES = {
    'cn_2y': ('china_rates', '中国2Y国债收益率', '%', 'daily'),
    'cn_10y': ('china_rates', '中国10Y国债收益率', '%', 'daily'),
    'cn_30y': ('china_rates', '中国30Y国债收益率', '%', 'daily'),
    'us_2y': ('us_rates', '美国2Y国债收益率', '%', 'daily'),
    'us_10y': ('us_rates', '美国10Y国债收益率', '%', 'daily'),
    'us_30y': ('us_rates', '美国30Y国债收益率', '%', 'daily'),
    'gov_3y': ('credit', '中债3Y国债收益率', '%', 'daily'),
    'aaa_3y': ('credit', '中债AAA 3Y中短期票据收益率', '%', 'daily'),
    'fr007': ('repo', 'FR007回购定盘利率', '%', 'daily'),
    'fdr007': ('repo', 'FDR007银银间回购定盘利率', '%', 'daily'),
    'pmi': ('pmi', '制造业PMI', '指数', 'monthly'),
    'nonmanufacturing_pmi': ('pmi', '非制造业商务活动指数', '指数', 'monthly'),
    'cpi_yoy': ('prices', 'CPI同比', '%', 'monthly'),
    'ppi_yoy': ('prices', 'PPI同比', '%', 'monthly'),
    'm1_yoy': ('money', 'M1同比', '%', 'monthly'),
    'm2_yoy': ('money', 'M2同比', '%', 'monthly'),
    'sf_stock_yoy': ('social_financing', '社融存量同比', '%', 'monthly'),
}

INDICES = (
    ('csi:000300', '沪深300'), ('csi:000905', '中证500'), ('csi:000852', '中证1000'),
    ('csi:000688', '科创50'), ('cni:399006', '创业板指'), ('csi:930050', '中证A50'),
    ('csi:000510', '中证A500'), ('csi:000922', '中证红利'), ('csi:000985', '中证全指'),
)

LIMITATIONS = [
    '历史分位是相对位置，不是内在价值、收益概率或仓位建议。综合水位是股票、利率债、信用债有效分数的中位数，缺类时覆盖范围改变。',
    '股票评分固定使用沪深300/中证500/中证1000的mcw PE及沪深300/中证500的mcw PB；本期未接入乐咕全市场中位数，股票覆盖与原Skill不同。图表方法切换不改变评分。',
    '中证全指是指数样本，不能代替乐咕全市场。全市场中位数、两综指对照、转债价格/溢价率和市值/GDP尚未接入，未进入任何评分。',
    'M2−M1为同月同比增速之差，单位百分点。M1自2025年启用新口径，2024年回溯覆盖未经逐点确认，跨口径历史仅供参考。',
    '统计月份、实际数据日、发布日期和抓取时间分别保留。来源历史可能修订；按截止日截取当前可得数据，不代表当时已知版本。',
    '分位按原始频率、严格小于当前值计算（含当前观测作分母），需完整5/10年跨度；跨度完整不保证逐点无缺失。均值只取连续60/120个完整自然月的最后观测，不补缺月。',
]
