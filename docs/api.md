# 本机 API：给 AI 与自动化客户端

版本：0.8.5。与浏览器共用 FastAPI 服务和领域校验。开发默认 origin 为 `http://127.0.0.1:8877`；正式实例为 `http://127.0.0.1:8765`，调用前核对目标环境。API 不连接券商、不下单。

## 资产分析 API

项目技能与完整调用流程见 [AI 接入约定](ai.md)。`GET /api/family/command-schema` 返回推荐 item/valuation/links/unlink/income 命令的 JSON Schema、条件语义及回读路径；capabilities.family 提供发现入口。底层 family 路径保留兼容。

以下接口使用同一会话、CSRF 与 Decimal/null 约定，资产写入不能修改投资组合表。

| 接口 | 内容 |
|---|---|
| `GET /api/family/overview?date=YYYY-MM-DD` | 当前／历史资产投影：总资产、负债、净资产、分类、来源日、关联；schedules 仅今天至未来35天 |
| `GET /api/family/catalog` | 资产记录所有版本、金额历史、关联、旧计划／设置及组合可选项、revision |
| `GET /api/family/history?grain=month&comparison=previous&start=YYYY-MM-DD&end=YYYY-MM-DD` | 月／年期末金额与变化额、变化率；资产页面使用 previous（月比上月末、年比上年末）；接口兼容 previous/year_start/yoy，yoy 未结束期间比上年同日，最大30年 |
| `GET /api/family/income?year=2026` | 指定年份12个月收入与分类合计、年度累计 summary、明细、revision；默认当年，不支持未来年份 |
| `GET /api/family/revision` | 只读revision和today（上海日期），用于可见页面的2秒增量刷新判断；即使没有业务写入，跨日也会重新读取 |
| `GET /api/family/reminders` | 今天至未来35天的还款／保费／续保提醒及本机通知状态，逾期移出列表 |
| `POST /api/family/preview` | 执行同一校验与投影，SAVEPOINT 回滚，不增加修订和审计 |
| `POST /api/family/commands` | 原子校验修订并提交，返回 result 与新 revision；提交后回读 |
| `GET /api/notifications` | 全局本机授权、启用状态、待发数量、覆盖日期与错误；旧 `/api/family/notifications` 为兼容别名 |
| `POST /api/notifications` | action=enable/disable/refresh/test；enable 请求系统权限，test 发送不含财务信息的测试通知；保留旧路径别名 |

命令外壳：`{"operation":"item","payload":{},"expected_revision":42,"record_id":null}`。expected_revision 必须是整数，省略／类型错误422，过期409且不写入。record_id 指定要编辑的记录；未知命令字段拒绝。

| operation | payload |
|---|---|
| item | name、category(cash/fixed/investment/receivable/insurance/debt)、currency(CNY/HKD/USD)、effective_on；household_amount 为实际持有/承担金额（非保险必填非负，保险可null）。编辑时同时追加资料与金额历史。债务可附 started_on、payment_plan；保单可附 insured、coverage_type、coverage_amount、policy_end、policy_lifetime、payment_plan。类别／币种不可修改 |
| valuation | 余额快照追加：item_id、effective_on、household_amount；旧 amount/restricted/interest/fees/source 继续支持。早于项目首笔日期补录时，现金/固定资产/理财/应收款可显式传 extend_start=true，预览后提前统计起始日，不覆盖后续快照或资料；其他类别拒绝提前 |
| links | portfolio_ids(1–100个不同组合ID)、effective_on(默认今天)；按100%关联，整批事务、任一失败不写入 |
| unlink | link_ids(1–100个不同关联ID)、effective_on(默认今天)；当天起不计入，保留组合和历史，允许同日解除后重新关联 |
| income | kind(salary/rent/fund/custom)、month(YYYY-MM)、currency、amount(非负必填)；custom 必填 name，其他名称自动设置。date 可选且必须在该月，默认月末或本月今日。record_id 更正时追加版本，统计只用最新版本 |

item 省略旧字段时保持原资料；仍兼容 economic_id/share/amount/household_restricted/platform/closed_on 等旧接口。非保险界面不关联家庭成员。旧 link、settings、schedule、reconcile、closure 命令继续用于兼容，日常界面已移除手工计划、处理事项、费用目标和盘点入口。全部命令可附 reason。

catalog.bounds 返回 first_date、asset_first_date、income_first_date、last_date、years、income_years；空账本起始日期为null、年份为空数组。years 从第一笔资产或收入记录所在年连续到今年，income_years 只含实际收入年份。

history.rows[].components 为 id/name/category/value 数组，支持类别内各项目的堆叠；changes[指标] 增加 scope_changed 和 reason。变化率的范围比较按当前指标独立计算，旧行级 scope_changed 仍表示全体项目变化。缺金额和缺对比基线返回null，不自动填0。

overview.debt_summary 返回 total_debt、month、month_due、known_month_due、available_cash、cash_coverage、unconfigured（未配置项目名称）、missing_fx。金额为CNY字符串，cash_coverage 为倍数。month_due 是按当前规则计算的整月计划金额，不是剩余待还；不据此写入余额。存在未设置的负债或缺汇率时 month_due/cash_coverage 为null；应还为0时 cash_coverage 为null。

payment_plan 为对象：mode=none 表示不设置；债务支持 once/fixed/annuity/equal_principal，保险支持 once/annual。首次付款 first_due 必填；periods 为整数1–600（保险最多100年，once固定1）。fixed/annual/保险once 必填 amount；annuity/equal_principal 必填 annual_rate（百分数，0–100）。债务once和分期计算以当时实际欠款为本金，服务端保存 principal，客户端不可指定。规则不变时更新余额不会重置原计算本金；调整规则会用当前欠款重新计算，此时首次还款日不得早于本次金额记录日；未修改规则时保留原起始日。

每期按月／年从首次日期推算，短月取月末，后续仍按原日期锚定。等额本息按月利率计算固定本息；等额本金固定本金加剩余本金利息；金额四舍五入到分，末期结清舍入余数。预览 result.payment_preview 包含首期、末期日期、期数与总付款。overview.items[].payment_end 为规则结束日期。规则只生成预计提醒，不写实际交易，不自动减少欠款和现金。配置规则后旧手工同类提醒保留存储但不重复展示。

应收款只参与资产和金额历史，不生成计划；旧 started_on/due_on 保留但不展示。负债余额为0时不再产生还款提醒。保单 policy_end 生成续保提醒；policy_lifetime=true表示终身保障且policy_end必须为空，仅取消到期续保，不取消缴费计划；日期过后从列表移出。提醒没有“已处理”工作流，不因为过期清零金额。

收入不是资产类别，不进入资产余额／净资产，不自动入账现金。income.rows 中 recorded=false 为未记录；缺汇率时 total 或分类 groups 对应值为null。外币收入按记录 date 当日或此前最近可得历史汇率换算，records[].fx 显示来源，日期不跨月；金额更正保留版本。不同收入记录相加，同一记录更正不重复累计。

