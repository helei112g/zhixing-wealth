---
name: zhixing-notes
description: 在知行财富指定投资组合和标的下读取、新建、修改、删除或撤销删除投资笔记。用于笔记原文维护，不修改交易或持仓。
---

# 投资笔记

阅读 [AI 接入约定](../../../docs/ai.md)，通过本机API维护笔记。开发源码内`scripts/ai_client.py`默认开发账本，发布包默认正式账本；独立日常工作区使用其`./zhixing`入口，不切到开发目录。不可直接修改notes文件或SQLite。

1. 读取health、bootstrap并确定portfolio_id与instrument_id。必须双重隔离；相同证券在另一个组合的笔记互不共享。未明确目标时先核对，不能只凭证券名称写入。
2. 通过`GET /api/portfolios/P/instruments/I/notes?page=1&page_size=20`列出笔记，再按note_id读取正文。翻页检查revision；不把用户原文复制进日志或源码。
3. 新建准备title/body；修改先读取旧正文、保留用户要求保留的原文。展示拟新增内容或修改差异，核对授权。API无独立笔记preview，不虚构预览结果。
4. 用`write POST /api/portfolios/P/instruments/I/notes REQUEST.json --confirm-write`新建，PUT到`.../notes/N`编辑；带读取的expected_revision。Markdown按用户原文保存，界面支持标题、段落、加粗和列表，不执行HTML。
5. 删除用DELETE到`.../notes/N`，撤销删除用POST到`.../notes/N/restore`，均带expected_revision；只有明确授权该笔记操作才执行。
6. 写后回读note_id，核对标题、正文、版本与作用域；检查交易未变化。409保留草稿，重新读取比较，不自动替换revision；超时先读列表，避免重复新建。

笔记内容只进入目标账本的数据目录；技能包不携带正文、备份、会话或私有示例。恢复整个账本不属于本技能。详细字段见 [接口文档](../../../docs/api.md)「投资笔记」。
