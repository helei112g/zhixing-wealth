---
name: zhixing-research
description: 在知行财富本机系统读取指数资料和市场水位事实包、核对代码配对、估值分位和高点日期，并在明确授权后建档或管理公共数据更新。
---

# 指数观察台

1. 读取 `/api/capabilities` 和 `/api/research/catalog`，按本地已核验家族识别；未添加不采集。
2. 用 `collection_preview(code, modules, history_start)` 展示范围、逐端点预算和原研究revision。依据用户授权提交 `PUT /api/research/collection`，保留expected_research_revision，按id回读；409不自动重试。保存不采集。
3. 明确更新时提交原有公共数据任务，稳定request_id，结果未知先查job_id。精确重试只提交失败项原resource；暂停保留历史。试用默认每端点20次本机预算，不自动扩大或解除权限阻断。
4. Token由用户在本机页面输入并保存本地文件。禁止读取Token文件或令牌内容，禁止发到聊天、请求文件、日志、云端或仓库。仅可读取配置状态和非敏感计数。
5. `research_index`/`research_studio`/`research_part`只读本地；日期和分析ID一致，跨版本分页重读。mcw/ew/median不混；旧overall/arithmetic保留旧来源。全历史标实际覆盖，当前篮子回溯不等于历史当期篮子。指数财报不能填逐只公司表。
6. 收益回测和指数重叠已移除；存量变化不称净流入。条件试算读取scenario_metrics与scenario_assumption，使用同日正数整体法/mcw；须接受固定成分、股数、权重、除数及全部成分价格、盈利/净资产同比例变化的假设。等权、中位数、全收益禁用。
7. 完整契约复用 [研究说明](../../../docs/research.md)、[API](../../../docs/api.md) 和 [AI指南](../../../docs/ai.md)。旧后缀验证/建档写接口已退役，历史来源数据不删除。

历史现金流按对应年末历史估值记录行业区分金融公司；`excluded_unknown_industry` 非零时不可宣称完整覆盖。历史行业与财报均为当前可得版本，不属于严格PIT。

财务优先用理杏仁历史各期成分直接汇总，按年度/一季度/半年/三季度分别读取；不要用今天的成分回填历史。t_r是回溯值，t_y2y才是同比。涨跌 changes.monthly 为月初前最近有效收盘至最新值的变化。

## 市场水位

1. 通过`market_waterlevel(method, asof, facts=True)`读取事实包，核对分析ID、方法版本、观测日期、来源和有效覆盖。mcw/ew/median适用于指数和理杏仁A股全指，comparisons提供所选口径PE/PB与两综指的月度对照，PE附国债收益率倒数。历史日期截取当前修订版。GET读取本地快照。
2. 股票评分固定指数mcw与全市场中位数，方法视角不重复计分；负PE/PB、短历史、陈旧或来源失败保持原状态。均值为60/120个完整月末值，缺月不补；分位严格小于当前值，不能复用指数观察台的日频均值/分位解释。
3. 明确更新时使用市场updates入口，或统一队列的 market 来源组与稳定request_id。市场水位仅支持手动更新。full_market 读取理杏仁 A股全指1000002，按本机理杏仁基本面额度计数。
4. 冻结提交原analysis_id、method、asof与expected_research_revision，回读snapshot_id后下载离线报告。409不强行覆盖或自动重试。规则摘要与模型解释分开，未接入的转债、市值/GDP以及具体来源缺口不能替代或虚构。
5. 详细来源、评分及迁移边界见[市场水位说明](../../../docs/waterlevel.md)。