overview.known_assets 为已知小计；有金额／汇率缺失时 total_assets/net_assets/debt_ratio=null。组合资金变化仅给出检查手工现金的提示，不要求额外确认才显示合计。分类占比针对正资产合计；负债单列。history.values 是期末金额，changes.amount 是差额、changes.rate 是比例；缺比较期、基期非正或缺值时 rate=null；范围变化计入总金额变化，scope_changed=true 且 reason 标注含新增、移除或份额调整，不再因此把 rate 置空。partial 表示未结束期间。历史按当前资料重算，旧盘点快照仍保留。

macOS 原生计划提前登记未来366天，到期事项提前7天、1天及当天09:00（上海时区）提醒；页面只显示未来35天。分红候选出现 pending/needs_review 时发送一次泛化通知；相同候选重复刷新不重发，确认／忽略后取消，资料实质变化后重新提醒。所有通知点击使用 `?reminders=1&focus=reminder_id` 定位全局提醒中心；旧分红 URL 保留兼容。

通知授权是本机 OS 状态，不是付款状态。pending_count 是系统待发数量，missing_count/error 表示安排有缺失；不能据此声称用户已收到或已付款。开发／测试默认关闭真实通知，须显式 enable；所有写操作要求本地会话和CSRF。

## 发现与安全

1. `GET /api/health` 验证 application=`portfolio-journal` 和版本；路径信息仅供本机定位。
2. `GET /api/capabilities` 获取当前能力；`GET /api/openapi.json` 获取路由和核心请求 schema。
3. `GET /api/bootstrap` 获取组合/标的 ID、当前 revision、CSRF token，并接收环境隔离的 HttpOnly cookie（名称见 capabilities）。token 只在内存保存，**不写日志、命令行参数或文件**。
4. 写入带同一会话的 Cookie、`X-CSRF-Token` 和 `Content-Type: application/json`。若发 Origin，必须与实际服务 origin 完全相同。外站 Origin、错误 Host、缺失或错误会话被拒绝；不开放 CORS。

这是可信个人机器上的单用户服务。会话与 CSRF 防外站滥用，不是本机恶意程序隔离或多用户鉴权。不要绑定公网，不要绕过同源校验，不要复制正式账本到共享机器。

## 数据约定

- 金额/份额/价格/汇率返回十进制字符串，建议请求也用字符串；`null` 表示缺失或无法确定，不能填0代替。
- 日期 `YYYY-MM-DD`；时间戳含时区。基础币种 CNY；HKD/USD 的汇率方向均为 `1 foreign = x CNY`。
- `return_on_cost`、`xirr`、`weight` 为比例：`0.05` 是5%。`target_pct`、`target_annual_pct` 为百分数：`5` 是5%。
- 交易、计划始终绑定 `portfolio_id`；投资笔记同时绑定 `portfolio_id` 和 `instrument_id`。相同证券在不同组合的交易和标签独立。不要用名称猜测 ID。
- 读取不改写旧流水。更正/删除会保留审计记录；分红公告未经确认不改变资产。
- 组合/标的/逐笔收益都含实际入账净分红和费税。投资累计收益率与 XIRR 排除未投入现金；API 为审计仍返回逐笔分红分摊，UI 在持仓列表和顶部收益等式中汇总展示，不增加逐笔累计分红列。

## 读取接口

| 方法与路径 | 参数/结果 |
|---|---|
| GET `/api/bootstrap` | portfolios、instruments、allocations、revision、同步状态等；不要持久化 csrf_token |
| GET `/api/portfolios/{pid}/dashboard` | `start`、`end`、`grain=day/week/month/year`、`view=full/summary`（默认full）；不传日期为当前。summary保留相同capital_summary、holdings、todos等当前财务值，省略curve、curve_quality、calendar、periods、target、benchmark及selected_*历史投影 |
| GET `/api/portfolios/{pid}/instruments/{iid}` | 当前组合标的详情、曲线、收益、关联交易、分红公告；旧全量 transactions 保留兼容，新列表使用分页接口 |
| GET `/api/portfolios/{pid}/transactions` | 见分页约定 |
| GET `/api/portfolios/{pid}/transactions/{event_id}` | 本笔流水 facts、actual、同组合同标的 neighbors、revisions；不含复盘正文 |
| GET `/api/portfolios/{pid}/sale-lots` | `instrument_id`、`date`；可选 `exclude_event_id`、`sequence`，返回可卖批次 |
| GET `/api/portfolios/{pid}/dividends` | 可选 `instrument_id`；公告、权益、状态、suggested_event、revision、review_basis、review_changes、review_decision |
| GET `/api/portfolios/{pid}/holding-analysis` | `basis=holdings/assets`；当前层级nodes、holdings、total、eligible、reason、revision；每标的仅计一次 |
| GET `/api/portfolios/{pid}/instruments/{iid}/labels` | 当前标签、自动识别范围、选项、同组合建议和revision |
| GET `/api/reference-rate` | `currency`、`date`；历史参考汇率，交易确认后锁定 |
| GET `/api/portfolios/{pid}/instruments/{iid}/reinvestment-estimate` | `date`、`gross_amount`；同日场外基金单位净值估算份额，只读 |
| GET `/api/jobs` | 同步任务及 syncing 状态；检查 items，不只检查 HTTP200 |
| GET `/api/revisions/{entity}/{id}` | 修订记录；entity 使用服务支持的实体名，如 event、portfolio；笔记不能通过此接口读取 |
| GET `/api/portfolios/{pid}/export.csv` | 私人交易数据，不得提交 Git |
| GET `/api/backup` | 一致性完整ZIP备份，私人数据，不得提交 Git |

### 分红核对：保留实际到账

`GET /api/portfolios/{pid}/dividends` 的 `needs_review` 候选返回 `recorded_event`、`event_id`、`revision`、`review_basis` 和中文 `review_changes`。读取只计算差异，不改账。核对实际金额、税费、到账日、再投份额及三项锁定汇率后，可选择更正原流水，或 `POST /api/dividends/{notice_id}/review`：

```json
{"portfolio_id":"P_FROM_READ","event_id":"E_FROM_READ","mode":"keep_recorded","review_basis":{"COPY":"完整原样使用候选的 review_basis"},"expected_revision":42,"reason":"按合成凭证核对，保留实际到账"}
```

`review_basis` 是候选返回的完整对象，不是上述占位示例：包括 eligible_quantity、per_share、estimated_gross、record_date（权益计算截止日）、notice_record_date（来源登记日）、pay_date（来源派息日）、ex_date、currency、source、split_restore_factor。未知为 null。此入口没有独立预览：展示读取的原流水与核对依据，并明确只保存核对结论，再按授权提交。额外金额或日期修改字段被拒绝；需要更正金额时用既有预览和 confirm/更正接口。

