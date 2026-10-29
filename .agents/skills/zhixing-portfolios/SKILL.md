---
name: zhixing-portfolios
description: 查询或维护知行财富的投资组合、买卖分红流水和投入计划。用于本机账本的记账与核对，不连接券商下单，不代替投资决策。
---

# 投资组合记账

阅读 [AI 接入约定](../../../docs/ai.md)。开发源码内`scripts/ai_client.py`默认开发账本，发布包默认正式账本；独立日常工作区使用其`./zhixing`入口替换客户端前缀，不切到开发目录。操作正式账本须有对应用户授权，不启动部署或恢复。

- 读取health、capabilities、bootstrap，使用真实portfolio_id和instrument_id；同一证券在不同组合独立。命中多个组合时先澄清目标，不以页面上次选择代替记账授权。
- 查询dashboard读取持仓、投入和收益；流水显式传`page=1&page_size=20`及筛选。翻页保持相同条件并检查revision；变化时重新读取，不混合不同版本。
- 日常录入使用BUY、SELL、DIVIDEND、REINVEST；送转/拆股专项待办使用SPLIT。买卖金额、费用、日期及锁定汇率按 [接口文档](../../../docs/api.md) 核对；不同分红日期分别读取reference-rate。不得拿当前汇率覆盖历史锁定值。
- 红利再投可读 `GET /api/portfolios/P/instruments/I/reinvestment-estimate?date=YYYY-MM-DD&gross_amount=...`：同日场外基金单位净值估算，按两位小数四舍五入。只读、不自动入账；null不补0，缺当日净值或其他市场按实际确认单填。核对后显式传reinvest_quantity，再走交易预览与提交；金额为不含手续费的再投总价，不重复扣税费。已有确认份额优先，不用估算覆盖。
- 用`preview REQUEST.json`预览交易；编辑请求包含原事件id，提交PUT时路径使用同一id。保存前核对份额、费用、现金与收益影响，以及用户授权。
- 保存使用`write POST /api/events REQUEST.json --confirm-write`，更正用PUT，取消用DELETE并提供原因；所有请求带预览/读取的expected_revision。取消影响关联分红与后续交易，范围不明时先确认。
- 新建组合用`write POST /api/portfolios`，编辑用`PUT /api/portfolios/ID`，先读取并核对拟变字段和revision。组合/笔记没有独立服务端预览接口时，展示读取值与拟保存差异，不声称已完成服务端预览。
- 投入计划先调用`GET /api/portfolios/P/instruments/I/plan-check?target_pct=...`，再按返回revision提交PUT；缺口仅提醒，不自动加预算或卖出。
- 提交后按正式ID回读流水/持仓/计划并对照预览。409不自动覆盖，超时先回读；同步请求受理不代表行情更新成功，应看jobs逐项状态。
- jobs只返回最近20条，后台默认最多保存20条（含运行中），不清理运行中任务，不当作完整业务审计。价格子项saved/unchanged表示新增变化版本/跳过重复；交易锁定汇率不随同步改变。部署和文件清理须另有授权。
- 待核对分红和其他事项统一从`/api/reminders`进入，再到对应组合查看原事实；不能将通知已读等同于分红已记账。未完成复核不自动确认、忽略或更正。

报告明确净投入、累计买入成本、持仓市值、净分红与收益的区别；资产增减不等于投资收益。精确Decimal值来自API，万/亿仅作展示。投资笔记任务使用项目的`zhixing-notes`技能。

送转/拆股专项：从 dashboard 或标的详情 todos 读取 confirm_split/verify_split；按公告和实际持仓核对除权日期与新旧份额倍数，走同一 preview → 原 revision 提交 → 回读。10送8转12填 factor=3；总成本不变，现金红利和扣税另行记录。公告同步不授权自动记账，已确认事件不重复添加；复权价格只作走势参考，不替换账本原成交价。
历史补录／更正还须核对 preview.split_changes 的前后份额及参与批次；按原 SPLIT ID 回读 facts.split。仅除权日前剩余份额参与，已售份额与当日新买入排除；批次随历史重算，不固定在首次确认时。全部售完为 no_position，已确认流水保留但不调整份额；不能把流水保留当作待办未完成。同日重复送转返回422，需更正原记录而非 allow_duplicate。
确认携带 source_split_id；遇 verify_split，核对 todo.notice 登记日与除权日及倍数，更正 todo.event.id。旧记录误记在登记日会提示核对原记录，不代表允许再次送转；未经用户核对不自动改日期或删除历史。UI与AI都用预览 revision 提交，409重新核对。
