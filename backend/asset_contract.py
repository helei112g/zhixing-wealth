"""Discoverable recommended asset commands; legacy payloads remain compatible."""
from copy import deepcopy


def obj(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)


DATE = dict(type='string', format='date', description='YYYY-MM-DD；金额日期不能晚于今天')
MONEY = dict(type='string', pattern=r'^\d+(\.\d+)?$', description='完整余额或收入，十进制字符串；不是增减额；币种由项目决定')
ID = dict(type='string', minLength=1, description='读取 catalog/bootstrap 返回的 ID，不按名称猜测')
REASON = dict(type='string', maxLength=300)
PLAN = obj({
    'mode': dict(enum=['none', 'once', 'fixed', 'annuity', 'equal_principal', 'annual']),
    'first_due': dict(type='string', format='date', description='债务重设计划时不得早于本次金额日期'),
    'periods': dict(type='integer', minimum=1, maximum=600, description='负债按月；保险按年且不超过100年'),
    'amount': {**MONEY, 'description':'fixed/annual 及保险 once 的每次付款额'},
    'annual_rate': dict(type='string', description='年利率百分数：3.5 表示3.5%，不是0.035；0到100'),
}, ['mode'])

PAYLOADS = {
    'item': obj({
        'category': dict(enum=['cash', 'fixed', 'investment', 'receivable', 'debt', 'insurance']),
        'currency': dict(enum=['CNY', 'HKD', 'USD']),
        'name': dict(type='string', minLength=1, maxLength=80),
        'effective_on': DATE,
        'household_amount': dict(anyOf=[MONEY, dict(type='null')], description='本人实际持有/承担金额；仅保险现金价值允许null；保额与保费不是资产'),
        'started_on': dict(type='string', format='date', description='负债借款日期，不等于余额日期'),
        'insured': dict(type='string', description='仅保险：被保险人'),
        'coverage_type': dict(type='string', description='仅保险：险种'),
        'coverage_amount': MONEY,
        'policy_end': dict(type=['string', 'null'], format='date'),
        'payment_plan': PLAN, 'reason': REASON,
    }, ['category', 'currency', 'name', 'effective_on', 'household_amount']),
    'valuation': obj({
        'item_id': ID, 'effective_on': DATE,
        'household_amount': dict(anyOf=[MONEY, dict(type='null')], description='该日完整实际余额，仅保险允许null'),
        'extend_start': dict(type='boolean', description='显式补录到首笔之前，仅现金/固定资产/理财/应收款支持'),
        'reason': REASON,
    }, ['item_id', 'effective_on', 'household_amount']),
    'links': obj({'portfolio_ids': dict(type='array', items=ID, minItems=1, maxItems=100, uniqueItems=True), 'reason': REASON}, ['portfolio_ids']),
    'unlink': obj({'link_ids': dict(type='array', items=ID, minItems=1, maxItems=100, uniqueItems=True), 'reason': REASON}, ['link_ids']),
    'income': obj({
        'kind': dict(enum=['salary', 'rent', 'fund', 'custom']),
        'name': dict(type='string', maxLength=80, description='custom 时必填'),
        'month': dict(type='string', pattern=r'^\d{4}-\d{2}$'),
        'date': {**DATE, 'description':'必须在month内，不能晚于今天'},
        'currency': dict(enum=['CNY', 'HKD', 'USD']), 'amount': MONEY, 'reason': REASON,
    }, ['kind', 'month', 'date', 'currency', 'amount']),
}


def asset_command_contract():
    variants = []
    for operation, payload in PAYLOADS.items():
        record = dict(type=['string', 'null'], description='仅编辑item/income时使用原记录ID；valuation始终追加快照') if operation in ('item', 'income') else dict(type='null')
        variants.append(obj({'operation': dict(const=operation), 'payload': deepcopy(payload),
                             'expected_revision': dict(type='integer', minimum=0), 'record_id': record},
                            ['operation', 'payload', 'expected_revision']))
    return {
        'contract_version': 1,
        'schema': {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': '资产推荐写入命令', 'oneOf': variants},
        'preview': '/api/family/preview', 'write': '/api/family/commands', 'catalog': '/api/family/catalog',
        'readback': {'item': '/api/family/catalog', 'valuation': '/api/family/catalog',
                     'links': '/api/family/overview', 'unlink': '/api/family/overview', 'income': '/api/family/income?year=YYYY'},
        'operations': {
            'item': '新增项目或编辑资料/还款/保费规则；已有项目余额更新优先用valuation',
            'valuation': '追加指定日期完整余额，同日新值生效，历史保留，不覆盖之后日期',
            'links': '多选关联投资组合，当天生效；只读引用，不反向写入组合',
            'unlink': '按关联ID解除，当天生效；保留组合和历史',
            'income': '记录收入或按record_id更正；不自动增加现金，不重复计入资产',
        },
        'rules': {
            'revision': '使用catalog/预览的revision；409重新读取核对，禁止自动替换版本提交',
            'retry': '未提供幂等键；超时先回读核对，不自动重试任何写入',
            'preview_ids': '预览中的新ID是暂定值；提交后使用响应result的正式ID回读',
            'numbers': '金额为Decimal字符串；null为未知，零为已知0；API不返回万/亿缩写',
            'scope': '推荐schema覆盖当前界面操作；原始兼容字段见/api/guide，不重建退役的开支/逐笔提醒功能',
            'item_edit': '编辑保留原category/currency；只变余额用valuation，勿将catalog的versions直接作为payload',
            'payment': '计划只生成提醒，不扣余额；debt支持none/once/fixed/annuity/equal_principal，insurance支持none/once/annual',
            'comparison': '总览月比上月末、年比上年末；history显式comparison=yoy表示同比',
        },
        'legacy_operations': ['link', 'settings', 'schedule', 'reconcile', 'closure'],
    }
