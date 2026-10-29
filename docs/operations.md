# 知行财富：运行、发布与恢复

所有命令在开发仓库根目录执行。系统代码只在这里修改；正式目录由发布工具安装。默认工作区是源码的上级目录，可用 `scripts/manage.py --workspace PATH ...` 指定独立工作区。工具不初始化 Git，也不推送 GitHub。

首次安装、每次升级及交接给日常 Agent 的完整流程以 [安装与升级指南](install.md) 为准。首次安装为空账本；升级必须保全原有业务数据，不能清空、重建或回灌开发副本。日常启动命令使用已安装版本的解释器与脚本。

## 环境

红利再投份额估算只读当前环境的历史净值缓存。缺少所选日单位净值时，在全局设置对该基金同步历史行情，完成后回表单点击“重新读取净值”；同步失败保留缺失状态，可按确认单手填实际份额。该功能不改变数据库结构，不自动修改已有分红、交易或确认状态。

送转股导致历史收益跳变时，先在目标环境同步对应股票，查看企业行动数据子项及待核对事项；按公告/交割单确认倍数与日期，再预览并提交，回读份额、成本与收益。升级仅提供能力，不自动改写历史流水。前复权行情用于走势参考，不能代替送转股记账。

企业行动数据同步失败不代表已发现送转计划。0.8.5 支持纯现金方案中的差异化股东说明，现金金额差异保留在同步 notes 与分红核对说明；不得直接以平均金额替代个人实际到账。升级后若仍有旧解析失败提醒，在对应标的重试同步，确认最新 corporate_actions 子项成功及提醒消失；保留旧同步记录，不直接删库或创建送转流水来消除提醒。

已确认送转后补录历史买卖，先看预览中的历史送转重算影响。参与批次按除权日前剩余份额自动更新；已卖完的批次不参与，当日及之后新买入不参与。流水详情展示各批次送转前后份额，买入行同时保留原成交数量和当前剩余数量。确认待办会消失，送转历史流水仍保留；即使补录后无可送转持仓，也保留记录并显示不调整份额，无需重复确认。

提醒中心仍显示送转核对时，检查已录发生日期是否误用登记日。表单同时展示公告登记日与除权日，“使用公告除权日与倍数”只填写表单，须预览、填写更正原因并提交原记录才会生效。日期／比例冲突转为核对原记录，不能再新增一条送转。程序不自动改变正式流水日期。

开发前端更新后应重启同一开发服务，避免新页面搭配旧内存代码。收入接口缺少 summary 时页面保留明细并提示重启，不在前端以零伪造累计统计；正式环境始终使用固定版本包。

### 家庭模块与通知维护

0.8.5 使用 schema7（本次无结构迁移）。schema3→4 新增家庭记录等独立表，4→5 增加借款／借出与预计到期日期，5→6 仅新增收入表 family_income，不改写已有业务记录。升级先做完整 SQLite＋笔记备份；支持 schema1–6 备份恢复后迁移。旧日期未知时不推测，原费用目标、手工计划、盘点等记录保留，界面已退役。当前 schema7 不可用旧代码直接启动或仅回退程序。

通知启用偏好和组件位于各环境数据目录 `.native-notifications/`；稳定 Bundle ID 包含环境与数据目录身份，开发／测试默认关闭，统一在全局设置显式启用。每次安排查询系统授权和实际队列；改期、分红候选变化或恢复后重建。还款与保费根据记录中的规则自动生成，系统登记未来366天，页面仅展示今天到未来35天，过期自动移出页面。

分红提醒复用既有分红候选计算，只提醒 pending/needs_review，按组合与候选实质内容去重；重复同步时间不产生新提醒。未授权／提交失败的候选在恢复授权后可重新安排。原生点击统一使用 `?reminders=1&focus=事项ID` 进入全局提醒中心；旧分红和家庭中心链接保留兼容。通知本身不确认分红、不修改余额。

