# AI 接入与项目技能

界面和 Agent 访问同一后端服务。支持直接 HTTP、OpenAPI 和标准库客户端，不要求额外 MCP 服务或插件。部署、恢复、数据搬迁不是记账技能的隐含权限。

组合指标从 `capabilities.investment_summary` 定位，或调用客户端 `investment_summary(P)`。累计投入取 `capital_summary.cost`（累计买入成本，含再投及期初记账成本），不能用净投入替代、不能称为外部新增本金；净投入独立按实际买入支出及相关费用减已到账卖出净回款和现金分红计算；`available_amount=planned_amount-invested_amount`，不能倒推净投入。利息单列，账面现金用`account_available_cash`。收益分解中的 `cash_dividends_net` 与 `reinvested_dividends_net` 按原分红记录类型拆分、各扣关联费税一次，相加仍是 `dividends_net`，不能再次叠加到收益或资产。应收可计入收益，到账前不能称为已回收现金；未知分项保留 null。

首次接手安装或每次升级先读 [安装与升级指南](install.md)。安装后默认进入生成的独立日常工作区，不使用开发账本。首次安装为空，不创建演示记录；查询、能力发现和读取文档不构成记账授权。发布保全与账务更正分开处理，不能用发布脚本更改正式业务数据。

## 发现技能与能力

项目自带四个技能：[资产管理](../.agents/skills/zhixing-assets/SKILL.md)、[组合记账](../.agents/skills/zhixing-portfolios/SKILL.md)、[投资笔记](../.agents/skills/zhixing-notes/SKILL.md)、[指数研究](../.agents/skills/zhixing-research/SKILL.md)。完整名称也可从 `capabilities.skills` 读取。在 Codex 中打开本项目目录，或以本项目为工作目录运行，即可发现 `.agents/skills`，无需全局安装；也可用 `$zhixing-assets` 等名称显式调用。

