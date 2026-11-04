---
name: zhixing-research
description: 在知行财富本机系统读取指数资料、核对代码配对、估值分位和高点日期，并在明确授权后建档或管理公共数据更新。
---

# 指数资料与数据更新

阅读 [AI接入约定](../../../docs/ai.md)。开发使用仓库的 `scripts/ai_client.py`，已安装日常工作区使用 `./zhixing`，先核对 environment 和 data_directory；离线不切换环境。不直写 SQLite，不把组合或笔记内容发给数据供应商。

1. 读取 `/api/capabilities` 和 `/api/research/indices`。指数在独立研究工具中，不依赖组合。支持范围与缺口见 [研究合同](../../../docs/research.md) 和 [接口](../../../docs/api.md)。
2. 通过 `resolve_index(code)` 或 preview 的 `/api/research/indices/resolve` 识别。展示名称、机构、价格/全收益代码、输入口径和研究版本；未知代码不能猜前缀、改成同名指数。
3. 从resolve响应读取valuation_source_label、valuation_options与default_valuation_suffix；后缀仅在明确登记时默认选中，不按编制方/代码猜测。调用validate_index_valuation(code, valuation_suffix)验证所选代码的PE、PB，展示各自状态、数量和实际日期。两项通过才有valuation_token；无法取数时保留缺口，由用户明确选择None才可暂不接入估值，不能自动放弃估值。验证只读，不发布快照或更新任务。获得建档授权后，POST `/api/research/indices`，原样携带 resolve_token、valuation_token、expected_research_revision、稳定 request_id（两种token均绑定本次输入；有效期10分钟）。建档和首次补齐意图原子保存。回读返回的正式 ID 与 job_id。受理不等于采集完成。
4. 409 重新读取并展示差异；未知结果先回读，保留原 request_id，不能换版本强制提交。重复添加只保留一个指数档案。
5. 估值、价格、成分和财报日期分别解释。3/5/10年先看windows状态：partial_history可有分位值，但必须注明实际样本from/to及coverage起止，不能称完整年限；其他不足为null，不能补零。日频排除当前日，等值计半权重。高点日期是各自窗口内最高收盘最近出现日；不称为“本轮高点”。PE/PB不按价格与全收益点位比例缩放。
6. 分页/图表使用同一个 analysis_id；快照变化后重读，不拼接不同版本。财务当前篮子与历史有效名单分开；当前可得重述财报不冒充严格 PIT，缺年份与行业未知保留缺口。
7. 更新策略从 `/api/settings/data-updates` 读取，用原 expected_settings_revision PUT。展示六类策略的差异，单独核对更新授权；它不使用账本 revision。应用关闭时不承诺执行。
8. 明确授权更新后 POST `/api/data-updates/run`，携带 scopes、稳定 request_id；确认失败后的新重试使用新 request_id，只提交失败项的原 resource；结果未知先回读并保留原 request_id，不扩大到其他币种或模块。轮询 jobs 核对实际日期和逐项状态；历史失败不等于当前仍失败。
9. 条件试算用 `scenario INDEX_ID REQUEST.json` 或 `research_scenario(index_id,payload)` 走服务端 scenarios（只读，不需--confirm-write），传当前 analysis_id、Decimal 字符串、明确固定篮子假设。全收益缺少未来分红假设时不可计算，不输出买入分数或投资结论。

不合并 PR、不部署、不恢复或清理数据，除非另外获得相应授权。

增强读取：使用changes的from_date/to_date解释日、周、年度涨跌幅；daily_series中的MA按完整交易日计算。成分财务按financials的报告期展示，current_years不冒充历史指数。条件关系图使用scenario返回的curve/reference_points，可选reference_window=3/5/10；历史P10–P90为固定分母参照，不称底部或概率；reference_coverage为partial_history时明确实际区间。

客户端完整读取：research_index支持basis、window、method；research_part读取四种投影。跨页显式携带同一analysis_id；从capabilities发现主口径、方法、均线周期及情景参考窗口，不能只根据UI默认值推断。

支持的代码配对、估值来源和覆盖边界统一查阅[研究合同](../../../docs/research.md)，并读取当前resolve与module_status；不能从旧技能中的名称或代码推断最新支持范围。

当前指数全模块更新：先回读精确index_id，展示拟更新范围；在已有明确授权下调用`refresh_index(index_id, request_id)`。固定提交该ID与index_prices/memberships/valuations/financials四个scopes，省略resources覆盖八个模块；不顺带更新其他指数、持仓或汇率。自动策略关闭不阻止明确手动请求。保留request_id，回读job_id至终态后重新读取详情；解释market_cutoff、information_cutoff与各模块实际日期。`update_history()`只是最近5条合并展示，任务未出现在其中不代表失败或未执行，应按job_id查询。

红利低波100估值源为韭圈儿930955.CSI，只支持整体法，按available_valuation_methods选择。valuation_cross_check只比较中证同日PE，差异绝对值达10%提示；PB未校验、官方失败或日期不匹配均不得说成一致。不使用ETF.run，也不把中证快照拼进韭圈儿历史。