开发源文件 `native/FamilyNotifications.swift`；首次启用用 Command Line Tools 构建。`manage.py prepare` 在 macOS 发布包中编译 `native-build/FamilyNotifications`，记录哈希并在安装前验证。组件复用品牌图生成图标；构建变化保留原 Bundle ID。点击使用对应版本启动器与固定环境目录，端口属于其他账本时拒绝打开。

关闭主服务不会取消已登记的未来通知；关闭通知功能取消该身份的待发与已展示通知。通知默认不显示名称和金额；系统受理或 delivered_ids 不能证明用户看到。勿扰、休眠、关机和权限变化的实机表现仍需在目标 Mac 验收，模拟测试不能替代。借款／保费计算只是预计安排，实际余额通过统一编辑更新。

新增验证：`tests/test_family.py`、`scripts/browser_family.cjs`；统一 `scripts/verify.py --browser` 已纳入家庭页面及手机布局。测试只能使用明确标记的合成数据目录；系统通知测试需单独启用，不随自动回归发出真实通知。

| 环境 | 端口 | 配置 | 数据 |
|---|---|---|---|
| 开发 | 8877 | `local/dev/config.toml` | `local/dev/data/` |
| 正式 | 8765 | `runtime/config.toml` | `runtime/data/` |
| 合成测试 | 8876 | `local/test/config.toml` | `local/test/browser-test/` |

配置路径均相对工作区；TOML 中 data_dir/runtime_dir 相对配置文件。每个数据目录有独立环境标记，不能手工改标记来绕过隔离。启动器仅绑定127.0.0.1，端口冲突不会顺延；Cookie、运行记录、数据库和笔记独立。文件锁位于数据目录，跨版本仍只允许一个服务进程。隔离防止操作串用，不是同一系统用户下的安全沙箱。

```sh
.venv/bin/python scripts/manage.py init --environment dev
.venv/bin/python scripts/manage.py start --environment dev --open
.venv/bin/python scripts/manage.py status --environment dev
.venv/bin/python scripts/manage.py stop --environment dev
```

macOS 在源码目录执行 `launchers --environment dev`（完整命令前缀为 `.venv/bin/python scripts/manage.py`），生成“启动/停止知行财富（开发）.app”；`launchers --environment test` 在 `local/test/` 生成测试入口。正式发布成功后在 `runtime/` 自动生成“启动/停止知行财富.app”。

这些入口使用同一 Logo，通过 macOS 的应用查找机制优先使用 iTerm，未安装时使用系统 Terminal；不更改系统文件关联、不向已有终端会话注入命令。启动入口会打开页面；停止入口仅停止对应环境。可给入口创建 Finder 替身，不能把应用包移出它所在的工作区目录。生成的 `.app` 不进入 GitHub，公开仓库保留生成脚本和 Logo。

开发、测试页面的侧边栏和浏览器标题显示对应环境字样；正式页面只显示“知行财富”。开发默认不自动同步行情，可在页面手动同步；正式默认自动同步。

## 准备和发布

1. 按 [开发与审阅流程](development.md) 在需求分支完成开发、测试并提交 MR / PR。用户 review 通过、合并到 `master` 后，切回干净且跟踪 `origin/master` 的 master，执行 `git pull --ff-only`。不从需求分支部署正式环境。无 Git 源码包需核对来自已合并 master / 正式版本并固定源码清单哈希。
2. 从 master 执行 `.venv/bin/python scripts/manage.py prepare`。它核对本地 HEAD、跟踪引用及远端实时 master 一致，按源码白名单生成新版本，创建独立虚拟环境、安装锁定依赖，重新执行完整回归、构建和浏览器验收；正式实例继续运行。准备前后均核对来源，记录在 `release.json.provenance`。
3. 查看验证结果和 `release.json`，确认源码标识、数据库版本和迁移内容；有数据库变化时先用最新正式备份在私人副本演练。候选版本源码或构建改变后必须重新准备。
4. 在安装／升级授权范围内执行 `.venv/bin/python scripts/manage.py apply VERSION_ID`。VERSION_ID 使用prepare返回的完整标识，例如 `v0.8.0-源码哈希`。管理工具复核文件，停止写入及同步，用最新正式数据做完整备份，在隔离副本演练并核验后再迁移正式库；切换后只读验收并保存升级后保护备份，通过才开放业务。已有完整授权不重复确认；只要求准备版本时不擅自apply。
5. 查看 `.venv/bin/python scripts/manage.py status --environment production`，核对实际环境和版本。macOS 同时更新正式启动/停止应用及图标；若仅入口更新失败，服务保持已验证的新版本，并显示独立警告，可重新执行 `launchers --environment production` 修复。

