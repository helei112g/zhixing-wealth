---
name: zhixing-research
description: 在知行财富本机系统读取指数资料、核对代码配对、估值分位和高点日期，并在明确授权后建档或管理公共数据更新。
---

# 指数观察台

1. 读取 `/api/capabilities` 和 `/api/research/catalog`，按本地已核验家族识别；未添加不采集。
2. 添加指数使用 `collection_preview(code)`，默认全历史与全部可用采集项。若有 existing_id 则读取已有档案。依据用户授权向 `PUT /api/research/collection` 提交 code 和原 expected_research_revision，再按 id 回读。管理采集需显式预览和提交 modules、history_start 与 enabled。409 后先回读。保存不触发采集。
3. 明确更新时提交原有公共数据任务，稳定request_id，结果未知先查job_id。精确重试只提交失败项原resource；暂停保留历史。试用默认每端点20次本机预算，不自动扩大或解除权限阻断。
4. Token由用户在本机页面输入并保存本地文件。禁止读取Token文件或令牌内容，禁止发到聊天、请求文件、日志、云端或仓库。仅可读取配置状态和非敏感计数。
5. `research_index`/`research_studio`/`research_part`只读本地；日期和分析ID一致，跨版本分页重读。mcw/ew/median不混；旧overall/arithmetic保留旧来源。全历史标实际覆盖，当前篮子回溯不等于历史当期篮子。指数财报不能填逐只公司表。
6. 收益回测和指数重叠已移除；存量变化不称净流入。条件试算读取scenario_metrics与scenario_assumption，使用同日正数整体法/mcw；须接受固定成分、股数、权重、除数及全部成分价格、盈利/净资产同比例变化的假设。等权、中位数、全收益禁用。
7. 完整契约复用 [研究说明](../../../docs/research.md)、[API](../../../docs/api.md) 和 [AI指南](../../../docs/ai.md)。旧后缀验证/建档写接口已退役，历史来源数据不删除。

历史现金流按对应年末历史估值记录行业区分金融公司；`excluded_unknown_industry` 非零时不可宣称完整覆盖。历史行业与财报均为当前可得版本，不属于严格PIT。

财务优先用理杏仁历史各期成分直接汇总，按年度/一季度/半年/三季度分别读取；不要用今天的成分回填历史。t_r是回溯值，t_y2y才是同比。涨跌 changes.monthly 为月初前最近有效收盘至最新值的变化。