返回 `{decision, event, revision}`：event 与原记录一致，不新增或更新任何流水字段；decision 保存 mode、event_id、event_date、核对依据、原流水 fingerprint、说明、时间和 review_revision，审计位于 `GET /api/revisions/dividend_decision/{decision.id}`。提交后回读候选的 confirmed/manual_recorded 与 `/api/reminders`。required expected_revision 必须是非负整数；旧 revision 返回409；未找到待核对原记录、原记录已不是本标的的分红/再投、作用域/事件/依据不匹配及无说明返回422；均不写入。超时先回读，不自动重试。

仅 Yahoo 每份参考值同时满足相对变化 ≤0.01% 且绝对变化 ≤0.0001 派息币种/份，才视为微调；基准固定为最近核对值，不逐次滚动。estimated_gross 是派生参考值，不单独用完整字典比较。权益份额、有效日期、币种、拆分还原因子仍精确比较，其他来源的每份变化仍需核对。旧依据未记录来源时，变化先提示核对，不推定旧来源为 Yahoo。来源日期/因子缺失不清空已核对依据或原流水，来源补全或实质修正仍可提醒。计算权益由已知变为未知仍需核对，null不当作无变化或零；旧依据缺少币种时按原已确认流水的币种比较，币种修正仍提醒。读取或升级不自动完成历史核对。核对决定通过 event_id 在同组合、同标的、有效分红/再投事件内保持关联，不因手工流水或来源除息日更正而转成新增候选。只改备注、税费等原流水事实不会让最近接受的权益依据回退；只有显式分红确认/复核推进该依据。原流水发生日期变化单独提示复核；fingerprint保留审计用途，不用它使权益依据失效。

同一未完成事项按公告、组合、权益、固定来源参考基准与最近核对版本去重，Yahoo参考微调、抓取时间和说明刷新不重复派发；金额变化仍更新核对页。来源基准只在实质变化时更新，实质金额修正即使尚未完成核对也会重新通知。完成后撤销对应通知，后续实质变化产生新事项版本。系统通知状态与业务核对状态分开，不声称通知已被用户看见。

### 筛选与分页

新客户端始终显式发送 `page`：

```text
GET /api/portfolios/{pid}/transactions?page=2&page_size=20&market=CN&asset_class=权益
```

中文分类参数须用标准 URL 编码。所有筛选为 AND：

| 参数 | 含义 |
|---|---|
| page | 从1开始；超过末页时校正为最后一页；无结果为第1/1页 |
| page_size | 默认20，最大100；UI 提供20/50/100 |
| instrument_id | 仅本组合某个标的；不能用另一组合的标的范围 |
| market | ALL、CN、HK、US、FUND、CASH、OTHER |
| asset_class / region | 当前计划分类；旧国内/海外兼容为A股/海外其他 |

返回：`items`、`total`（筛选后）、`unfiltered_total`（本组合/标的范围内筛选前）、`page`、`page_size`、`page_count`、`filters`、`facets`、`portfolio_id`、`instrument_id`、`revision`。每个 item 有 event、instrument、facts、actual 和 can_view_profit/can_edit/can_cancel。

排序固定 `date desc, sequence desc, id desc`；先筛选再分页。每次翻页必须携带相同筛选参数。改变筛选回第一页。多页遍历时比较 revision；若发生变化，应从第一页重新读取以避免跨快照遗漏/重复。当前是 offset 分页，不宣称跨并发写入的快照游标。无 page 且无筛选时兼容返回旧全量数组；不要在新 AI 工作流依赖这个返回形态。

## 写入与协同

核心写入接受 `expected_revision`，在同一 SQLite 写事务中核对。版本来自最近一次 bootstrap 或交易预览。人、AI 或行情同步改变账本版本后，旧版本写入返回 **409**，没有写入任何行。重新读取并预览，再决定是否提交；不要自行把版本替换成最新并覆盖。

| 方法与路径 | 必要内容 |
|---|---|
| POST `/api/portfolios` / PUT `/api/portfolios/{pid}` | name、planned_amount、start_date；可选 description 为最多10000字的纯文字组合介绍（保留换行）；目标、基准、归档等见 schema |
| PUT `/api/portfolios/{pid}/instruments/{iid}` | target_pct、asset_class、region；单标的比例0–100；组合资金不足仅提醒，不限制合计≤100 |
| POST `/api/events/preview` | 拟写入的完整事件；更正带 id。只读计算，返回 revision、event、现金变化、warnings、duplicate_of、instrument_merge |
| POST `/api/events` | 与已核对预览相同的输入；expected_revision；服务端再校验 |
| PUT `/api/events/{id}` | 更正后的完整事件、change_reason、expected_revision；从原记录合并，不丢弃隐藏历史字段 |
| GET `/api/events/{id}/deletion-preview` | 只读关联删除预览：revision、event_ids、items、can_delete、validation_error |
| DELETE `/api/events/{id}` | reason、expected_revision；cascade=true 时必须提交预览全部 event_ids，逻辑删除并保留修订；默认兼容单条/转移对 |
| POST `/api/dividends/{notice_id}/review` | 保留已确认流水并完成核对；请求与错误见下文，必须附候选原 revision |
| POST `/api/dividends/{notice_id}/confirm` | 在候选 suggested_event 上核对税前总额、税费、到账、再投信息，保留 dividend_basis，附 expected_revision；权益变更时拒绝旧核对 |

组合介绍随组合对象在保存响应、bootstrap.portfolios 和 dashboard.portfolio 返回。创建时省略或null为空；编辑时省略或null保留原介绍，空字符串清空。旧组合可没有该字段，读取不回写。超长或非字符串返回422；过期revision返回409且不保存介绍或其他变更。介绍仅作展示，不参与财务计算。

旧交易/计划接口的 `expected_revision` 兼容为可选；资产、投资笔记接口和 AI 客户端写入强制提供。不能把 CSRF token 当作并发版本。

### 交易输入

买入 `BUY`：portfolio_id、date、instrument（market/code，名称可选）或 instrument_id、quantity、gross_amount、transaction_costs、fx_rate。gross_amount 不含费税；单价由总额÷份额计算。新标的只在实际提交时建档。

卖出 `SELL`：instrument_id、date、gross_amount、transaction_costs、fx_rate；从 sale-lots 取得 buy_event_id，提供 `sell_lots: [{buy_event_id, quantity}]`，或明确 `sell_all: true`。服务端校验组合、标的、日期、可用份额与后续关联。

现金分红 `DIVIDEND`：instrument_id、date（除息日）、pay_date、gross_amount、tax、fx_rate、pay_fx_rate、tax_known、ex_date_known（net_amount 可省略，由总额减税费计算；旧净额输入保持兼容）；未知税额/日期不能标成已确认。再投 `REINVEST` 另提供 reinvest_date、reinvest_quantity、reinvest_gross_amount、reinvest_fx_rate，可选 fee。公开分红必须核对真实到账，不直接把预计金额当作事实。

