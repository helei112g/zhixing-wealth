"""Local environment identity and a lock shared by all code versions for one ledger."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import tomllib
from pathlib import Path

from .common import DomainError

LABELS = {'dev': '开发环境', 'production': '正式环境', 'test': '测试环境'}
PORTS = {'dev': 8877, 'production': 8765, 'test': 8876}


def workspace_for(root):
    root = Path(root).resolve()
    return root.parents[2] if root.parent.name == 'releases' and root.parent.parent.name == 'runtime' else root.parent


def config_path(workspace, environment):
    return Path(workspace) / ('runtime/config.toml' if environment == 'production' else f'local/{environment}/config.toml')


def read_config(path):
    path = Path(path).resolve()
    with path.open('rb') as handle:
        values = tomllib.load(handle)
    if values.get('environment') not in LABELS:
        raise DomainError('环境配置缺少有效 environment')
    result = dict(values)
    for key in ('data_dir', 'runtime_dir'):
        if not isinstance(values.get(key), str) or not values[key]:
            raise DomainError(f'环境配置缺少 {key}')
        result[key] = (path.parent / values[key]).resolve()
    port = values.get('port')
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise DomainError('端口应为1024至65535之间的整数')
    if not isinstance(values.get('auto_sync', False), bool):
        raise DomainError('auto_sync 必须为布尔值')
    return result


def claim_environment(directory, environment):
    if environment not in LABELS:
        raise DomainError('未知运行环境')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / 'portfolio.sqlite'
    if database.is_symlink() or (database.exists() and database.stat().st_nlink != 1):
        raise DomainError('账本必须是独立普通文件，不能使用符号链接或硬链接共享数据库')
    marker = directory / '.environment.json'
    if marker.is_symlink():
        raise DomainError('环境标记不能是符号链接')
    if not marker.exists():
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump({'environment': environment}, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            pass
    if json.loads(marker.read_text()).get('environment') != environment:
        raise DomainError('数据目录所属环境不匹配；禁止跨环境启动或覆盖标记')
    return directory


def cookie_name(directory, environment):
    identity = hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:12]
    return f'portfolio_session_{environment}_{identity}'


class LedgerProcessLock:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.handle = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / '.instance.lock'
        if path.is_symlink():
            raise DomainError('运行锁不能是符号链接')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self.handle = os.fdopen(fd, 'a+')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise DomainError('该数据目录已有服务或维护操作运行，请先停止对应实例') from None
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.close()
