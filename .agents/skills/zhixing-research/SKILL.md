---
name: zhixing-research
description: 在知行财富按主估值指标筛选指数并排序，读取指数观察台、市场水位和市场活动，或在授权后建档和更新研究数据。
---

# 指数观察台

先读 [AI 接入约定](../../../docs/ai.md)。正式日常工作区使用 `./zhixing`，开发源码客户端默认连接开发实例。用 health 校验目标，再读 capabilities 和研究目录。

1. 用 `research_indices(page=1, method='mcw', valuation_group='all')` 查看首页对应的分组。CLI 可用 `./zhixing read '/api/research/indices?method=mcw&valuation_group=dividend_yield&sort_by=ytd&sort_direction=desc'`。分组由 `profile.primary_valuation` 决定。各指标当前值、日期及原始分位取 `valuation.<metric>`，主指标的 5 年 / 10 年位置和分级取 `valuation_position.windows`。股息率的位置等于 1 减原始分位。按 `capabilities.research.index_list` 选择排序字段，筛选和排序先于分页，空值置后。报告涨跌幅时附实际取价日期。
2. 用 `collection_preview(code, modules, history_start, primary_valuation=...)` 或 CLI 的 `preview REQUEST.json --path /api/research/collection/preview`，展示拟用分类、核验日线来源和日期、范围、预计调用量、可采集和排除模块及原 `research_revision`。H11136/H01136 指向同一中证海外中国互联网指数，价格、全收益和当前成分走官方数据，无需理杏仁 Token。理杏仁日频 PE/PB 可独立订阅，跨市场财务按预览排除。其他新指数按 capabilities 核对验证来源。
3. 获授权后 `PUT /api/research/collection`，带预览返回的 `modules`、`history_start`、`primary_valuation`、经核对的 `enabled` 和原 `expected_research_revision`，按正式 ID 回读。新增必须启用，保存后按返回的 `job_id` 查首次全量任务。只改分类时沿用原订阅模块、起始日及启用状态，先核对预览的研究版本与所读原订阅一致。分类编辑在本机完成。409 或超时先回读核实。
4. Token由用户在本机「研究工具 → 数据管理」输入并保存本地文件。禁止读取Token文件或令牌内容，禁止发到聊天、请求文件、日志、云端或仓库。仅可读取配置状态和非敏感计数。
5. `research_index`/`research_studio`/`research_part`只读本地；日期和分析ID一致，跨版本分页重读。mcw/ew/ewpvo/median不混；旧overall/arithmetic保留旧来源。全历史标实际覆盖，当前篮子回溯不等于历史当期篮子。指数财报不能填逐只公司表。
6. 收益回测和指数重叠已移除；存量变化不称净流入。条件试算读取scenario_metrics与scenario_assumption，使用同日正数整体法/mcw；须接受固定成分、股数、权重、除数及全部成分价格、盈利/净资产同比例变化的假设。等权、中位数、全收益禁用。
7. 完整契约复用 [研究说明](../../../docs/research.md)、[API](../../../docs/api.md) 和 [AI指南](../../../docs/ai.md)。旧后缀验证/建档写接口已退役，历史来源数据不删除。

## 研究数据更新

- 先读 `GET /api/data-updates/history?area=research` 和现有订阅。单指数增量用 `refresh_index(index_id, request_id)`，或按 `capabilities.data_updates.current_index` 提交精确的 `targets` 与 `scopes`。按来源更新使用详情的 `collection.resources`。H11136 可单独提交 `valuations` 资源更新 PE/PB。全量校准须在授权范围内按 API 的 full 约定提交。暂停保留历史。
- 使用稳定的 `request_id` 提交，按 `GET /api/data-updates/jobs/JOB_ID` 核对终态、各模块观测日和失败原因。结果未知先查任务并保留原 ID 和负载。确认失败后获授权精确重试时使用新 ID 和原失败项 `resource`。本机只统计调用尝试，供应商权限或额度阻断需单独核对。

历史现金流按对应年末历史估值记录行业区分金融公司；`excluded_unknown_industry` 非零时不可宣称完整覆盖。历史行业与财报均为当前可得版本，不属于严格PIT。

财务优先用理杏仁历史各期成分直接汇总，按年度/一季度/半年/三季度分别读取；不要用今天的成分回填历史。t_r是回溯值，t_y2y才是同比。涨跌 changes.monthly 为月初前最近有效收盘至最新值的变化。

## 市场水位

1. 通过`market_waterlevel(method, asof, facts=True)`读取事实包，核对分析ID、方法版本、观测日期、来源和有效覆盖。mcw/ew/ewpvo/median适用于指数和理杏仁A股全指，comparisons提供所选口径PE/PB与两综指的月度对照，PE附国债收益率倒数。历史日期截取当前修订版。GET读取本地快照。
2. 股票评分固定指数mcw与全市场中位数，方法视角不重复计分；负PE/PB、短历史、陈旧或来源失败保持原状态。均值为60/120个完整月末值，缺月不补；分位严格小于当前值，不能复用指数观察台的日频均值/分位解释。
3. 用 `market_data_sources()` 读取具体指标、最新观测日期和 `schedule`。应用开启自动更新时每周五北京时间 18:00 后增量更新。获授权手动更新时，将返回的精确 resource 与稳定 request_id 提交到统一队列。CPI 只提交 `prices/cpi_yoy`。沪深两融余额和 A股全指样本成交额分属 `margin_balances` 与 `market_turnover`，金额用 CNY 元，两者均不计分。full_market 与 market_turnover 计入理杏仁基本面端点累计尝试次数。
4. 冻结提交原analysis_id、method、asof与expected_research_revision，回读snapshot_id后下载离线报告。409不强行覆盖或自动重试。规则摘要与模型解释分开，未接入的转债、市值/GDP以及具体来源缺口不能替代或虚构。
5. `market_activity(asof=None)` 从本地快照逐交易日读取两融余额与成交额，核对 asof、research_revision 和实际观测日。详细来源、评分及迁移边界见[市场水位说明](../../../docs/waterlevel.md)。