再投份额估算：`GET /api/portfolios/P/instruments/I/reinvestment-estimate?date=2024-06-03&gross_amount=10`。P/I必须是当前组合已有的标的；金额为不含手续费的再投总价，不再次扣税或手续费。仅 `FUND` 使用自动同步的同日 `raw` 单位净值；不使用人工价、累计净值、复权价、股票收盘价或相邻日期。只读缓存，不触发同步、不记账。

响应包含 `status`、`quantity`（Decimal字符串或null）、`reference`（date/nav/source或null）、`currency`、`date`、`gross_amount`、`revision`、`quantity_decimals=2`、`rounding=ROUND_HALF_UP`、`note`。例如当日单位净值1.25时，10元返回 `status=available, quantity="8"`。份额是总价÷单位净值并四舍五入至两位小数，仅为估算，基金实际规则或确认结果可能不同。`missing_nav`、`unsupported_market`、`rounded_zero` 均返回null份额；缺当日净值时同步历史行情后重查或手填，不以0替代未知。日期无效/在未来、金额非正/非有限、组合或归属不匹配返回422。新接口由 capabilities.reinvestment_estimate 与 OpenAPI 公开。

UI选择红利再投后自动填入估算，日期/金额/标的变化立即撤下旧估算；手改或打开已有正份额记录后保留其值，只有点击“按净值重新估算”才恢复自动。预览和保存使用同一份额；确认前显示估算提示。AI同样先核对估算，再将确认的 quantity 显式传为 reinvest_quantity，经过 events/preview、原revision提交及回读。估算响应不代表用户确认，也不替代写入预览。

### 无浏览器示例

使用 `scripts/ai_client.py`。写请求文件存放在源码外的 `../local/work/`，下列数量和代码只是示例，不是交易建议：

```json
{"portfolio_id":"从bootstrap取得的ID","kind":"BUY","date":"2026-01-05","instrument":{"market":"CN","code":"510300"},"quantity":"100","gross_amount":"400","transaction_costs":"5","fx_rate":"1"}
```

```sh
.venv/bin/python scripts/ai_client.py preview ../local/work/request.json
# 核对返回结果，将 revision 加到原请求的 expected_revision 字段，再提交。
.venv/bin/python scripts/ai_client.py write POST /api/events ../local/work/request.json --confirm-write
# 随后从 transactions 和 dashboard 回读核对。
```

写入超时后不自动重试。先读取最新记录和修订核对是否已成功；如必须重试，保留原 expected_revision，已成功的第一次写入会使第二次返回409。`allow_duplicate` 仅用于另一次确实相同的成交，不能用于绕过重试冲突。尚未实现 Idempotency-Key；不要假定 POST 幂等。

## 其他能力

- `POST /api/sync`：`{instrument_id?: string, full?: boolean}`，启动/排队后台更新；用 `/api/jobs` 查看逐项结果，失败可重试。此操作只更新行情/公告缓存，不自动确认分红。
- `POST /api/dividends/{notice_id}/ignore`：portfolio_id；仅隐藏候选，不计收益。
- `POST /api/csv/preview`：text、category=`events/fx`、portfolio_id。只读校验。`POST /api/csv/import` 同参数整批写入；根据 event_id/指纹查重。没有 expected_revision 保护，AI 客户端不暴露此写入；导入前重新预览并确认整个批次。
- `POST /api/fx`：currency、date、mode=`daily/manual/auto`，非auto时提供 rate；人工估值汇率，不回写交易锁定率。该接口不提供 expected_revision 保护。
- `POST /api/restore`：content（ZIP的base64）、confirm=`RESTORE`。覆盖当前账本，先保留保护快照。必须获明确恢复授权，普通升级不要调用。
- `POST /api/instruments`、`POST/PUT /api/reservations`、`POST /api/transfers`：兼容旧功能，不属于推荐 AI 日常写入；不要以特殊资金事件替代预算模式。
- 手工价格、价格CSV、`/api/prices` 不存在。

## 错误与检查

HTTP 403：同源/会话不匹配，重新建会话，不放宽校验。409：并发冲突，重新读取预览。422：输入或领域校验失败；返回 error 或 detail，修正真实输入，不能删校验。5xx：查看本地日志，先核实是否写入，勿盲目重试。

`/api/sync` 的 HTTP200 仅表示已接受任务；job `partial/error` 必须展示具体项目与原因。行情缺失时保留旧数据和估值日期，不填零或假行情。

真实数据不得放入 issue、提交、公开日志或示例。AI 调试与自动测试只用合成账本。API 通路的验收包括：无浏览器建会话、预览不写入、提交回读、筛选分页、错误会话拒绝、旧版本冲突和正式账本原始行保护。

## 收益分解与分红展示字段（0.6.5）

`dashboard.capital_summary.profit_breakdown` 与 `holding.performance.profit_breakdown` 使用同一结构。`total = price_net + dividends_net + other_income`，均为 CNY Decimal 字符串；`price_net = market_value + sale_proceeds - buy_cost - extra_costs`。

`price_net` 是含汇兑、扣除买卖及非分红费税后的价格收益，含已实现与未实现；`dividends_net` 包含已确认应收、现金及再投净分红，扣关联补税/费用。`other_income` 为利息等，不应混称价格变化。`complete=false` 或金额为 null 时不能补零凑等式。`rounding_adjustment` 仅解释各项显示两位小数时的舍入差，不计入原始收益。

组合与标的收益分解增加 `cash_dividends_net`（DIVIDEND 记录）及 `reinvested_dividends_net`（REINVEST 记录），各自扣除归属该记录的关联补税／费用，仅扣一次；两项已知时相加等于原 `dividends_net`，不改变累计收益、资产、现金流或 XIRR。按原记录类型归集，再投记录内的现金余量仍归该记录；不是新增个人本金或额外资产。两项均包含已确认应收，未到账应收不属于已回收现金；缺少估值的分项保留 null，另一已知分项仍可展示。

分开展示时使用 `component_rounding_adjustment`，解释 `total` 与价格收益、两项净分红及其他收入各自显示两位小数之和的差额。原合并分红的 `rounding_adjustment` 保持兼容，两种舍入差均不是新增收益。

`dashboard.capital_summary.cost` 为“累计投入（累计买入成本）”：历次买入及红利再投的含费税成本，含期初持仓的记账起点成本，不扣卖出或分红，不等于外部累计新增本金。`invested_amount = planned_amount - available_amount` 为净投入；买入减少剩余可投金额，卖出及现金分红到账回补，未到账应收不回补；同额再投为内部资金。金额不在前端重新计算。客户端 `investment_summary(P)` 直接读取同一 summary，能力发现见 `capabilities.investment_summary`。

