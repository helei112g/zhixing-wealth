"""Documented core write contracts. Legacy event-specific fields stay compatible."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

Amount = str | int | float


class SyncRequest(BaseModel):
    instrument_id: str | None = None
    full: bool = False
    trigger: Literal['manual_update','manual_instrument','manual_full','retry','api_request'] = Field('api_request', description='触发来源，供更新记录追溯。定时、启动、记账来源由服务端设置。')


class ReinvestmentNav(BaseModel):
    date: str = Field(description='与请求日期严格相同，YYYY-MM-DD')
    nav: str = Field(description='自动同步的场外基金单位净值，Decimal 字符串')
    source: str


class ReinvestmentEstimate(BaseModel):
    portfolio_id: str
    instrument_id: str
    date: str
    gross_amount: str = Field(description='再投总价，不含手续费；不再次扣减分红税费')
    currency: str
    revision: int
    status: Literal['available', 'missing_nav', 'unsupported_market', 'rounded_zero']
    reference: ReinvestmentNav | None
    quantity: str | None = Field(description='仅 available 返回估算份额；必须核对后显式提交，不自动记账')
    quantity_decimals: int
    rounding: str
    note: str


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    expected_revision: int | str | None = Field(None, description='从 bootstrap 或交易预览读取。提交时若账本变化则返回409，不写入；建议所有AI写入提供。')


class DividendReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    portfolio_id: str
    event_id: str = Field(description='候选 recorded_event.id；必须仍是本标的的DIVIDEND/REINVEST，只核对此原流水，不新增或更改流水')
    mode: Literal['keep_recorded']
    review_basis: dict[str, Any] = Field(description='原样传回候选 review_basis，拒绝过时的权益或公告依据')
    expected_revision: int = Field(ge=0, strict=True, description='必须使用候选 revision；旧版本409，不重试')
    reason: str = Field(min_length=1, max_length=2000, description='本人核对并保留实际到账记录的说明')


class DividendReviewResult(BaseModel):
    decision: dict[str, Any] = Field(description='含 review_basis、event_id、mode、reason、review_revision 的可审计核对结论')
    event: dict[str, Any] = Field(description='保持原样的已确认流水，包括金额、日期和锁定汇率')
    revision: int


class PortfolioRequest(WriteRequest):
    name: str
    description: str | None = Field(None, max_length=10000, description='可选组合介绍，纯文字，保留换行；省略或null保留原介绍，空字符串清空')
    planned_amount: Amount = Field(description='CNY 总预算；推荐十进制字符串')
    start_date: str | None = Field(None, description='YYYY-MM-DD，不得晚于今天；省略为今天')
    target_annual_pct: Amount = Field('5', description='目标年化百分数，5 表示5%')
    benchmark_id: str | None = None
    archived: bool = False


class InstrumentInput(BaseModel):
    model_config = ConfigDict(extra='allow')
    market: Literal['CN', 'SH', 'SZ', 'HK', 'US', 'FUND']
    code: str
    name: str = ''


class SellLot(BaseModel):
    buy_event_id: str
    quantity: Amount


class EventRequest(WriteRequest):
    factor: Amount | None = Field(None, description='SPLIT 专项核对：送转/拆股后的数量倍数，10送8转12为3；保留总成本，先于除权日买卖执行。现金红利单独核对。')
    source_split_id: str | None = Field(None, max_length=128, description='SPLIT 所核对的公告ID，来自todo；修改日期后仍关联原公告，避免重复确认')
    portfolio_id: str
    kind: str = Field(description='日常只用 BUY、SELL、DIVIDEND、REINVEST；其他类型仅兼容历史或专项核对')
    date: str = Field(description='成交日或除息日，YYYY-MM-DD')
    id: str | None = Field(None, description='仅更正预览提供；新增的事件ID由服务端生成')
    instrument_id: str | None = None
    instrument: InstrumentInput | None = Field(None, description='买入可直接按市场+代码建档；卖出和分红使用 instrument_id')
    quantity: Amount | None = None
    gross_amount: Amount | None = Field(None, description='买卖成交总金额，不含手续费及税费；分红为税前金额')
    transaction_costs: Amount | None = Field(None, description='手续费及税费合计，与历史 fee/tax 不重复提交')
    fx_rate: Amount | None = Field(None, description='锁定汇率：1交易币种=x CNY，CNY为1')
    sell_lots: list[SellLot] | None = None
    sell_all: bool = False
    net_amount: Amount | None = Field(None, description='分红税后净额；可省略，由 gross_amount-tax 精确计算；同时传入且税费已核对时须一致')
    pay_date: str | None = None
    pay_fx_rate: Amount | None = Field(None, description='到账日折算率；留空沿用当笔 fx_rate')
    pay_fx_source_date: str | None = Field(None, description='到账汇率来源日期，不晚于 pay_date')
    pay_fx_type: str | None = None
    reinvest_date: str | None = Field(None, description='红利再投净值归属日，YYYY-MM-DD')
    reinvest_quantity: Amount | None = Field(None, description='红利再投实际确认份额')
    reinvest_gross_amount: Amount | None = Field(None, description='红利再投成交总价，不含再投手续费')
    reinvest_fx_rate: Amount | None = Field(None, description='红利再投折算率；留空沿用到账率')
    reinvest_fx_source_date: str | None = Field(None, description='再投汇率来源日期，不晚于 reinvest_date')
    reinvest_fx_type: str | None = None
    fee: Amount | None = Field(None, description='再投手续费或历史手续费；买卖推荐 transaction_costs，避免重复提交')
    tax: Amount | None = None
    tax_known: bool | None = None
    ex_date_known: bool | None = None
    reason: str = ''
    change_reason: str | None = Field(None, description='更正必须填写原因')
    allow_duplicate: bool = Field(False, description='仅确认为另一笔相同成交时使用，不是失败重试开关')


class AllocationRequest(WriteRequest):
    target_pct: Amount = Field('0', description='单标的计划占总预算百分数，0至100；组合计划资金缺口仅提醒，允许保存')
    asset_class: str = '未分类'
    region: str = Field('未分类', description='A股、美股、港股、海外其他、混合、未分类')
    tags: list[str] | str = Field(default_factory=list, description='推荐字符串数组；兼容旧界面的逗号分隔文本')


class NoteMutationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, description='从笔记列表/详情读取。过期返回409，保留编辑内容，重新核对后再提交。')


class NoteRequest(NoteMutationRequest):
    title: str = Field(min_length=1, max_length=100, description='笔记标题，保存到Markdown文件，不入数据库')
    body: str = Field(min_length=1, max_length=200000, description='Markdown原文；支持标题、段落、加粗、序号列表。服务器生成隔离路径，禁止客户端指定文件路径。')


class NoteInfo(BaseModel):
    id: str
    portfolio_id: str
    instrument_id: str
    path: str = Field(description='数据目录内的相对Markdown路径；不可提交该路径来修改文件')
    title: str
    created_at: str
    updated_at: str


class NoteDetail(NoteInfo):
    body: str
    revision: int


class NoteListItem(NoteInfo):
    excerpt: str
    available: bool
    error: str | None = None


class NotePage(BaseModel):
    items: list[NoteListItem]
    total: int
    page: int
    page_size: int
    page_count: int
    portfolio_id: str
    instrument_id: str
    revision: int


class CancelRequest(WriteRequest):
    reason: str = Field(description='删除原因，保留修订记录')
    cascade: bool = Field(default=False, description='显式关联删除，必须先读取 deletion-preview 并核对全部清单')
    event_ids: list[str] | None = Field(default=None, description='关联删除预览的完整 event_ids；必须原样提交并带原 revision')


class EventDeletionItem(BaseModel):
    event: dict[str, Any]
    instrument_name: str
    portfolio_name: str
    is_root: bool
    dependency_ids: list[str]
    shared_with_other_lots: bool
    other_buy_event_ids: list[str]


class EventDeletionPreview(BaseModel):
    event_id: str
    revision: int
    event_ids: list[str]
    items: list[EventDeletionItem]
    total: int
    audit_preserved: bool
    can_delete: bool
    validation_error: str | None


class SplitLotEffect(BaseModel):
    buy_event_id: str
    date: str
    quantity_before: str
    quantity_after: str
    quantity_change: str


class SplitEffect(BaseModel):
    event_id: str
    instrument_id: str
    date: str
    factor: str
    quantity_before: str
    quantity_after: str
    quantity_change: str
    cost_native: str = Field(description='送转前后的持仓总成本不变；标的币种')
    currency: str
    status: Literal['applied', 'no_position']
    lots: list[SplitLotEffect] = Field(description='按当前历史流水重放的参与批次，仅除权日前剩余份额；不含已售份额和除权日新买入')


class SplitChange(BaseModel):
    event_id: str
    before: SplitEffect | None
    after: SplitEffect | None


class EventPreview(BaseModel):
    model_config = ConfigDict(extra='allow')
    revision: int
    event: dict[str, Any]
    split_changes: list[SplitChange] = Field(description='本次记账或更正引起的送转效果变化，含后续已确认记录；预览不写入')


class TransactionFacts(BaseModel):
    split: SplitEffect | None = None
    quantity: str | None
    unit_price: str | None
    unit_after_costs: str | None = Field(description='买入/再投含费税成本除以份额；卖出为扣费税净回款除以份额')
    unit_after_costs_label: str
    currency: str
    fees_native: str
    native_total: str
    base_total: str | None
    amount_label: str
    fx_rate: str | None
    pending: bool
    distribution_base: str | None
    dividend_gross_native: str | None
    dividend_tax_native: str | None
    dividend_net_native: str | None
    dividend_tax_differs: bool


class ProfitBreakdown(BaseModel):
    total: str | None
    price_net: str | None = Field(description='CNY价格及汇兑收益，扣除买卖及非分红费用；含已实现/未实现收益')
    dividends_net: str | None = Field(description='净分红，含再投与已确认应收，扣关联补税/费用；不重复计入价格项')
    other_income: str = Field(description='利息等其他收入；非零时单独展示')
    market_value: str | None
    sale_proceeds: str | None
    buy_cost: str
    extra_costs: str
    rounding_adjustment: str | None = Field(description='仅用于两位小数显示对账的舍入差，不是新增收益')
    complete: bool
    price_basis: str


class Performance(BaseModel):
    model_config = ConfigDict(extra='allow')
    profit_breakdown: ProfitBreakdown | None = Field(None, description='组合与标的摘要的收益等式；逐笔批次结果不重复展示此摘要')
    profit: str | None = Field(description='累计收益，CNY；已包含净分红、费税及汇兑')
    return_on_cost: str | None = Field(description='累计收益除以累计买入成本；比例值，不是百分数字')
    xirr: str | None = Field(description='ACT/365实际投资现金流年化；比例值；不含闲置现金')
    xirr_status: str
    dividends_net: str | None = Field(description='审计使用的分红分摊，CNY；已计入profit，不能重复加')


class TransactionRow(BaseModel):
    event: dict
    instrument: dict | None
    facts: TransactionFacts
    actual: Performance | None
    can_view_profit: bool
    can_edit: bool
    can_cancel: bool


class TransactionPage(BaseModel):
    items: list[TransactionRow]
    total: int
    unfiltered_total: int
    page: int
    page_size: int
    page_count: int
    filters: dict[str, str]
    facets: dict[str, list[str]]
    portfolio_id: str
    instrument_id: str | None
    revision: int


class TransactionNeighbor(BaseModel):
    id: str
    date: str
    kind: str


class TransactionRevision(BaseModel):
    id: int
    entity: str
    entity_id: str
    reason: str
    created_at: str
    before_data: dict[str, Any] | None = None
    after_data: dict[str, Any] | None = None


class MarketWindow(BaseModel):
    months: Literal[1, 3]
    target_date: str = Field(description='交易日加1或3个自然月，月末日期截到目标月末')
    date: str | None = Field(description='目标日起7日内、且不晚于观察截止日的首个可得行情日')
    price: str | None
    source: str | None
    return_: str | None = Field(alias='return', description='同一行情序列的期末值/交易日收盘值-1；比例值')
    relative_trade_return: str | None = Field(description='以上序列涨跌按交易日收盘价/实际成交价调整的参考，不是实际批次收益')
    status: Literal['pending', 'missing_base', 'missing_quote', 'corporate_action', 'available']
    status_label: str


class ForwardPerformance(BaseModel):
    trade_date: str
    as_of: str
    currency: str
    basis: Literal['raw', 'adjusted', 'total_return']
    basis_label: str
    base_date: str | None
    base_price: str | None
    base_source: str | None
    windows: list[MarketWindow]


class PlanFunding(BaseModel):
    currency: Literal['CNY']
    status: Literal['shortfall', 'balanced', 'surplus', 'unknown']
    available_amount: str | None
    required_amount: str | None = Field(description='各已设置计划标的 max(计划金额-净投入,0) 之和；无法完整核算时为null')
    known_required_amount: str
    shortfall: str | None = Field(description='max(剩余计划需投入-剩余可投金额,0)，CNY；仅提醒，不阻止保存')
    surplus: str | None = Field(description='max(剩余可投金额-剩余计划需投入,0)，CNY')
    total_target_pct: str
    unknown_instrument_ids: list[str]
    basis: str


class PlanCheck(BaseModel):
    portfolio_id: str
    instrument_id: str
    revision: int
    target_pct: str
    target_amount: str
    plan_funding: PlanFunding


class AllocationResult(BaseModel):
    portfolio_id: str
    instrument_id: str
    target_pct: str
    asset_class: str
    region: str
    tags: list[str]
    plan_funding: PlanFunding


class TransactionDetail(BaseModel):
    portfolio_id: str
    revision: int
    event: dict[str, Any]
    instrument: dict[str, Any] | None
    facts: TransactionFacts
    actual: Performance | None
    forward_performance: ForwardPerformance | None = None
    todos: list[dict[str, Any]]
    cancelled: bool
    neighbors: list[TransactionNeighbor]
    revisions: list[TransactionRevision]


class NoteDeletion(BaseModel):
    id: str
    portfolio_id: str
    instrument_id: str
    deleted: bool
    revision: int
