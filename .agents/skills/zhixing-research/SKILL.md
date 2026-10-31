---
name: zhixing-research
description: 在知行财富本机系统读取指数资料、核对代码配对、估值分位和高点日期，并在明确授权后建档或管理公共数据更新。
---

# 指数资料与数据更新

使用仓库的 `scripts/ai_client.py`，先核对 environment 和 data_directory；离线不切换环境。不直写 SQLite，不把组合或笔记内容发给数据供应商。

1. 读取 `/api/capabilities` 和 `/api/research/indices`。指数在独立研究工具中，不依赖组合。支持范围与缺口见 [研究合同](../../../docs/research.md) 和 [接口](../../../docs/api.md)。
2. 通过 `resolve_index(code)` 或 preview 的 `/api/research/indices/resolve` 识别。展示名称、机构、价格/全收益代码、输入口径和研究版本；未知代码不能猜前缀、改成同名指数。
3. 获得建档授权后，POST `/api/research/indices`，原样携带 resolve_token、expected_research_revision、稳定 request_id。建档和首次补齐意图原子保存。回读返回的正式 ID 与 job_id。受理不等于采集完成。
4. 409 重新读取并展示差异；未知结果先回读，保留原 request_id，不能换版本强制提交。重复添加只保留一个指数档案。
5. 估值、价格、成分和财报日期分别解释。3/5/10 年不足为 null，不能补零。高点日期是各自窗口内最高收盘最近出现日；不称为“本轮高点”。PE/PB不按价格与全收益点位比例缩放。
6. 分页/图表使用同一个 analysis_id；快照变化后重读，不拼接不同版本。财务当前篮子与历史有效名单分开；当前可得重述财报不冒充严格 PIT，缺年份与行业未知保留缺口。
7. 更新策略从 `/api/settings/data-updates` 读取，用原 expected_settings_revision PUT。展示六类策略的差异，单独核对更新授权；它不使用账本 revision。应用关闭时不承诺执行。
8. 明确授权更新后 POST `/api/data-updates/run`，携带 scopes、稳定 request_id；失败重试只提交失败项的原 resource，不扩大到其他币种或模块。轮询 jobs 核对实际日期和逐项状态；历史失败不等于当前仍失败。
9. 条件试算走服务端 scenarios，传当前 analysis_id、Decimal 字符串、明确固定篮子假设。全收益缺少未来分红假设时不可计算，不输出买入分数或投资结论。

不合并 PR、不部署、不恢复或清理数据，除非另外获得相应授权。

增强读取：使用changes的from_date/to_date解释日、周、年度涨跌幅；daily_series中的MA按完整交易日计算。成分财务按financials的报告期展示，current_years不冒充历史指数。条件关系图使用scenario返回的curve/reference_points，可选reference_window=3/5/10；历史P10–P90为固定分母参照，不称底部或概率。

客户端完整读取：research_index支持basis、window、method；research_part读取四种投影。跨页显式携带同一analysis_id；从capabilities发现主口径、方法、均线周期及情景参考窗口，不能只根据UI默认值推断。