首次正式安装前先运行 `init --environment production`，按安装指南交付空账本。已有安装的升级不重新初始化数据；用户明确要求迁移既有账本时，另走停机、保护备份及恢复验证流程。绝对不能把开发账本复制覆盖正式库。发布不更新、替换或清空投资笔记和业务账本。

发布文件位于 `runtime/releases/`，`runtime/current` 指向当前版本。每个版本有独立Python依赖和前端产物，不依赖开发虚拟环境；本机Python基础解释器仍需保留。正式版本不保留node_modules，正常启动不需要Node.js。不要移动已创建的虚拟环境目录。

### AI 客户端与日常工作区

开发源码中的客户端默认8877；发布包默认8765，按脚本位置而非当前工作目录判断。测试必须显式选择 `--environment test`／8876。`manage.py prepare` 包含合成实例的真实 HTTP 读写回归，随后检查候选包不带参数的 `ai_client.py target` 确实指向production／8765，并记录 `checks.ai_client=true`。安装/启动重新检查默认目标；apply在暂停所有后台任务并拒绝API写入的验收阶段，使用候选包客户端与精确正式数据目录执行只读health检查，再恢复正常服务。apply/rollback目标包还必须具备 `checks.deployment_guard=true`；不使用旧管理脚本绕过缺失的保护能力。

可在独立于开发工作区的目录初始化日常入口：

```sh
.venv/bin/python scripts/init_daily_workspace.py --workspace WORKSPACE --directory DAILY_DIRECTORY
```

只创建 `AGENTS.md` 和可执行 `zhixing`，目标目录已存在时拒绝覆盖，不复制源码或账本。需先安装支持环境校验的正式客户端。`./zhixing` 固定正式地址与数据目录，每次解析current并使用该版本的独立解释器；后续正常发布无需重写此入口。可用 `./zhixing target` 检查配置、`./zhixing read /api/health` 检查服务身份。入口不自动启动、部署或切到开发服务。

构建可能需要访问PyPI和npm。网络或依赖失败会停止准备、保留失败日志；不得删除有效断言或用未验证版本代替。失败的版本目录没有有效发布清单，不能apply；核对失败原因后可删除该未安装目录并重新prepare。

前端锁文件将 Redux Toolkit 固定为2.11.2、其 standard-schema/utils 依赖固定为0.3.0，修复旧锁文件引用不可下载版本的问题；版本在Recharts声明的兼容范围内，并经过空缓存安装及图表回归验证。

## 备份与刷新开发副本

```sh
.venv/bin/python scripts/manage.py backup --environment production
.venv/bin/python scripts/manage.py refresh-dev
```

backup 输出完整ZIP，含SQLite、所有引用的笔记历史和逐文件校验。运行中的实例通过服务内锁生成快照；离线备份必须获得数据目录锁。refresh-dev 是显式覆盖开发账本的操作：先备份正式库和现有开发库、停止开发实例，再恢复并迁移。它不会改写正式数据。

每日SQLite自动备份和同名.notes.zip必须成对保留，不能只复制SQLite遗漏笔记。自动备份保留最近30个有备份的日期；手工完整备份及保护备份不自动删除。备份也含私人数据，不能上传GitHub。

## 数据库迁移和回退

Alembic迁移位于 `migrations/versions/`，支持schema1–6逐步迁移到schema7。结构、数据和版本记录在同一事务中完成，失败回滚。软件版本与数据库版本分别管理。升级只允许已支持的版本，未知或更高版本拒绝写入。

