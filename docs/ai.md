# AI 接入与项目技能

界面和 Agent 访问同一后端服务。支持直接 HTTP、OpenAPI 和标准库客户端，不要求额外 MCP 服务或插件。部署、恢复、数据搬迁不是记账技能的隐含权限。

首次接手安装或每次升级先读 [安装与升级指南](install.md)。安装后默认进入生成的独立日常工作区，不使用开发账本。首次安装为空，不创建演示记录；查询、能力发现和读取文档不构成记账授权。发布保全与账务更正分开处理，不能用发布脚本更改正式业务数据。

## 发现技能与能力

项目自带三个技能：[资产管理](../.agents/skills/zhixing-assets/SKILL.md)、[组合记账](../.agents/skills/zhixing-portfolios/SKILL.md)、[投资笔记](../.agents/skills/zhixing-notes/SKILL.md)。在 Codex 中打开本项目目录，或以本项目为工作目录运行，即可发现 `.agents/skills`，无需全局安装；也可用 `$zhixing-assets` 等名称显式调用。

Codex 查找当前目录和向上的技能目录，不递归搜索任意子项目。若从父工作区启动，请先将本项目设为工作目录；未出现时重启会话。其他 Agent 是否自动发现取决于其技能规范，不能保证都兼容。见 [Codex 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。技能文件校验通过与当前会话已加载技能是不同事实。

同步与存储：capabilities.storage 提供保留策略。jobs仅返回最近20条，默认最多保存20条（含运行中），不清理运行中任务，不作为完整审计；相同行情不重复写入，修正版本保留，交易锁定汇率不变。AI与界面使用同一同步入口，不直接写库或执行文件清理。

先 `GET /api/health` 确认 environment 和目标账本，再读 `/api/capabilities`、`/api/openapi.json`、`/api/guide`。资产读 `/api/family/catalog` 获取真实 ID、revision、日期范围，再读 `/api/family/command-schema` 获取推荐命令 JSON Schema 与语义规则。schema 描述当前推荐入口；条件约束由服务端执行，旧兼容字段仍见 [API 文档](api.md)。

## 连接与授权

客户端按脚本所在位置选择默认环境，与当前工作目录无关：开发源码默认 `dev`／8877；`runtime/releases/` 内的发布包（包括经 current 调用）及带 `release.json` 的发布包默认 `production`／8765。合成测试显式传 `--environment test`，默认8876。可用 `target` 离线查看选择结果；这不代表已连接服务。`--base-url` 兼容标准端口并推断对应环境；自定义端口必须同时指定环境，标准端口与显式环境不一致则拒绝。

正式日常工作区使用其 `./zhixing` 入口，固定正式地址与数据目录，并在每次调用时使用 current 指向的安装版本。这里的示例命令前缀 `python3 scripts/ai_client.py` 应替换为 `./zhixing`，不用切回源码目录。日常工作区明确指定正式账本，但每次业务写入仍须符合用户授权。不可把开发修改回灌正式账本，服务不在线也不自动安装或切换版本。

`scripts/ai_client.py` 只访问回环地址，拒绝重定向；每次 API 请求前通过 health 校验应用和环境，传 `--expected-data-dir PATH` 时还校验精确账本目录。校验失败或离线就停止，不回退到其他服务。bootstrap 建立 Cookie 会话，CSRF 仅保存在进程内。无需从浏览器复制凭据，不能关闭 Host/Origin/CSRF 校验。`read /api/bootstrap` 自动隐藏令牌。

1. 读取原记录、作用域及 revision；同名结果不猜ID。
2. 资产和交易使用服务端 preview。组合资料、笔记没有独立 preview 时展示原值和拟保存差异，不能声称服务端已预览。
3. 核对用户对目标、金额、日期和操作的授权。已有明确授权无需重复确认；新范围或覆盖需要核对。
4. 使用预览/读取的原 expected_revision 提交；不能以重新连接时的新 revision 替换。`--confirm-write` 只是调用者声明授权，不能替代人的授权。
5. 按保存响应的正式 ID 回读并核对。新记录预览 ID 是暂定值，不能直接拿来查提交结果。

409表示未写入，应重新读取、比较变化并重新核对；不自动换版本覆盖。网络超时结果未知，先回读核实，禁止盲目重试。接口没有通用幂等键。不自动设置 allow_duplicate。

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

## 验证与维护

红利再投可用 `Client.reinvestment_estimate(portfolio_id, instrument_id, date, gross_amount)`，或 `read /api/portfolios/P/instruments/I/reinvestment-estimate?date=2024-06-03&gross_amount=10` 获取只读估算。UI与AI共用该计算：仅场外基金同日自动单位净值，份额按总价÷净值四舍五入至两位。返回null时不得填0，不用股票收盘价、相邻日或累计净值代替。实际确认单优先；核对后显式填 reinvest_quantity，再预览、按预览revision提交并回读。详见 [接口文档](api.md)。

`python scripts/verify.py --browser` 包含标准库客户端真实 HTTP 回归：能力发现、预览不写入、快照回读、旧 revision 拒绝、收入更正、组合关联及笔记作用域。只能使用临时合成 test 账本。`source_check.py` 只收集白名单技能文件，私人请求、笔记及账本不得进入技能包。

后续迭代须按 [AGENTS.md](../AGENTS.md) 同步维护能力契约、客户端、文档、技能和验证。

收入累计和占比读取 `/api/family/income?year=YYYY` 的 `summary`（字段见 docs/api.md），不在客户端把缺失金额补零或重复汇总更正版本。关联台账金额用 `items[].value`，分类金额用 `categories[].value`；预算型未投入预算不计入资产。

送转股/拆股：读取 capabilities.corporate_actions 与 dashboard/instrument_detail 的 todos。先核对公开公告和实际持仓，使用 `SPLIT` 专项预览，再按原 revision 提交并回读份额、成本和收益。10送8转12填 factor=3，总成本保持不变；现金分红、扣税另行核对。同步公告不等于确认记账，不得因价格骤降自动造流水或用前复权价格改写历史买入。资产相邻期变化包含新增/移除/份额调整，原样报告 scope_changed/reason，不能称为投资收益率。
