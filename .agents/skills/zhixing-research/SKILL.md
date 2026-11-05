---
name: zhixing-research
description: 在知行财富本机系统读取指数资料、核对代码配对、估值分位和高点日期，并在明确授权后建档或管理公共数据更新。
---

# 指数观察台

1. 读取 `/api/capabilities` 和 `/api/research/catalog`，按本地已核验家族识别；未添加不采集。
2. 用 `collection_preview(code, modules, history_start)` 展示范围、逐端点预算和原研究revision。依据用户授权提交 `PUT /api/research/collection`，保留expected_research_revision，按id回读；409不自动重试。保存不采集。
3. 明确更新时提交原有公共数据任务，稳定request_id，结果未知先查job_id。精确重试只提交失败项原resource；暂停保留历史。试用默认每端点20次本机预算，不自动扩大或解除权限阻断。
4. Token由用户在本机页面输入并保存本地文件。禁止读取Token文件或令牌内容，禁止发到聊天、请求文件、日志、云端或仓库。仅可读取配置状态和非敏感计数。
5. `research_index`/`research_studio`/`research_part`只读本地；日期和分析ID一致，跨版本分页重读。mcw/ew/median不混；旧overall/arithmetic保留旧来源。全历史标实际覆盖，当前篮子回溯不等于历史当期篮子。指数财报不能填逐只公司表。
6. 回测区分成熟、未到期、缺失及重叠样本；存量变化不称净流入。条件试算读取scenario_metrics与scenario_assumption，使用同日正数整体法/mcw；须接受固定成分、股数、权重、除数及全部成分价格、盈利/净资产同比例变化的假设。等权、中位数、全收益禁用。
7. 完整契约复用 [研究说明](../../../docs/research.md)、[API](../../../docs/api.md) 和 [AI指南](../../../docs/ai.md)。旧后缀验证/建档写接口已退役，历史来源数据不删除。