分红流水 `facts` 增加 `dividend_gross_native`、`dividend_tax_native`、`dividend_net_native` 和 `dividend_tax_differs`；实际币种用 `facts.currency`。字段后缀只作为API标识，界面应明确写 CNY/HKD/USD。税前税后不一致时显示税额等式；不按市场猜税率。录入 UI 填写税前总额和税费，自动计算净额；新增现金分红／红利再投（含公告确认）的“税额已核对”默认勾选，可取消；编辑或重算已有记录保留原核对状态，打开表单不自动写入。API 可省略 net_amount，由 gross_amount−tax 精确计算，旧净额输入兼容。两者同时传入时校验税前−税额＝净额（税额已核对时）。

## 持仓排行与更新原因（0.6.5）

排行无需浏览器或独立计算：读取 `dashboard.holdings`，对 `performance.return_on_cost`、`profit` 或 `xirr` 排序；`null` 放末尾且保留未知，`quantity=0` 标记已清仓，`performance.cost>0` 为已投资标的。字段均沿用既有收益口径，不将各标的 XIRR 平均成组合 XIRR。

`POST /api/sync` 请求可带 `instrument_id`、`full` 和 `trigger`。trigger 支持 `manual_update`、`manual_instrument`、`manual_full`、`retry`、`api_request`（省略时默认）；启动、定时、记账、组合编辑和导入原因由服务端记录，客户端不能冒充。仅同步公开行情，不修改真实交易。

行情缓存的界面分页每页10个标的；bootstrap 仍返回完整 instruments 和 price_status，客户端自行切页，不改变同步目标或历史缓存。

`GET /api/jobs` 与 bootstrap 返回最近20条任务，按 started_at 和创建顺序倒序。界面展示最近5条；后台默认最多保存20条（运行中、成功、部分失败和失败统一计入），运行中任务不清理；进程启动时将残留 running 标记为 error，增加 interrupted=true 并保留原触发原因。任务历史不是业务审计。价格项 saved/unchanged 表示本次全部返回序列中新增变化版本/跳过重复数，count仍是标的主序列点数。`GET /api/capabilities` 的 storage 字段公开保留策略及当前schema。任务增加 `trigger`、`triggers`（合并的原因列表）、`trigger_label`（可读说明）、`queued`、`instrument_id`、`full`。排队合并保留请求原因和全量需求。历史任务可能没有新字段，应显示“历史记录未记录”，不能推断触发原因。trigger 的 `multiple` 只出现在合并任务的响应中。

## 日期汇率与单位成本（0.6.5）

`GET /api/reference-rate?currency=USD&date=2023-06-01` 返回 `reference`（rate/date/source）或 null。它读取当日或此前最近可得记录，不使用全局区间估值覆盖；未来日期返回422。客户端核对来源日期，不把缺失值填为0。

录入前按 `date`、`pay_date`、`reinvest_date` 分别查询，并在预览/提交中显式传入 `fx_rate`、`pay_fx_rate`、`reinvest_fx_rate`。前端日期或币种变化会清空旧请求结果并重查；接口仍保留旧客户端缺省到账/再投率沿用的兼容行为，因此 AI 客户端应显式传入各日期的汇率。不要更改不涉及日期/币种的旧记录锁定值。

到账/再投可附 `pay_fx_source_date`、`pay_fx_type`、`reinvest_fx_source_date`、`reinvest_fx_type`。来源日期不能晚于对应日期；CSV导出保留这些字段。

交易 facts 增加 `unit_after_costs` 和 `unit_after_costs_label`：买入/再投为含费税每份成本，卖出为扣费税每份净回款；与 `unit_price`（未含费税）并列。不适用时为 null，不据此把分红扣税重复加入再投成本。

备份恢复限制：ZIP最大100 MiB，解压后SQLite最大512 MiB。`POST /api/restore` 要求 confirm="RESTORE"，会验证校验码、数据库与账务，并返回 `preserved_backup`。真实备份演练必须指向独立数据目录和代码副本；恢复提升revision，原预览应废弃并重新读取。

## 投资笔记（0.6.6）

作用域前缀 `N=/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes`，两个 ID 都必须匹配记录。标的须已加入当前组合；跨组合或跨标的的 note_id 返回422，不根据全局标的自动共享笔记。

| 方法与路径 | 输入 / 返回 |
|---|---|
| GET `N` | page默认1、page_size默认20最大100；items、total、page、page_count、revision |
| GET `N/{note_id}` | id、portfolio_id、instrument_id、title、body、created_at、updated_at、path、revision |
| POST `N` | title、body、expected_revision；返回保存后的完整笔记 |
| PUT `N/{note_id}` | 完整title、body、expected_revision；保持最初created_at |
| DELETE `N/{note_id}` | expected_revision；逻辑删除，不改交易 |
| POST `N/{note_id}/restore` | expected_revision；撤销同作用域的删除 |

标题1–100字且不换行；正文1–200000字，UTF-8文件不超过1 MiB。body是原始 Markdown，支持标题、段落、加粗与序号列表；浏览器不会执行 HTML、脚本或加载外部资源。列表不返回body，仅返回title、excerpt、时间、available等元数据。文件不可读时保留列表项并报告错误，不默默隐藏记录。

正文、标题、时间都在磁盘 Markdown 文件中；SQLite investment_notes 只含 id、portfolio_id、instrument_id、path、deleted。客户端不能指定路径，传入额外字段会被拒绝。path 是数据目录下的相对路径，仅用于核验；正文通过作用域 GET 接口读取，不通过静态文件地址读取。写入新文件版本后再原子提交索引和修订号，旧文件不覆盖。修改的审计记录也不复制原文。

例：先 GET `N` 得到 revision=42，核对准确内容后提交：

```json
{"title":"本次观察","body":"## 主要判断\n\n保留 **需要验证的条件**。\n\n1. 核对最新披露。","expected_revision":42}
```

可用 `scripts/ai_client.py write POST /api/portfolios/P/instruments/I/notes ../local/work/note.json --confirm-write`，编辑用PUT，删除用DELETE。之后按返回id回读核对。409时保留草稿，重新读取和比较内容；不可自动提高revision后覆盖。写入超时先回读列表核实，不盲目重试；已成功的首次写入会使旧revision失效。

完整ZIP格式2的 manifest.note_files 是相对路径→SHA256映射；包含当前、已删除和历史文件版本。恢复校验所有文件名、内容范围与哈希，缺文件、重复项、路径穿越、符号链接或错配作用域都拒绝。支持恢复旧格式1的无笔记备份；恢复前保护当前数据并提高revision。自动备份的SQLite与同名.notes.zip是配对文件。

退役接口：`GET /api/portfolios/{pid}/reviews`、`PUT /api/reviews/{event_id}` 已移除；流水 `scope` 参数不再接受并返回422，避免旧客户端误以为筛选仍生效。改用交易详情获取真实收益和修订记录，用上述组合+标的笔记接口写研究记录。旧复盘数据仅在数据库/备份保留兼容，不作为新笔记返回。

## 计划资金校验与交易后行情（0.6.8）

`GET /api/portfolios/{pid}/instruments/{iid}/plan-check?target_pct=40` 是只读试算，不写账本、不预留现金。返回 `revision`、`target_pct`、`target_amount`、`plan_funding`。替换该标的拟设比例后，按当前组合所有计划计算；标的必须属于当前组合。客户端可将该 revision 用于后续 PUT，过期返回409。

