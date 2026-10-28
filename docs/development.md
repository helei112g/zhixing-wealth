# 开发、审阅与正式发布

作者：Leo · 2026.10.29

每个需求都经过 **需求分支 → 开发与测试 → MR / PR → review 与合并 → master 发布**。GitHub 使用 Pull Request（PR）名称，与本文 MR 指同一流程。提交 PR 不等于授权合并或部署。

## 开始一个需求

先查看 `git status` 和远端，保护已有改动；工作区不干净时不要自动丢弃或 stash。确认本地 master 跟踪 origin/master 后执行：

```sh
git switch master
git pull --ff-only
git switch -c feat/your-change
git config core.hooksPath .githooks
```

修复可用 `fix/…`，文档可用 `docs/…`。开发只连接独立 dev 环境；自动验收与公开截图只能用新建的 test 合成账本。测试库、开发库和演练快照永远不能回灌正式环境。

## 测试后提交 PR

同步维护功能直接影响的 API、AI 客户端、技能、文档与测试。至少运行相关单元测试、前端构建和 `source_check.py`；UI 变化验收实际渲染。安装、启动、迁移和发布代码变化必须运行完整验收（包含 empty）：

```sh
.venv/bin/python scripts/verify.py --browser
git add <本次文件>
git commit -m "Describe the user-visible change"
git push -u origin HEAD
```

在 GitHub 创建以 `master` 为 base 的 PR，说明问题、改后行为、实际验证和数据影响。公开截图须为人工检查过的合成画面，并注明来源。CI 成功后交由用户 review；根据意见修改时重新执行受影响检查，更新 PR，不能复用旧提交的验证结论。不得由 Agent 擅自合并或开启自动合并。

本地 hooks 拒绝在主分支提交和直接推送主分支；这是协作防误操作措施，可被 Git 参数跳过，不能当作服务端权限控制。仓库管理员可进一步设置 master 分支保护，要求 PR 与 `verify` 检查；审阅与合并仍由维护者负责。

## 合并之后才准备正式版本

核对 PR 已合并、目标提交的 CI 通过，并取得安装／升级授权。在干净的源码仓库执行：

```sh
git switch master
git pull --ff-only
.venv/bin/python scripts/manage.py prepare
```

`prepare` 要求当前是 `master`、upstream 为 `origin/master`，本地 HEAD、本地跟踪引用和远端实时提交一致。它重新构建并运行完整测试，记录提交号、分支和源码哈希；本地超前、落后、分叉、未提交改动、游离 HEAD、需求分支或远端不可访问都会停止。它不会自动合并，也不能单靠 Git 提交证明人工 review；合并记录由维护者核对。

之后按 [安装与升级](install.md) 执行 `apply RELEASE_ID`，使用最新正式数据做停写、完整备份、隔离演练、内容比对和只读验收。正式部署不能清空、覆盖或更改现有业务记录、交易汇率及笔记历史。准备版本不自动部署；本轮提交 PR 后止步于审阅。

无 Git 的源码压缩包继续按哈希准备发布。首次安装者须核对下载源为已合并 master 或对应正式版本，不能通过删除 `.git` 将未合并分支伪装成发布包。

## README 产品截图

图片来自隔离的合成 test 实例，见 [截图数据与重建方式](showcase.md)。这些图只展示功能；安装、升级、启动及日常任务均不导入示例。新增图片必须逐个加入源码白名单并人工检查，不能放宽整个截图目录或所有图片后缀。
