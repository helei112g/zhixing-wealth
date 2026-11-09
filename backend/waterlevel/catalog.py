"""Explicit series definitions. Source and statistical identities never alias."""
from typing import Literal

from ..common import ZONE
from ..research.lixinger import METHODS

MARKET_SCHEDULE = dict(weekday=5, time='18:00', timezone=ZONE.key)

FULL_MARKET_CODE = '1000002'
FULL_MARKET_ID = 'lxri:' + FULL_MARKET_CODE
FULL_MARKET_START = '1994-01-01'
FULL_MARKET_FIELDS = {f'full_market_{metric}_{method}': f'{field}.{method}'
                      for metric, field in (('pe', 'pe_ttm'), ('pb', 'pb'))
                      for method in METHODS}
LIXINGER_FIELDS = {
    'full_market': FULL_MARKET_FIELDS,
    'market_turnover': {'a_share_turnover': 'ta'},
}
MARGIN_FIELDS = {'sh_margin_balance': 'H_RZRQYE', 'sz_margin_balance': 'S_RZRQYE'}
AMOUNT_GROUPS = {'margin_balances', 'market_turnover'}
MAX_MARKET_AMOUNT = 10**14

GROUPS = {
    'full_market': dict(label='A股全指 PE / PB', cadence='manual', publisher='理杏仁'),
    'margin_balances': dict(label='沪深两融余额', cadence='manual', publisher='东方财富'),
    'market_turnover': dict(label='A股全指成交额', cadence='manual', publisher='理杏仁'),
    'composites': dict(label='上证综指 / 深证综指', cadence='manual', publisher='新浪财经'),
    'calendar': dict(label='国家统计局发布日历', cadence='manual', publisher='国家统计局'),
    'china_rates': dict(label='中国国债收益率', cadence='manual', publisher='东方财富（原有渠道）'),
    'us_rates': dict(label='美国国债收益率', cadence='manual', publisher='美国财政部'),
    'credit': dict(label='AAA 3Y中票与3Y国债', cadence='manual', publisher='中债 / 中国债券信息网'),
    'repo': dict(label='FR007 / FDR007', cadence='manual', publisher='全国银行间同业拆借中心'),
    'pmi': dict(label='PMI', cadence='manual', publisher='东方财富（国家统计局数据）'),
    'prices': dict(label='CPI / PPI', cadence='manual', publisher='东方财富（国家统计局数据）'),
    'money': dict(label='M1 / M2同比', cadence='manual', publisher='东方财富（人民银行数据）'),
    'social_financing': dict(label='社融存量同比', cadence='manual', publisher='中国人民银行'),
}
Group = Literal[*GROUPS]

SERIES = {
    'full_market_pe': ('full_market', '理杏仁 A股全指 PE TTM', '倍', 'daily'),
    'full_market_pb': ('full_market', '理杏仁 A股全指 PB', '倍', 'daily'),
    'margin_balance': ('margin_balances', '沪深两融余额', 'CNY', 'daily'),
    'sh_margin_balance': ('margin_balances', '沪市两融余额', 'CNY', 'daily'),
    'sz_margin_balance': ('margin_balances', '深市两融余额', 'CNY', 'daily'),
    'a_share_turnover': ('market_turnover', 'A股全指成交额', 'CNY', 'daily'),
    'sh_composite': ('composites', '上证综指000001', '点', 'daily'),
    'sz_composite': ('composites', '深证综指399106', '点', 'daily'),
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

# These series have independent upstream requests. Keep the original group
# identities for existing snapshots, reports and callers that update the group.
SPLIT_GROUPS = {
    'composites': ('sh_composite', 'sz_composite'),
    'prices': ('cpi_yoy', 'ppi_yoy'),
    'repo': ('fr007', 'fdr007'),
}

INDICES = (
    ('csi:000300', '沪深300'), ('csi:000905', '中证500'), ('csi:000852', '中证1000'),
    ('csi:000688', '科创50'), ('cni:399006', '创业板指'), ('csi:930050', '中证A50'),
    ('csi:000510', '中证A500'), ('csi:000922', '中证红利'), ('csi:000985', '中证全指'),
)

LIMITATIONS = [
    '历史分位是相对位置，不是内在价值、收益概率或仓位建议。综合水位是股票、利率债、信用债有效分数的中位数，缺类时覆盖范围改变。',
    '股票评分固定使用沪深300/中证500/中证1000的mcw PE、沪深300/中证500的mcw PB及理杏仁A股全指中位数PE/PB。图表方法切换不改变评分。',
    '全市场使用理杏仁A股全指（1000002）的PE TTM和PB，分别保存mcw、ew、ewpvo与median的日频历史。两综指为价格指数，不含分红再投资；收益率倒数仅作跨资产参照。',
    '沪深两融余额按同日沪市、深市两融余额合计。成交额为理杏仁A股全指（1000002）的样本汇总。两项均为观察指标，不计入水位评分。',
    '全历史指本地已取得的全部有效覆盖，不保证供应商全部历史。转债价格/溢价率、市值/GDP及未经核验的宏观调整估值公式未接入。',
    'M2−M1为同月同比增速之差，单位百分点。M1自2025年启用新口径，2024年回溯覆盖未经逐点确认，跨口径历史仅供参考。',
    '统计月份、实际数据日、发布日期和抓取时间分别保留。来源历史可能修订；按截止日截取当前可得数据，不代表当时已知版本。',
    '分位按原始频率、严格小于当前值计算（含当前观测作分母），需完整5/10年跨度；跨度完整不保证逐点无缺失。均值只取连续60/120个完整自然月的最后观测，不补缺月。',
]