PUT计划响应和 dashboard.plan_funding 提供相同结构：

- `required_amount`：各计划标的 max(总预算×计划比例−净投入,0) 之和。已超投不抵减其他标的需求；无计划不计需求。
- `available_amount`：当前可投金额，CNY；未到账回款不算可投。
- `shortfall` / `surplus`：需求超过余额的差额 / 余额超过需求的余量，均非负。
- `status`：shortfall / balanced / surplus / unknown。缺净投入或余额时，required_amount、shortfall、surplus 为null，known_required_amount仅为已知部分，不能替代完整合计。
- `total_target_pct` 为组合比例合计（百分数）；`unknown_instrument_ids` 明确缺失净投入的标的。资金缺口只警示，不拒绝合法计划保存；不会改预算、交易或现金。

交易详情新增 `forward_performance`（非买卖/再投或已取消时null）：包含 `basis`（raw / adjusted / total_return）、交易日起点的价格、日期、来源和1/3个自然月的 `windows`。优先使用交易日有精确记录的全收益、复权、未复权序列；观察端使用同一序列，不混合端点。

每个窗口包含 target_date、实际行情 date、price、source、return（比例字符串）、relative_trade_return（调整至实际成交价的行情参考）。到期行情取目标日起7日内且不晚于观察截止日的首条；无可比数据保留null。状态为 available / pending / missing_base / missing_quote / corporate_action，配有 status_label。该字段不进入 actual 收益和XIRR，也不恢复交易复盘写入功能。

## 环境和汇率缓存（0.7.0）

`GET /api/health` 增加 `environment`（dev/production/test）、`schema`。`GET /api/bootstrap` 同时返回 `environment_label`。会话 Cookie 名称由环境与数据目录身份共同决定；通过 `GET /api/capabilities` 的 authentication.cookie 获取名称，客户端应使用 Cookie jar，不能硬编码旧的 portfolio_session。

health 的 `read_only=true` 表示发布只读验收阶段：所有非 GET/HEAD/OPTIONS 的 `/api/` 请求返回503，行情、通知等后台任务暂停。此时不能提交记账或同步，也不能换地址绕过；等部署验证完成后再读health。首次安装不生成demo记录，空账本的启动／定时同步不自动下载行情；安装、升级与数据保护约定见 [安装指南](install.md)。

开发源码内 AI 客户端默认dev／8877；已安装发布包默认production／8765，按脚本位置判断，不依赖cwd。测试显式 `--environment test` 使用8876。`target` 离线显示目标；`--base-url` 可显式选择回环地址，标准端口与环境冲突则拒绝，自定义端口必须指定环境。每次请求前校验health中的application与environment；可用 `--expected-data-dir PATH` 锁定账本目录。服务不符或离线不自动切换目标。Host/Origin/CSRF规则不变，环境不同也不能转用token。自动汇率同步按 currency + date 去重，更新记录的汇率子项增加 inserted/updated/unchanged 计数。手工日频及CSV覆盖不会被后续自动同步改掉；历史交易的锁定率保持原值。

## 统一期间统计与提醒中心

完整约定见 [设计规范](design.md)。`GET /api/reminders` 是前端提醒唯一聚合接口；`GET /api/family/reminders` 仅作旧客户端资产子集兼容。新来源通过 provider 注册复用。响应 items 包含 id/source/kind/title/detail/date/days/amount/currency/severity/action，金额为 Decimal 字符串，未知为 null；action 是业务导航描述，不执行写入。全局读取包含所有未归档组合。

`GET /api/family/summary?grain=year&period=2026`、`?grain=month&period=2026-09` 或 `?grain=custom&start=2026-01-01&end=2026-09-30`：

- 默认 grain=month；period：grain/start/end/opening_date/comparison_start/comparison_end/comparison_label/partial。总览月度余额比较上月末，年度比较上年末，自定义比较起始日前一天；与 history 的 comparison=previous 余额基线一致，显式 yoy 仍使用去年同期。
- snapshot：结束日资产投影，与桑基图共用。
- metrics：total_assets/net_assets/total_debt/debt_ratio/liquid_cash/fixed/investment/receivable/income；各含 value/previous/amount/rate/reason，余额另有 period_change。rate 为小数比例；debt_ratio.amount 为比例之差，界面乘100显示百分点。
- income/previous_income：已录入合计 value、记录数 count、months、missing_fx。无记录合计为 null。收入基线为上一完整自然月/自然年，或自定义之前等长区间。
- cash_cover：month、monthly_repayment（该月计划还款总额）、months（可用现金÷monthly_repayment）、unconfigured、missing_fx、basis。按结束月份的还款额折算月数，不包含生活开支与保费，也不承诺未来月月等额。计划/汇率/可用现金缺失时结果为null；当月应还为0时months为null。读取不扣减余额。
- revision：此次读取的账本版本。日期/范围错误返回422；只读请求不写快照或修订。所有数据走相同本机会话、Host 与 Origin 校验。

现金、固定资产、理财与应收款表单传入 effective_on（金额日期）；更正可补录历史，不能晚于今天。旧版本记录继续保留，当前余额仍取最新有效日期。

0.8.1 完整备份下载在本地保留经校验的压缩 ZIP，含 SQLite 和全部引用笔记历史；落盘成功后才删除该次导出的未压缩中间副本。接口响应格式不变。

### 收入年度累计与组成

`GET /api/family/income?year=YYYY` 新增只读 `summary`，原有 `rows`、`records`、`revision` 保持兼容：

- `total`：该年已记录收入合计，Decimal 字符串；无记录或任意记录缺汇率时为 null。
- `known_total`：已知金额小计；`missing_count` 为缺少换算金额的记录数；`recorded_months` 为有记录的月份数，不代表其他月份收入为零。
- `groups.salary/rent/fund/custom`：各含 `amount`、`share`。`share` 是 0–1 比例，分母为 `total`；总额未知或不大于零时为 null。组内缺汇率时 amount 也为 null。
- 按记录日期历史汇率折合 CNY，同一记录更正只取最新版本，不改变现金余额。年份非法返回 422；只读查询不增加 revision。

台账分类汇总直接使用 overview.categories 的 value/known_value/weight，关联组合详情使用 items[].value（CNY），不读取手工记录专用 household_amount。预算型闲置预算排除，实盘现金分列。

## 送转股与复权行情

A股行情同步单独读取已实施送股/转增公告，`jobs.items[].source_kind=corporate_actions` 以“企业行动数据”显示结果；同步失败不代表存在送转计划。源中的纯现金分红可能将送转比例置空：仅在全部送转字段缺失、基础现金金额为正且完整实施方案明确为纯派现时，按无送转处理；支持明确列出中小股东和大股东的现金方案。未知方案、包含送转的附加文字或部分比例缺失仍报错，不将 null 普遍视为0，不从价格跳变猜测比例。港美股已有拆股公告沿用相同确认流程。