Codex 查找当前目录和向上的技能目录，不递归搜索任意子项目。若从父工作区启动，请先将本项目设为工作目录；未出现时重启会话。其他 Agent 是否自动发现取决于其技能规范，不能保证都兼容。见 [Codex 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。技能文件校验通过与当前会话已加载技能是不同事实。

同步与存储：capabilities.storage 提供保留策略。jobs仅返回最近20条，默认最多保存20条（含运行中），不清理运行中任务，不作为完整审计；相同行情不重复写入，修正版本保留，交易锁定汇率不变。AI与界面使用同一同步入口，不直接写库或执行文件清理。

系统通知从 `Client.notification_status()` 或 `GET /api/notifications` 读取，能力见 `capabilities.family.notification_scheduling`。系统每次只登记最近一条，同一时刻合并；`deferred_count` 是等待后续排程数，不是失败，`missing_count` 只计本次选中的缺失提醒。`next_notification_at` 为已受理时刻，后续接续需本机服务运行。不能把系统受理等同于用户看到、分红确认或已付款；不得用确认业务、删除通知状态文件来消除排程提示。

企业行动数据同步失败不表示发现送转计划。完整纯现金方案（含中小股东／大股东差异化金额）可确认无送转，现金金额差异另存 jobs.items[].notes 及分红候选 note；基础金额可能为平均值，确认分红前须按公告和实际到账核对。重试 `/api/sync` 后检查同标的最新 corporate_actions 子项与 `/api/reminders`：成功会自然消除旧失败提醒，不能删除同步历史或新增 SPLIT 来消除消息。

先 `GET /api/health` 确认 environment 和目标账本，再读 `/api/capabilities`、`/api/openapi.json`、`/api/guide`。资产读 `/api/family/catalog` 获取真实 ID、revision、日期范围，再读 `/api/family/command-schema` 获取推荐命令 JSON Schema 与语义规则。schema 描述当前推荐入口；条件约束由服务端执行，旧兼容字段仍见 [API 文档](api.md)。

## 连接与授权

客户端按脚本所在位置选择默认环境，与当前工作目录无关：开发源码默认 `dev`／8877；`runtime/releases/` 内的发布包（包括经 current 调用）及带 `release.json` 的发布包默认 `production`／8765。合成回归显式传 `--environment dev`、临时回环地址和精确合成账本路径。可用 `target` 离线查看选择结果；这不代表已连接服务。`--base-url` 兼容标准端口并推断对应环境；自定义端口必须同时指定环境，标准端口与显式环境不一致则拒绝。

正式日常工作区使用其 `./zhixing` 入口，固定正式地址与数据目录，并在每次调用时使用 current 指向的安装版本。这里的示例命令前缀 `python3 scripts/ai_client.py` 应替换为 `./zhixing`，不用切回源码目录。日常工作区明确指定正式账本，但每次业务写入仍须符合用户授权。不可把开发修改回灌正式账本，服务不在线也不自动安装或切换版本。

`scripts/ai_client.py` 只访问回环地址，拒绝重定向；每次 API 请求前通过 health 校验应用和环境，传 `--expected-data-dir PATH` 时还校验精确账本目录。校验失败或离线就停止，不回退到其他服务。bootstrap 建立 Cookie 会话，CSRF 仅保存在进程内。无需从浏览器复制凭据，不能关闭 Host/Origin/CSRF 校验。`read /api/bootstrap` 自动隐藏令牌。

1. 读取原记录、作用域及 revision；同名结果不猜ID。
2. 资产和交易使用服务端 preview。组合资料、笔记没有独立 preview 时展示原值和拟保存差异，不能声称服务端已预览。
3. 核对用户对目标、金额、日期和操作的授权。已有明确授权无需重复确认；新范围或覆盖需要核对。
4. 使用预览/读取的原版本号提交：账本用 expected_revision，研究订阅用 expected_research_revision，更新设置用 expected_settings_revision。不能混用，不能以重新连接时的新版本替换。`--confirm-write` 只是调用者声明授权，不能替代人的授权。
5. 按保存响应的正式 ID 回读并核对。新记录预览 ID 是暂定值，不能直接拿来查提交结果。

409表示未写入，应重新读取、比较变化并重新核对；不自动换版本覆盖。网络超时结果未知，先回读核实，禁止盲目重试。账本接口没有通用幂等键，不自动设置 allow_duplicate。公共数据更新支持 request_id：同一 ID 与原负载只返回已受理结果，不会重新执行；负载改变返回409。结果未知时保留原 ID，先按正式 ID/job_id 回读。已确认失败后另发一次精确重试，使用新 request_id 并保留失败项原 resource；客户端不会自动重试。

## 资产快照示例

请求文件放在源码外的本机工作目录；以下为合成示例，ID 与 revision 必须替换为刚读取并核对的值：

```json
{"operation":"valuation","payload":{"item_id":"ID_FROM_CATALOG","effective_on":"2026-10-01","household_amount":"123456.78"},"expected_revision":42}
```

```sh
python3 scripts/ai_client.py read /api/family/catalog
python3 scripts/ai_client.py read /api/family/command-schema
python3 scripts/ai_client.py preview ../local/work/request.json --path /api/family/preview
python3 scripts/ai_client.py write POST /api/family/commands ../local/work/request.json --confirm-write
python3 scripts/ai_client.py read /api/family/catalog
```

valuation 是该日完整余额，不是增减额。补录不覆盖后续日期；早于首笔时要显式 extend_start。item 编辑保留类别/币种；计划只产生提醒，不扣款。links/unlink 只改变资产引用，不修改组合。income 更正用原 record_id，收入不会自动增加现金。全系统提醒复用 `/api/reminders`。

金额为精确十进制字符串，null为未知，0为已知零；UI的万/亿不改变API原值。总览默认月比上月末、年比上年末，趋势分析使用 comparison=previous（月比上月末、年比上年末）；只有明确要求同比时才用 comparison=yoy。所有余额是时点量，不能把月末余额相加当总资产。

标的更名先从 `capabilities.instrument_name` 定位写入口，再读 `/api/bootstrap`，按市场、代码与 ID 核对标的和原名称。展示全局显示名的变更，使用原 `expected_revision` 提交 `write PUT /api/instruments/ID/name REQUEST.json --confirm-write`。没有独立预览接口。提交后按同一 ID 回读 bootstrap 和 instrument 审计，409 或超时先回读。更名独立于交易和组合关联。

分红出现 needs_review 时，先读候选的 recorded_event、review_basis、review_changes 和 revision，展示实际金额、日期、税费与锁定汇率。用户明确要求保留实际到账并结束核对时，用 `write POST /api/dividends/NOTICE_ID/review REQUEST.json --confirm-write`，传 portfolio_id、event_id、mode=keep_recorded、原样 review_basis、原 expected_revision 及 reason。无独立服务端预览，展示上述原值与仅保存结论的影响即可；不经 events 接口重存流水。手工分红完成核对后按 decision.event_id 保持关联；更正日期后仍核对同一流水，不新增分红。只更正备注或税费不回退最近已接受的权益基准；已知权益变为未知仍需核对，null不补0。提交后回读候选、reminders 和 dividend_decision 审计；409或超时不自动重试。港股明确保留原流水后，单独的 Yahoo HKD 参考值变化不重开核对。场内基金分红须通过同代码除息日净值标记的校验，异常日期查 jobs 的 dividends 子项。参考值微调规则见 [API 文档](api.md#分红核对保留实际到账)。通知已读、关闭弹窗及刷新来源都不构成业务核对授权。

## 近期功能与 AI 入口

以下入口与页面共用后端；只读命令统一使用 `read PATH`。`write` 仍须有明确授权及 `--confirm-write`，不能因为功能可发现就推定允许写入。

| 功能 | 读取／核对 | 保存或客户端入口 |
|---|---|---|
| 组合介绍 | bootstrap 的 portfolios[].description | POST/PUT portfolios；省略或null保留，空字符串清空，最多10000字 |
| 全部组合的资产、累计与区间收益 | GET /api/portfolios/overview | portfolio_overview()；含归档组合，读取实际估值日期及暂估标记 |
| 持仓收益、净投入与分红拆分 | dashboard?view=summary 的 capital_summary、holdings | investment_summary(P)；金额/null及计算口径由后端给出 |
| 持有／清仓状态与交易类型 | holdings[].quantity、performance.cost；交易 actual.closed、filters.kind、facets.kinds | 标的无份额且累计成本为0是尚未买入；交易状态指单笔批次，不代替当前标的状态 |
| 最近交易日／本周行情 | instrument_detail(P,I).market_changes | 只读；输出实际取价日期、序列与缺失原因，行情不等于个人收益 |
| 投入计划与剩余分配额 | plan_check(P,I,target_pct)，同时读取 labels 的原revision | 两次读取revision一致后PUT计划，可同事务保存holding_labels；计划超配与现金缺口分别说明，均为非阻塞提醒 |
| 删除未买入计划 | purchase_plan_deletion_preview(P,I)，核对标的、原计划及将移除的本组合标签 | DELETE /api/portfolios/P/purchase-plans/I，携带预览 revision；回读本组合中是否还关联该标的 |
| 持仓分类与分布 | holding_labels(P,I)、holding_distribution(P,basis) | 独立labels PUT或计划PUT；只影响当前组合同标的，不改历史流水 |
| 保留实际分红并完成核对 | dividends候选、recorded_event、review_basis | POST dividends/ID/review；原revision、原依据及原因，回读原流水与审计 |
| 按日期归档／恢复资产 | family/catalog、command-schema及preview | family/commands 的archive/restore；不删除历史、不自动产生收款 |
| 指数建档与资料分页 | collection_preview、research_index、research_part | preview范围后原研究版本保存订阅；跨页固定analysis_id |
| 指数条件试算 | 当前analysis_id及用户指定假设 | `scenario INDEX_ID REQUEST.json`；只读，无需写入确认开关 |
| 公共数据更新 | update_settings、data-updates/jobs及单个job_id | write保存策略或提交更新；区分settings版本与request_id |

研究试算请求文件示例（合成假设，替换刚读取的真实analysis_id）：

```json
{"analysis_id":"ANALYSIS_ID_FROM_DETAIL","basis":"price","metric":"pe","target_multiple":"12","change_ratio":"0.1","reference_window":5,"accept_fixed_basket_assumption":true}
```

```sh
python3 scripts/ai_client.py scenario INDEX_ID_FROM_LIST ../local/work/scenario.json
```

它只计算条件关系，不新增指数、不触发采集、不改账本。analysis_id过期返回409；全收益或数据不足不能强行试算，条件结果不代表预测。已安装的日常工作区仍用 `./zhixing` 替换命令前缀。

## 验证与维护

红利再投可用 `Client.reinvestment_estimate(portfolio_id, instrument_id, date, gross_amount)`，或 `read /api/portfolios/P/instruments/I/reinvestment-estimate?date=2024-06-03&gross_amount=10` 获取只读估算。UI与AI共用该计算：仅场外基金同日自动单位净值，份额按总价÷净值四舍五入至两位。返回null时不得填0，不用股票收盘价、相邻日或累计净值代替。实际确认单优先；核对后显式填 reinvest_quantity，再预览、按预览revision提交并回读。详见 [接口文档](api.md)。

`python scripts/verify.py --browser` 包含标准库客户端真实 HTTP 回归：能力发现、预览不写入、快照回读、旧 revision 拒绝、收入更正、组合关联及笔记作用域。只能使用临时合成 test 账本。`source_check.py` 只收集白名单技能文件，私人请求、笔记及账本不得进入技能包。

后续迭代须按 [AGENTS.md](../AGENTS.md) 同步维护能力契约、客户端、文档、技能和验证。

历史买卖补录、更正时核对 preview.split_changes 的 before/after 及 lots；确认后按 SPLIT 原ID读取交易详情 facts.split，对照 quantity_before/quantity_after 和参与批次。此前卖出份额、除权日及之后新买入不参与；当日卖出使用已调整份额。补录后全部平仓可返回 no_position，保留历史记录且不新增送转。提醒消失与流水保留含义不同；不能因为已确认记录仍在列表就再次确认，同日重复送转返回422。

确认请求保留 todo.prefill.source_split_id；verify_split 必须更正 todo.event.id。核对 todo.notice 的登记日和除权日，送转日期用经核对的除权日；已录日期／倍数与公告不符时不重复新增。旧记录只凭登记日与同倍数匹配是核对候选，不能当作已证实关联或自动修正日期。更正也使用预览原 revision，按用户授权保存并回读。

收入累计和占比读取 `/api/family/income?year=YYYY` 的 `summary`（字段见 docs/api.md），不在客户端把缺失金额补零或重复汇总更正版本。关联台账金额用 `items[].value`，分类金额用 `categories[].value`；预算型未投入预算不计入资产。

送转股/拆股：读取 capabilities.corporate_actions 与 dashboard/instrument_detail 的 todos。先核对公开公告和实际持仓，使用 `SPLIT` 专项预览，再按原 revision 提交并回读份额、成本和收益。10送8转12填 factor=3，总成本保持不变；现金分红、扣税另行核对。若送转没有出现在 todos 中，先检查 jobs 中 corporate_actions 同步状态并重试，不要用前复权价格掩盖缺失的份额调整。确认后回读独立 SPLIT 流水；原 BUY 数量仍是历史成交数量，不应变成送转后的数量。同步公告不等于确认记账，不得因价格骤降自动造流水或用前复权价格改写历史买入。资产相邻期变化包含新增/移除/份额调整，原样报告 scope_changed/reason，不能称为投资收益率。

删除流水先用客户端 `deletion_preview(event_id)` 或 GET `/api/events/ID/deletion-preview`，逐项核对 `items`（含共享批次），再以原revision、完整event_ids、cascade=true和原因提交DELETE。旧接口默认仅单条/转移对，不会静默扩大范围。过期409或超时先回读，不能自动换revision、重试删除。按返回 `deleted_event_ids` 回读交易详情的 `cancelled=true` 及持仓；审计保留。Dashboard 的 `curve_quality` 解释历史缺段，不能为了补线自动确认送转或将null补零。

### 持仓标签与当前分布

通过capabilities.holding_analysis发现接口。用客户端holding_labels(P,I)读取当前归属和revision，展示scope/direction/detail差异后按原revision PUT labels；409或超时先回读核对，不自动覆盖。无独立标签预览接口，不声称服务端预览已完成。holding_distribution(P,basis)读取完整树与同一后端分母，不能只统计已打标签持仓或自行重复相加父子节点。股票可自动识别市场，基金不按上市地或币种猜投资敞口。旧/api/tags统计已退役；旧风格/主题tags已清除，保留资产类别和地区筛选；计划写入不传tags，地区不再接受混合。总资产value为null时可说明known_value是已知资产合计，不能冒充完整总额。

## 指数研究

`research_indices(page=1, method='mcw', valuation_group='all')` 读取与指数研究首页相同的分组列表。四个估值指标各自有观测日期，原始 5 年和 10 年分位见 `valuation` 中对应指标的 `windows`。主指标的估值位置与分级见 `valuation_position`，股息率使用反向位置。涨跌幅带起止取价日期。最近三个完整自然年的结果位于 `changes.annual`，缺少有效基准时返回 null。

可传 `sort_by='annual_1'`、`'annual_2'`、`'annual_3'`、`'ytd'`、`'monthly'` 或 `'daily'` 对匹配列表排序。`sort_direction` 支持 `'asc'` 和 `'desc'`，默认降序。排序先于分页，缺失值置后。

读取 capabilities 和研究目录，调用 `collection_preview(code, modules, history_start, primary_valuation=...)` 展示指数家族、拟用主指标、核验日线的来源和日期、范围、预计调用数和原 research_revision。目录内跨市场指数使用官方配对和日线核验，其他新指数通过理杏仁核验。在用户授权范围内以 `write('/api/research/collection', payload, 'PUT')` 提交预览返回的 modules、primary_valuation、history_start，并带上 enabled 与 expected_research_revision，再按 id 回读。首次保存返回全量同步的 job_id。按该 ID 回读任务，避免另发重复更新。后续可显式使用 `refresh_index`，或从详情的 `collection.resources` 选择对应资源并提交稳定 request_id。

`research_index` 默认 mcw；`research_studio` 读取四指标、三方法、0/3/5/10窗口；不再提供收益回测或指数重叠。0仅表示本地已采集全历史，不能宣称供应商完整历史。版本变更后重读；来源失败保留旧值，缺失不补零。旧源overall/arithmetic仅用于回看。指数汇总财务、固定公司篮子、同篮子同比不得混用。

Token只能由用户在本机「研究工具 → 数据管理 → 理杏仁数据源」输入。AI不读取凭据文件、不给命令行传Token，不把Token写进请求JSON文件、日志或聊天。可读 `/api/settings/lixinger` 的非敏感状态和计数。本机不设调用次数上限，失败也记录尝试；本机计数不能代表供应商剩余额度。首次同步随用户确认新增触发，日常更新由用户提交。供应商权限或额度阻断须核对后显式恢复。

详情与快照历史读取不触发网络；只有用户授权的显式更新调用供应商。价格、估值、权重、财报和披露日各自保留。财务按年度/一季度/半年/三季度跨年读取；源端曲线按历史当期成分加总，当前可得版本不是严格PIT；mcw条件试算须明确接受全部成分同比例变化假设，不声称已核验实际点位映射。完整规范见 [研究合同](research.md) 与 [API](api.md)。

`research_source_check(index_id, analysis_id)` 仅在明确授权联网核验时调用；先以mcw价格口径读取analysis_id，结果保存在独立官方参考快照，不改变理杏仁历史或估值分母映射。

## 市场水位

使用`market_waterlevel(method='mcw', asof=None, facts=True)`或`GET /api/market-waterlevel/facts`读取版本化事实。指数估值来自现有理杏仁快照；全市场估值来自理杏仁 A股全指1000002，按请求选择估值方法。两综指、宏观与债券来自公开来源。comparisons提供两综指共同基准月及对齐说明，完整dashboard包含曲线。GET读取本地数据。先核对asof、实际观测日、来源、method_version、窗口样本、状态和缺口，再解释历史位置。

用 `market_data_sources()` 读取可更新指标、实际观测日期和 `schedule`。实例开启自动更新时，每周五北京时间 18:00 增量更新，可在更新历史查 `trigger=market_schedule`。用户授权手动更新时，通过 `write('/api/data-updates/run', payload)` 提交该项返回的 `resource`，例如 CPI 的 `group=prices, series=cpi_yoy`。沪深两融余额 `margin_balances` 与理杏仁 A股全指样本成交额 `market_turnover` 独立更新，金额为 CNY 元，均不计分。省略full走增量，显式true执行全量校对，保存收据并回读任务状态。复用稳定request_id，结果未知先查询。full_market 与 market_turnover 消耗理杏仁基本面额度。

用 `market_activity(asof=None)` 读取两融余额与成交额的逐交易日观测，核对返回的 asof 与 research_revision，再与事实包对应。

冻结流程为读取事实包→按原analysis_id、method、asof、expected_research_revision提交`write('/api/market-waterlevel/snapshots', payload)`→精确ID回读→按需下载export。409先重新读取核对，不替换成最新revision强行提交。冻结保存不触发供应商调用，报告独立于后续来源更新。

各估值方法不重复计分，评分固定指数mcw与理杏仁 A股全指中位数；负PE/PB不能用另一口径代替。均值是连续60/120个完整月末观测，分位严格小于当前值，与指数研究页的日频均值/分位不同。历史日期只截取当前可得修订版，不能用于声称严格PIT的回测。未接入项、缺类后的覆盖变化和M1口径变化必须保留。程序规则摘要不是模型生成分析；如需解释，应明确区分事实与推断。详见[市场水位契约](waterlevel.md)。
