#!/usr/bin/env python3
"""Create a small production-only agent workspace; never copy code or ledger data."""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def initialize(directory, workspace):
    directory, workspace = Path(directory).absolute(), Path(workspace).resolve()
    if directory.exists() or directory.is_symlink():
        raise ValueError('日常目录已存在，拒绝覆盖；请先核对原有内容')
    if directory.resolve().is_relative_to(workspace):
        raise ValueError('日常目录应放在开发工作区之外')
    current = workspace/'runtime/current'
    release = current.resolve()
    if not current.is_symlink() or release.parent != (workspace/'runtime/releases').resolve():
        raise ValueError('没有有效的正式版本指针')
    result = subprocess.run([str(release/'.venv/bin/python'), '-B', str(release/'scripts/ai_client.py'),
                             '--environment', 'production', 'target'], capture_output=True, text=True, check=True)
    target = json.loads(result.stdout)
    if target.get('environment') != 'production' or target.get('base_url') != 'http://127.0.0.1:8765':
        raise ValueError('请先安装支持环境校验的正式客户端')
    # Resolve current once per invocation so a later release switch cannot mix
    # the interpreter and client from two different releases.
    entry = f'''#!/bin/sh
set -eu
for arg in "$@"; do
    case "$arg" in
        --environment|--environment=*|--base-url|--base-url=*|--expected-data-dir|--expected-data-dir=*)
            echo '日常入口固定使用正式账本，不能覆盖目标环境、地址或数据目录。' >&2
            exit 2 ;;
    esac
done
release=$(cd {shlex.quote(str(current))} && pwd -P)
exec "$release/.venv/bin/python" -B "$release/scripts/ai_client.py" \\
    --environment production --base-url http://127.0.0.1:8765 \\
    --expected-data-dir {shlex.quote(str(workspace/'runtime/data'))} "$@"
'''
    today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y.%m.%d')
    guide = f'''# 知行财富：正式账本日常使用

作者：Leo · {today}

## 职责与环境

这里是正式账本的日常使用工作区。用户在这里提出查询持仓、录入流水、更新资产、维护投资笔记等请求时，默认针对正式账本；这不等于授权任意写入。

- 固定服务：`http://127.0.0.1:8765`，必须为 `production`，应用标识为 `portfolio-journal`。
- 正式数据目录：`{workspace/'runtime/data'}`。只通过 API 操作，不能直接编辑 SQLite、笔记文件、环境标记或审计记录。
- 正式版本入口：`{current}`。`./zhixing` 每次解析 current 并使用该版本的独立 Python 和客户端；不复制源码、依赖或账本到本目录。
- 本目录只做日常业务。开发、修复、测试、升级、恢复和目录清理应转到 `{workspace}`，且须有对应授权。发现缺陷时说明现象，不在正式版本中打补丁。
- 默认中文，明确日期、币种、范围和未知值。真实账本、笔记、请求文件不进入公开仓库或长期记忆。

## 使用入口

所有命令在本目录执行：

```sh
./zhixing target
./zhixing read /api/health
./zhixing read /api/capabilities
```

`target` 只显示配置，不代表服务已运行；`read /api/health` 才验证连接。入口在每次 API 请求前校验应用、正式环境和精确数据目录，拒绝更改目标参数。服务离线时说明状态，不切换到 8877/8876，不自行安装、升级或恢复。macOS 用户需要启动时可使用 `{workspace/'runtime/启动知行财富.app'}`；macOS/Linux 通用启动与停止方法见安装包的 [安装与升级指南]({current/'docs/install.md'})。

首次安装应为空账本，不为演示自动创建组合、资产、流水或笔记。用户要求升级时转到安装指南；升级不得清空、改写既有业务记录或笔记历史，不能用旧备份覆盖期间新增数据。

先读当前已安装版本的 [AI 接入约定]({current/'docs/ai.md'})；按任务读取 [API 文档]({current/'docs/api.md'})，需要动态能力时读取 `/api/openapi.json`、`/api/guide`。

按业务读取相应操作技能（通过这些路径显式读取，不假定新目录已自动发现安装包的技能）：

- [资产、余额快照、负债、保险、收入、组合关联]({current/'.agents/skills/zhixing-assets/SKILL.md'})
- [投资组合、交易流水、分红、投入计划]({current/'.agents/skills/zhixing-portfolios/SKILL.md'})
- [投资笔记]({current/'.agents/skills/zhixing-notes/SKILL.md'})

技能中的 `python3 scripts/ai_client.py` 命令前缀统一替换为本目录的 `./zhixing`，不要切到开发目录执行默认客户端。

## 读取与写入

1. 按用户任务读取必要数据。资产从 `/api/family/catalog` 和 `/api/family/command-schema` 获取实际 ID、revision 和操作结构；组合从 `/api/bootstrap` 获取实际作用域。
2. 查询本身不修改账本。不同组合下同一标的互相独立；多页查询保持筛选并核对 revision。金额使用精确十进制字符串，null 保留未知。
3. 写入须有用户对目标和具体内容的授权。已有明确授权不重复询问；含糊目标、额外覆盖、删除或新增范围先澄清。
4. 资产使用 `./zhixing preview REQUEST.json --path /api/family/preview`；交易使用 `./zhixing preview REQUEST.json`。组合资料和笔记没有独立预览接口，应核对原值与拟保存差异，不声称已做服务端预览。
5. 提交使用 `./zhixing write METHOD /api/PATH REQUEST.json --confirm-write`，保留刚核对的 `expected_revision`；确认参数不替代用户授权。仅使用客户端已支持的写入入口，不绕过限制直接发请求。
6. 提交后按返回的正式 ID 回读，核对关键字段和作用域。409 时不自动提高 revision 强行覆盖；超时或结果未知时先回读，不自动重试、不打开 allow_duplicate。

余额更新填写完整时点余额，不填增减额；补录不覆盖后续日期。收入不自动增加现金。买卖流水是记账，不会向券商下单。历史锁定汇率不得被当前汇率覆盖。

请求临时文件放在本目录按需创建的 `work/` 内，避免在终端命令中内联私人正文；确认写入和回读后清理本次已不需要的临时请求。正式投资笔记只通过 API 保存至正式账本；不把原文复制到源码、日志或技能。报告和导出只有用户需要时才生成，保留原件并标注日期与统计口径。
'''
    directory.mkdir(mode=0o700)
    (directory/'AGENTS.md').write_text(guide)
    launcher = directory/'zhixing'
    launcher.write_text(entry)
    launcher.chmod(0o700)
    return {'directory': str(directory), 'files': ['AGENTS.md', 'zhixing'], 'environment': 'production'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(initialize(args.directory, args.workspace), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