现金金额与方案文字不一致、差异化股东金额属于独立核对事项，保留在该子项 `notes` 和现金公告 `coverage_note`（候选 `note`）中，不阻断已明确的纯现金类型识别；基础金额可能为平均值，不代表特定股东应得金额，必须按实施公告和到账单核对，本次不自动更正已确认分红或交易。公告源失败保留旧缓存。对同一 instrument_id 的最新 corporate_actions 子项成功后，`GET /api/reminders` 自动移除旧同步失败提醒，不删除旧任务、不修改业务流水；旧任务“送转股”名称在提醒中心兼容显示为“企业行动数据”。

`dashboard.todos` 和 `GET /api/portfolios/P/instruments/I` 的 `todos` 提供 `confirm_split` / `verify_split`。核对登记日持仓、除权日与倍数后，使用普通交易预览和 `/api/events` 提交 `kind=SPLIT`、`factor`、`instrument_id`、`portfolio_id`、`date`、`expected_revision`；10送8转12的倍数为3。顶部系统提醒统一汇总待核对项，通过“查看提醒”进入中心处理；组合页面不重复展示独立通知区。确认后新增独立送转流水，保留原买入数量和总成本，除权当日先调整隔夜持仓，再处理当日买卖；现金分红及实际扣税单独核对。已知未确认或比例不一致时，相关估值/收益留空提示核对，不以错误份额展示暴跌。

标的详情新增 `price_basis`（`raw` / `adjusted`），有复权数据的股票默认 adjusted。A股标注前复权参考，港美股按数据源标注复权参考；基金单位净值不强行复权。`curves` 中仍保留原有两种序列。同步时复权参考刷新完整记账区间，避免除权前后的缓存调整基准混用；未复权行情继续增量同步。复权仅用于价格走势及后续行情参考，不替换真实成交价、估值现价、收益、现金红利或 XIRR。

送转参与批次由当前 revision 的历史流水按日期重放：先扣除除权日前的卖出，再对剩余份额应用倍数；除权日新买入不参与，当日卖出使用调整后的份额。补录、更正、取消交易都会重新计算，不把确认时的批次或数量固化到数据库。确认后待办消失，独立 SPLIT 流水保留；补录后除权前已全部卖出时，该流水显示无持仓、数量变化为0。新增同组合、同标的、同日第二条 SPLIT 返回422（allow_duplicate也不能放行）；更正应 PUT 原ID，已有历史记录不自动删除或合并。

`todo.notice` 提供公告登记日 `record_date`、除权日 `date` 与倍数；新确认携带 `prefill.source_split_id`。更正日期后仍保留此公告关联；日期／比例不一致时返回 `verify_split` 和原事件，不生成第二条确认。旧记录没有关联ID时，若同标的同倍数记录误填在公告登记日，作为需核对候选返回原记录，禁止再新增该公告的送转；读取不改写或合并历史。UI明确区分登记日与除权日，可显式选用公告值、预览并更正原ID。不能仅因提醒仍存在再次送转，也不能自动忽略日期差异；若更正影响后续卖出合法性仍由完整账本校验阻止。

- 流水列表（含分页）、标的详情交易列表与单笔详情的 `facts.split` 返回该 SPLIT 的 `event_id/instrument_id/date/factor`、`quantity_before/quantity_after/quantity_change`、`cost_native/currency`、`status=applied|no_position`、`lots[]`；每批包含 `buy_event_id/date` 和上述三个数量字段。数值均为 Decimal 字符串，cost_native 为该次送转前后保持不变的持仓总成本。非送转或已取消流水为 null；这是当前历史重算结果，不是确认时的存档。
- `POST /api/events/preview` 的 `split_changes[]` 返回受本次记账／更正影响的送转记录，包含 `event_id`、`before`、`after`；before/after 使用同一 SplitEffect 结构，新记录的 before 为 null，更正移除送转效果时 after 为 null。补录买卖也可能改变后续送转，需核对批次而不仅是总量。预览不写入，保存仍用预览 revision；并发补录导致409时重新核对。

### 关联删除与曲线缺失说明

删除预览以原账本重放买入批次、实际卖出（含旧 FIFO）、登记日分红权益及送转参与份额，递归列出关联分红、再投、送转、卖出、费税和成对组合转移。只跟随依赖方向；其他买入批次、其他组合和公开公告缓存保留。共享分红/卖出/送转按整条记录删除，不拆分金额、不重写剩余记录；`items[].shared_with_other_lots` 与 `other_buy_event_ids` 明示影响。`items[].event` 为原流水，`dependency_ids` 是本清单内的直接依赖，`is_root` 标注所选记录。尚未关联到任何权益批次的旧分红不猜测归属。

先 GET 预览（无副作用），核对全部清单，再 DELETE 同一ID，例如 `{"reason":"重复录入，已核对关联流水","cascade":true,"event_ids":["BUY_ID","SALE_ID"],"expected_revision":42}`。服务端在同一事务重新推导并核对清单、revision 和剩余账务；清单不一致或账务失败返回422，过期revision返回409，均无写入。预览 `can_delete=false` 时返回具体 `validation_error`，不允许确认。返回兼容字段 `cancelled` 和完整 `deleted_event_ids`；每条记录保留审计，删除记录可按原ID从交易详情读回 `cancelled=true`。省略cascade保留旧单条/转移对删除语义，不自动扩大旧客户端删除范围。

Dashboard full 视图包含 `curve_quality`：`missing_days`、`from/to` 和 `reasons[{message,from,to}]`。仅统计已投入或估值不完整时无法展示的组合曲线点；未投资的空白不当作故障。原因取自对应历史日期（如送转待核对、缺价格/汇率），不借用今天的状态解释历史；缺口后的累计曲线不补零、不跨缺口连接。图旁红色说明有待办时提供提醒中心入口，否则进入全局设置补齐历史数据。持仓分页为完整 dashboard 持仓上的本地分页，默认20，可选10/20/50；汇总和排行榜使用全量数据。排行榜50只以内为单列并随页面向下滚动；桌面超过50只按每列50只顺序拆列，手机保持单列，不截断排名。

## 当前持仓标签与分布

作者：Leo · 2026.10.31

`PUT /api/portfolios/{pid}/instruments/{iid}/labels` 要求 `expected_revision` 为读取结果中的非负整数。完整替换三个可选字段：

```json
{"scope":"海外债券","direction":"国债","detail":"美元国债","expected_revision":12}
```

`scope` 留空恢复自动；仅股票按市场识别，基金不按交易地或币种推断，无法确认时显示待确认。`direction` 取GET返回的该范围选项，`detail` 为最多40字的单行标签，须先选方向。同一路径下同名标签自然聚合，无需维护分类树。清空三个字段不修改历史业务记录。

