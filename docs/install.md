# 安装、升级与 Agent 交接

作者：Leo · 2026.10.28

本指南面向拿到源码后负责安装的 Agent，也适用于人工安装。首次安装交付空账本；升级保留安装者自己的正式数据。源码包中的合成测试代码只用于隔离验收，不能运行在日常账本上，不能作为初始化种子。

## 先确定任务和目录

- **首次安装**：没有既有正式账本，准备新的运行工作区和独立日常目录。
- **升级／修复／回退**：已有 `runtime/current`、数据库、投资笔记或历史备份。必须走下文升级流程，不得以重新安装为理由清空数据。
- **日常使用**：进入已有日常目录，按其中的 `AGENTS.md` 使用 `./zhixing`；不编译、不部署。

先核对操作系统、源码来源、用户指定的目录、既有安装和端口。信息能从文件或服务确认就自行检查；只有路径选择、覆盖或授权范围不明确时询问。用户已明确授权完整安装时，完成检查后执行，不重复请求同一安装授权。用户只要求分析或准备版本时，不安装正式环境。

推荐目录关系如下，名称可由安装者选择，不依赖作者电脑路径：

```text
chosen-parent/
├── zhixing-workspace/
│   ├── portfolio-app/       # 从仓库或源码包取得的源码
│   ├── local/              # 开发配置、隔离测试与验收材料
│   └── runtime/            # 正式版本、配置、数据与备份
└── zhixing-daily/           # 正式日常入口与 Agent 指引
```

源码、运行工作区和日常目录的精确绝对路径在本机确定。日常目录应在运行工作区之外；不要复制整个作者工作区、`runtime`、`.venv`、数据库、笔记或备份。保留用户已有目录，发生冲突时停止核对。

## 前置检查

当前支持 macOS / Linux 的 POSIX 启动流程；Windows 原生未验证，不能宣称支持。需要 Python 3.11+、Node.js 20.19+、npm，能访问锁定依赖的来源。

```sh
python3 --version
node --version
npm --version
```

macOS 的发布准备还需要 Xcode Command Line Tools（用 `xcrun --find swiftc` 检查）以编译通知组件。Linux 使用页面提醒；Playwright 的 Chromium 需要对应系统依赖。涉及安装系统包或提权时说明具体动作，遵守安装者授权，不暗中修改系统设置。

固定端口为正式8765、开发8877、测试8876，均仅监听127.0.0.1。检查端口占用：若已有本应用，核对 `/api/health` 的环境和数据目录并识别已有安装；若为其他程序，停止说明冲突，不杀进程或自动换端口。

## 首次安装

以下命令在源码根目录执行。先将变量设为安装者确认的实际绝对路径，不要把占位符直接执行：

```sh
zhixing_workspace='/absolute/path/to/zhixing-workspace'
zhixing_daily='/absolute/path/to/zhixing-daily'
```

