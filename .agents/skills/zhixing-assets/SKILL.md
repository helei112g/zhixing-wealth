---
name: zhixing-assets
description: 查询或维护知行财富的资产更新、余额快照、负债、保险、收入和组合关联。用于个人或家庭的本机记账；不执行证券交易或投资建议。
---

# 资产分析与资产更新

侧边栏为「资产分析 → 总览／趋势分析／保险／收入」，在总览通过「资产更新」维护项目与余额。原 family API 路径和命令保持兼容。

先阅读 [AI 接入约定](../../../docs/ai.md)。从技能目录向上三级是该版本的代码根目录；独立日常工作区优先使用其 `./zhixing` 入口替换下列客户端前缀，不切到开发目录，不依赖全局安装。

## 查询

- 先检查目标服务 `/api/health` 的 environment；开发源码默认8877，发布包默认8765，日常工作区固定正式账本。业务写入仍须有对应用户授权。测试显式选择test／8876；服务不在线时说明未连接，不自行安装、切换版本或环境。
- 读 `/api/capabilities` 与 `/api/family/catalog` 获得能力、revision、项目及关联ID；需要写入时读 `/api/family/command-schema` 获取当前 JSON Schema，勿根据名称猜ID。
- 总览：`/api/family/summary?grain=month&period=YYYY-MM`；按年用year。月度比较上月末，年度比较上年末；结束日余额不可累加。自定义必须提供start/end。
- 趋势分析：`/api/family/history?grain=month&comparison=previous&start=YYYY-MM-DD&end=YYYY-MM-DD`。按月比较上月末、按年比较上年末；用户明确要求同比时改用 comparison=yoy 并说明基线，null保留未知，统计范围变化不可编造比率。
- 收入：`/api/family/income?year=YYYY`；提醒统一用`/api/reminders`。收入不自动增加现金，待收款不是现金，保险保额/保费不是资产。现金覆盖仅按计划还款折算，不包括生活开支。

## 维护

1. 确认目标项目、币种、金额日期和完整余额。已有现金/固定资产/手工理财/应收款优先`valuation`追加快照；填写完整余额而不是增减额，不能再建同名项目。早于首笔记录时说明会提前起始日并显式使用`extend_start`。
2. 新增或编辑资料用`item`，编辑传`record_id`，保留类别/币种。只改余额不要覆盖还款或保险规则。
3. 债务在`payment_plan`一次设置还款方式、首期日期、期数和必要年利率；保险支持一次付清或按年付款。年利率`3.5`表示3.5%。计划不会自动扣减余额，不逐笔新建提醒，应收款不建收款计划。
4. 组合多选用`links`和`portfolio_ids`，解除用`unlink`和`link_ids`，不是组合ID；当天生效。此方向只读组合金额，不能从资产端写回组合。
5. 收入用`income`，kind为salary/rent/fund/custom；custom填写名称。更正同一记录带`record_id`，不叠加创建。
6. 用客户端预览：`python3 scripts/ai_client.py preview --path /api/family/preview REQUEST.json`。核对返回summary、付款预览与revision；预览中的新ID不是正式ID。
7. 核对当前用户对精确内容的授权后，原样提交同一请求与expected_revision：`python3 scripts/ai_client.py write POST /api/family/commands REQUEST.json --confirm-write`。已有明确授权无需重复询问，出现新覆盖或范围变化时再确认。
8. 使用提交结果的正式ID回读catalog/overview/income，核对日期、金额、历史和组合未被改动。409或超时遵循接入约定，不能自动换revision重试。

请求文件放在源码外的本机工作目录，不写入技能或示例；不要输出凭据或完整私人账本。参数细节按需阅读 [接口文档](../../../docs/api.md) 的资产章节。

- 收入年度累计与分类占比使用 `/api/family/income?year=YYYY` 的 `summary`；`total` 未知或为零时不编造占比，`recorded_months` 不等于全年完整记录。字段详见 `docs/api.md`。