返回 `labels` 含原始scope/direction/detail、resolved_scope、automatic_scope、scope_source与path；`options` 含scopes、directions和仅当前组合的suggestions。422表示作用域/枚举/长度或依赖无效；409表示版本冲突且未写入；保留草稿，重新读取并核对差异后由用户显式保存。网络超时先回读，不能盲目重试。

首次BUY可在交易请求附带 `holding_labels`（scope/direction/detail），预览返回拟保存路径且无副作用；原revision提交与流水同一事务保存到独立标签表，该字段不进入历史事件、指纹或交易CSV。已有标签通过独立接口修改，普通计划保存不改标签。

持仓分布只分析当前持仓，所有金额为CNY的Decimal字符串，占比为0–1比例，未知为null。`basis=holdings` 分母为全部当前证券市值；`assets` 纳入剩余可投金额和应收及在途系统节点。每个node含id、path、value、weight、holding_ids和children。父节点直接归属且同时有下级时补“本级持仓”，保证下级金额可核对。缺估值/汇率或存在负值、非正总额时eligible=false，保留明细并说明reason。标签未知仍计入分母。

旧 `GET /api/tags` 和旧多选标签统计已移除，返回404且不在OpenAPI中；asset_class/region和对应流水筛选保留；旧tags由schema9清理。

资产总览 `metrics.total_assets` 另返回 known_value、missing_count、missing_categories、complete。完整金额缺失时value/rate/amount仍为null，UI只显示明确标注的已知资产合计，不将缺失金额补零。

### 分类与通知变更（2026.10.31）

资产类别：权益、债券、大宗商品、其他、未分类；地区：A股、美股、港股、海外其他、未分类。AllocationRequest不再接受tags，流水tag筛选返回422；事件写入拒绝tags。旧CSV的tags列兼容忽略且不入库，导出不含tags列。schema9清除业务行根级旧tags，将混合地区转未分类；独立holding_labels保持不变。

GET /api/notifications返回missing_requests：每项含id、schedule_id、item_id、portfolio_id、name、kind、due_date、at、reason及可选error_domain/error_code；未知系统原因明确标注。POST action支持refresh（重新排程并验证系统队列）、open_settings（打开本机macOS通知设置）、enable/disable/test。原生弹出通知继续隐藏名称与金额；详情只在本机设置页面展示。排程成功不等于用户看见或业务已处理。

## 指数研究与全局数据更新（schema10）

指数不绑定组合。会话、Origin、CSRF、停写保护与现有接口相同；GET不发起采集。细节、来源频率与限制见[研究合同](research.md)。

| 接口 | 合同 |
|---|---|
| POST `/api/research/indices/resolve` | `{code}`；返回 profile、resolve_token、research_revision、expires_at。只读身份预览。 |
| POST `/api/research/indices` | `{resolve_token, expected_research_revision, request_id}`；短期签名绑定精确配对，CAS建档及首次补齐意图同事务持久化；返回 id、basis、job_id、research_revision、existing。原 request_id 重放返回原结果，变更负载409。 |
| GET `/api/research/indices?kind=宽基&page=1` | 固定每页20；价格摘要、PE/PB 3/5/10年分位和独立高点日期。无搜索、关注及组合关联。 |
| GET `/api/research/indices/{id}?basis=price&method=overall&window=3` | basis为price/total_return；method为overall/median/arithmetic；窗口3/5/10。返回analysis_id、独立research_revision及四页签投影。当前可得版本；历史asof参数明确拒绝，避免冒充PIT。 |
| GET `.../{id}/series` | basis、window、可选analysis_id；返回抽样曲线与完整日线计算的高点/最大回撤。 |
| GET `.../{id}/constituents`、`financials` | page≥1、page_size默认20上限100、analysis_id；分页前先核对输入集合。financials返回当前篮子摘要、历史断点及原始财报字段分页。 |
| GET `.../{id}/sources` | 各模块snapshot_id、源日期、抓取时间、字段及SHA-256；与指定analysis_id不一致时409。 |
| POST `.../{id}/scenarios` | `{analysis_id,basis,metric,target_multiple,change_ratio,accept_fixed_basket_assumption:true}`；只读Decimal试算，metric为pe/pb，ratio为比例。仅同日价格序列、整体法、正估值可用；无未来分红假设不开放全收益。 |
| GET/PUT `/api/settings/data-updates` | GET返回policies及settings_revision；PUT完整六项policies及expected_settings_revision，冲突409。scope、enabled、cadence（daily/trade_day/weekly/manual）、time、timezone=Asia/Shanghai。 |
| POST `/api/data-updates/run` | `{scopes,request_id,targets?}` 或加精确 `resources`；202仅表示受理。scopes为quotes/fx/index_prices/memberships/valuations/financials。重复request_id不重复执行；不同负载409。 |
| GET `/api/data-updates/jobs/{job_id}` | 按已受理ID读取持久任务与逐项结果，不依赖最近20条展示列表。 |
| GET `/api/data-updates/jobs` | 最近20条任务、resources、逐项实际数据日、来源版本、错误及状态。失败项resource可原样重试。 |

精确资源示例：`{"scope":"fx","currency":"HKD"}`、`{"scope":"quotes","instrument_id":"synthetic-id"}`、`{"scope":"index_prices","target":"cni:399006","module":"total_return"}`。memberships包含memberships/historical_memberships，valuations包含valuations/stock_valuations，financials包含financials/historical_financials；重试单模块不扩大为整个scope。旧 `/api/sync` 和 `/api/jobs` 继续兼容。

缺值为null，状态说明历史不足、覆盖不足、非正基数等原因；百分位/回撤均为比例字符串。高点日期按对应完整日历窗口、指定序列的最高收盘**最近一次**出现日计算。

计划PUT允许可选 `holding_labels:{scope,direction,detail}`，与计划同事务、原expected_revision保存。GET labels同时返回当前allocation。省略region/holding_labels分别保留原地区/标签，不把默认值写回；独立labels PUT语义保持不变。

公共更新的行情/汇率资源支持可选布尔`full`（默认false），用于历史补齐；精确重试须保留它。记账、基准与导入后的自动补齐进入同一队列，遵守全局scope开关及仅手动设置。

指数增强（method_version `index-library-2`）：detail新增`changes.daily/weekly/ytd`（ratio/from_date/to_date或null）、`top10_weight`（百分数）、`daily_series`（完整显示日线及ma51/ma120/ma250/ma850，缺期null）、按权重降序的`companies[].weight_rank/financials[报告期]`与`financials.current_years`。`valuation.*.windows.*.quantiles`为有效同源历史样本的p10/p25/p50/p75/p90。list亦提供changes。旧字段保持兼容。

`POST .../scenarios`可选`reference_window:3|5|10`（默认5），返回`curve`（change_pct、point）、`reference_points`（分位→multiple/point）和reference_window。曲线与所选结果均由后端Decimal计算；参考点固定分母不变，窗口不足则空对象。只接受同日整体法价格序列与明确固定篮子假设；所有结果仍绑定analysis_id。