1. 核对源码及工作区边界，确认不是已有账本的重装。安装本项目依赖，不修改全局 Python 环境：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
npm --prefix frontend ci
frontend/node_modules/.bin/playwright install chromium
```

Linux 若提示缺少 Chromium 系统依赖，按提示说明依赖和权限后安装；不能跳过浏览器验收伪装成功。

2. 运行 `source_check.py`。准备固定发布包；`prepare` 会创建该版本独立的依赖、构建前端，并执行全套合成测试、空白安装、浏览器及 AI HTTP 验收：

```sh
.venv/bin/python scripts/source_check.py
.venv/bin/python scripts/manage.py --workspace "$zhixing_workspace" prepare
```

记录返回的完整 `release` 标识和 `source_id`，后续用实际标识替换 `RELEASE_ID`。有 Git 时发布要求源码仓库干净；不能为了通过检查丢弃未提交修改。网络、构建或验证失败时停止并保留原因，不删断言或改业务逻辑绕过。

3. 在安装授权范围内初始化正式配置并应用已验证版本。只有这一步产生新的正式空数据库；没有从作者或开发环境复制账本的步骤：

```sh
.venv/bin/python scripts/manage.py --workspace "$zhixing_workspace" init --environment production
.venv/bin/python scripts/manage.py --workspace "$zhixing_workspace" apply RELEASE_ID
.venv/bin/python scripts/manage.py --workspace "$zhixing_workspace" status --environment production
.venv/bin/python scripts/check_empty_install.py --data-dir "$zhixing_workspace/runtime/data" --environment production
```

`apply` 会先在隔离临时目录演练，再安装正式库并以只读模式验收。首次安装各业务表、行情/汇率缓存、更新记录和笔记均为空；保留数据库结构、版本、环境身份等必要系统元数据，以及安装生成的空库保护备份。币种、资产类别和基准定义是程序能力，不是预录入的用户持仓。

空账本的启动和定时任务不自动下载行情或汇率。创建自己的组合、标的或家庭记录后，后续自动同步按原机制运行；明确手动同步也可能生成行情缓存。不要为“演示能用”往正式账本写示例资产、交易、笔记或收入。

空白检查为只读，遇到已有内容返回失败，不清空、不重建；此时核对是否误选了已有安装。**升级后的账本不运行空白检查来要求归零。**

4. 初始化日常工作区，并从新目录验证正式客户端：

```sh
.venv/bin/python scripts/init_daily_workspace.py --workspace "$zhixing_workspace" --directory "$zhixing_daily"
cd "$zhixing_daily"
./zhixing target
./zhixing read /api/health
./zhixing read /api/capabilities
```

日常目录只生成 `AGENTS.md` 和 `zhixing`，不会复制程序、依赖或账本。入口每次跟随 `runtime/current`，固定正式地址与精确数据目录；升级后继续使用同一入口。Agent 根据日常 `AGENTS.md` 显式读取当前安装包的资产、组合和笔记技能，不假定跨目录技能已自动加载。

5. 交付实际安装路径、版本、正式地址、日常目录、启动/停止方法及验证结果。请用户将日常目录作为后续 Agent 工作目录；只有用户要求开发时才进入源码目录。

## 正常启动与停止

从安装包使用对应解释器和管理脚本，不依赖开发目录：

```sh
"$zhixing_workspace/runtime/current/.venv/bin/python" "$zhixing_workspace/runtime/current/scripts/manage.py" --workspace "$zhixing_workspace" start --environment production --open
"$zhixing_workspace/runtime/current/.venv/bin/python" "$zhixing_workspace/runtime/current/scripts/manage.py" --workspace "$zhixing_workspace" stop --environment production
```

macOS 也可使用 `runtime/` 下生成的启动/停止应用。Linux 使用上述命令。安装基础 Python 的路径需要保留，不可随意搬动已创建的虚拟环境。

## 每次升级的强制流程

**开发、修复、升级和发布都不授权改写正式业务数据。** 不可重建账本、导入 demo、用开发库回灌、覆盖期间新增数据、更改历史交易或锁定汇率、删除笔记历史；账务更正必须作为用户另行明确授权的业务操作，通过 API 留审计。

1. 在源码目录完成修改、测试、构建和边界检查，并运行 `prepare`。同步维护 API、AI 客户端、技能及文档。正式程序只能来自已验证的固定发布包，不在 `runtime/current` 原位编辑源码。
2. 记录候选包哈希、schema与变更范围。已有业务字段和记录必须保持；允许新增字段或空表。会重写/删除既有记录、改变账务含义的迁移不能混入普通版本发布，必须另行准备明确方案及对应验证，没有“强制跳过核验”的开关。
3. 对已授权的版本执行 `apply RELEASE_ID`。工具停止正式服务及同步，并等待数据目录锁释放（HTTP停止响应不代表进程已退出），用停机后的**最新正式数据库与全部被引用笔记历史**生成完整保护备份，并记录只含数量与哈希的证据。等待超时保持阻断，不继续迁移。
4. 工具将这份完整备份展开到隔离临时目录，用候选版本执行离线迁移演练。这个私人副本不作自动测试 fixture，不进入源码或发布包。演练核对原表的字段、记录关联、内容、数量、修订号、身份及笔记历史；不一致就停止，尚未修改正式库。
5. 演练通过后，直接迁移最新正式库，**不把演练副本覆盖回正式库**。停机状态下再次核验，然后启动候选包的只读验收模式：API 拒绝写入，行情同步、通知等后台任务暂停。健康状态、客户端和数据指纹通过后，保存升级后完整恢复点，再开放正常业务。
6. `runtime/.runtime/deployment-*.json` 保存本轮结果及前后保护备份路径；`runtime/last-deployment.json` 记录成功切换。报告实际通过的检查，不把静态检查、模拟流程测试和本机正式安装混为一谈。

运行期正常行情同步与用户记账属于正常业务行为；它们可以生成新的缓存或审计。发布数据比对在停机／只读且暂停后台任务的阶段进行，不能把正常业务变化误当作允许发布脚本改写历史的理由。

## 失败与回退

部署中断或核验失败时，`deployment-guard.json` 会阻断正常启动；支持此机制的运行实例也会拒绝新的写请求、暂停后续定时任务。启动超时会终止本次创建的进程。保留现有数据、失败证据和保护备份，不得删除阻断记录后强行启动，也不得自动恢复旧备份或自动重启旧程序。若停止失败，阻断记录会标记 `stop_failed`，需先核对实际服务及已在执行的同步任务，不能宣称已经停写。

只有查明差异、完成有授权的修复并重新核验后，才能处理阻断状态。回退代码同样要求数据库兼容、备份、演练、数据核验和只读验收；目标旧包必须支持这套保护流程。旧包缺少保护能力时拒绝直接回退，不能用旧管理脚本绕过。发生新业务写入后，不能用升级前备份覆盖账本；恢复或数据回迁必须另行制定保全新记录的方案。

## 开源包边界

只发布源码仓库。`source_check.py --output PATH` 生成白名单源码包，包含安装指南、操作技能及合成测试代码，排除数据库、笔记、备份、运行目录、构建环境和私人材料。发布前还须检查已跟踪文件与历史；扫描通过不能代替人工复核。安装者的数据由本机首次初始化创建，不随源码包分发。

完整回归入口 `scripts/verify.py --browser` 包含空白页面与 API 检查；单元测试覆盖自动同步不造数据、已有数据检查不清空、迁移／启动差异阻断、前后备份、笔记历史以及源码包边界。持续集成和 `prepare` 共用该入口，后续开发不能省略这些约束。