`rollback VERSION_ID` 仅允许目标版本支持当前数据库和数据保护流程，仍须备份、演练、数据比对和只读验收；不会自动降级数据库。若升级后结构不兼容，或已产生新的业务写入，应保留当前完整备份后准备专门修复/回迁，不能直接以旧备份覆盖。迁移、启动或核验失败会写入阻断记录，保持停机，不自动重启旧程序或覆盖数据库。具体证据位置和阻断处理见安装指南。

离线维护入口：`scripts/maintenance.py snapshot|migrate|inspect|restore --data-dir PATH --environment dev|production|test`。snapshot/restore另需 `--archive PATH`。先停止对应服务；所有操作均受目录锁保护。恢复前自动保留原库，历史备份恢复后执行所需迁移。维护日志只输出数量、哈希和状态，不输出账本内容。

## GitHub发布边界

源码目录是唯一仓库根。公开内容为代码、运行Logo、合成测试和通用文档；整个工作区和任何真实数据都不进入Git。准备本地版本不要求先公开。

设置 `git config core.hooksPath .githooks` 启用提交/推送检查：主分支禁止直接提交和推送，需求分支检查公开源码边界。GitHub CI 执行合成测试、构建和源码检查。待提交内容、合成截图和历史仍须人工核对；忽略规则及扫描器不能证明绝无私人信息。Hooks 不替代服务端分支保护或人工 review。

作者：Leo · 2026.10.28

全局中心复用规范见 [design.md](design.md)。本次期间统计与全局聚合不新增数据库迁移，当时仍为 schema6；0.8.1 的存储迁移升级至 schema7。新增 `tests/test_family_summary_reminders.py` 覆盖同比和跨模块聚合；浏览器验收覆盖顶部唯一入口、分类柱形、月份侧栏、日期范围与全局跳转。原生代码有路由更新，发布包须重新编译，系统实际点击单独验收。


## 存储维护（0.8.1）

自动行情同步只跳过与同日同序列最后版本完全相同的内容（忽略fetched_at），真实来源修正继续追加。A→B→A仍算变化，不能仅因为旧版本出现过A就跳过。schema6→7只合并相同内容的自动行情，每种内容保留最新副本；不同价格、来源、序列、人工历史均保留，不按日期删行情。移除fx_scope重复索引，保留fx_daily_unique约束。迁移在事务内执行，压缩由停机维护流程执行。schema6→7在维护流程内核对迁移前后的业务表、有效行情、不同内容版本及笔记哈希；不一致时保持停机，不开放业务写入。核验只输出数量与哈希。

更新记录展示最近5条，后台默认最多保存20条（含运行中），不清理运行中任务；启动时将上次进程中断任务标记失败。业务revisions、笔记历史、交易锁定汇率不参加此轮转。日常自动备份仍保留30个有备份的日期。

完整备份导出先校验并持久化manual-*.zip，再删除该次导出的SQLite/notes.zip中间副本；失败时保留恢复材料。manage backup复用内容一致的已落盘ZIP。普通每日SQLite与notes.zip仍成对保留，保护备份不自动轮转。

文件清理必须先生成清单，核对候选和保留版本后再执行：

```sh
.venv/bin/python scripts/manage.py storage-plan --output ../local/work/storage-plan.json
.venv/bin/python scripts/manage.py storage-cleanup --plan ../local/work/storage-plan.json
```

只处理测试/过程目录中的Swift模块缓存、未使用的发布目录、随被清理版本产生的验收目录，以及有完整ZIP哈希证明的manual SQLite/笔记重复副本。保留当前版本、部署前驱、运行记录所指版本、当前schema最新两个完整版本和其他每种schema的最新完整版本。普通设计/过程材料、开发依赖、账本及所有保护备份不自动删除。清单含路径、内容哈希、字节数及备份见证；对象、运行指针或见证变化时拒绝陈旧清单。该命令是显式维护动作，不在服务启动或发布后自动执行。

Swift通知开发构建使用临时module-cache，成功或失败均清理该次缓存。发布准备原有编译缓存清理规则保持不变。
