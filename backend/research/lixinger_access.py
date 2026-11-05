"""Local-only credentials and a durable, fail-closed trial request gate.

Never include transport objects, response messages or credential values in errors.
The private directory is deliberately outside SQLite/note exports and source builds.
"""
import fcntl
import json
import os
import re
import secrets
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from ..common import ConflictError, DomainError, now

ENDPOINTS = ('cn/index', 'cn/index/candlestick', 'cn/index/fundamental',
             'cn/index/constituents', 'cn/index/constituent-weightings',
             'cn/index/fs/hybrid', 'cn/index/fs/non_financial', 'cn/index/fs/bank', 'cn/index/fs/security',
             'cn/index/margin-trading-and-securities-lending', 'cn/index/mutual-market',
             'cn/index/tracking-fund', 'cn/index/volatility')
ROOT_URL = 'https://open.lixinger.com/api/'


class LocalAccess:
    def __init__(self, directory):
        self.directory = Path(directory) / 'private-lixinger'
        self.lock = threading.RLock()

    @contextmanager
    def files(self):
        with self.lock:
            try:
                self.directory.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                with os.fdopen(os.open('access.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                       0o600, dir_fd=fd), 'r+b') as lock:
                    for info in (os.fstat(fd), os.fstat(lock.fileno())):
                        if info.st_uid != os.getuid() or info.st_mode & 0o077: raise DomainError('理杏仁本地私有目录或文件权限须为0700/0600')
                    if os.fstat(lock.fileno()).st_nlink != 1: raise DomainError('理杏仁本地锁文件不安全')
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    try: yield fd
                    finally: fcntl.flock(lock, fcntl.LOCK_UN)
            except OSError:
                raise DomainError('理杏仁本地文件无法安全访问；拒绝符号链接或权限不符的路径') from None
            finally:
                if 'fd' in locals(): os.close(fd)

    @staticmethod
    def read(fd, name):
        try: handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError: return None
        with os.fdopen(handle, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DomainError('理杏仁本地文件类型或权限不安全')
            content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024: raise DomainError('理杏仁本地文件过大')
            return content

    @staticmethod
    def write(fd, name, content):
        # Inspect an existing destination before an atomic replacement (no symlink following).
        LocalAccess.read(fd, name)
        temp = '.' + secrets.token_hex(16)
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd); os.fsync(fd)
        finally:
            try: os.unlink(temp, dir_fd=fd)
            except FileNotFoundError: pass

    def state(self, fd):
        raw = self.read(fd, 'budget.json')
        if raw is None:
            try: os.stat('token', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise DomainError('已有Token但调用计数文件缺失；已停止采集，禁止自动重置额度')
            return dict(revision=0, enabled=False, automatic=False, limit=20, counts={}, blocked={}, mode='trial')
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or type(value['revision']) is not int or value['revision'] < 0 or type(value['limit']) is not int or not 1 <= value['limit'] <= 1000: raise ValueError()
            if set(value)-{'revision','enabled','automatic','limit','counts','blocked','mode','last_attempt'}: raise ValueError()
            if type(value['enabled']) is not bool or type(value['automatic']) is not bool or value['mode'] != 'trial': raise ValueError()
            if not isinstance(value['counts'],dict) or not isinstance(value['blocked'],dict): raise ValueError()
            if any(k not in ENDPOINTS or type(v) is not int or v < 0 for k, v in value['counts'].items()): raise ValueError()
            if any(k not in ENDPOINTS or v not in ('permission_or_quota','provider_rejected') for k,v in value['blocked'].items()): raise ValueError()
            if 'last_attempt' in value and (not isinstance(value['last_attempt'],str) or not re.fullmatch(r'[0-9T:+. -]{19,40}',value['last_attempt'])): raise ValueError()
            return value
        except (ValueError, KeyError, TypeError): raise DomainError('调用计数文件损坏；已阻断真实采集，禁止自动重置额度') from None

    def store_state(self, fd, value): self.write(fd, 'budget.json', json.dumps(value, ensure_ascii=False).encode())

    def status(self):
        with self.files() as fd:
            value = self.state(fd)
            # Only inspect file metadata, not its contents, on a status request.
            try:
                info = os.stat('token', dir_fd=fd, follow_symlinks=False)
                configured = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and not info.st_mode & 0o077
            except FileNotFoundError: configured = False
            return dict(value, configured=configured, path=str(self.directory / 'token'),
                        endpoints=[dict(endpoint=e, used=value['counts'].get(e, 0), limit=value['limit'],
                                        remaining=max(0, value['limit']-value['counts'].get(e, 0)),
                                        blocked=value['blocked'].get(e)) for e in ENDPOINTS])

    def save_token(self, token, expected):
        # Only called by the local HTTP process, never from a CLI argument or AI schema.
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._-]{8,512}', token):
            raise DomainError('Token格式不合法；只接受8至512位字母、数字、点、下划线或连字符')
        with self.files() as fd:
            value = self.state(fd)
            if type(expected) is not int or expected != value['revision']: raise ConflictError('本地凭据设置已变化，请重新读取状态')
            value['revision'] += 1
            # A changed credential never resets the used counters nor starts requests.
            value['enabled'] = False; value['automatic'] = False
            self.store_state(fd, value)
            self.write(fd, 'token', token.encode('ascii'))
        return self.status()

    def configure(self, expected, enabled, limit, automatic=False, clear_block=None):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(enabled) is not bool or type(automatic) is not bool:
            raise DomainError('每接口预算须为1至1000次，采集开关须明确指定')
        with self.files() as fd:
            value = self.state(fd)
            if type(expected) is not int or expected != value['revision']: raise ConflictError('本地调用预算已变化，请重新读取后核对')
            if clear_block and clear_block not in ENDPOINTS: raise DomainError('无效端点')
            value.update(revision=value['revision']+1, enabled=enabled, limit=limit, automatic=automatic)
            if clear_block: value['blocked'].pop(clear_block, None)
            self.store_state(fd, value)
        return self.status()

    def block(self, endpoint, reason):
        with self.files() as fd:
            value = self.state(fd); value['blocked'][endpoint] = reason
            value['revision'] += 1; self.store_state(fd, value)

    def request(self, endpoint, params, *, automatic=False, transport=None):
        if endpoint not in ENDPOINTS or 'token' in params: raise DomainError('不允许的理杏仁请求')
        with self.files() as fd:
            value = self.state(fd)
            if automatic and (not value['enabled'] or not value['automatic']):
                raise DomainError('理杏仁自动采集未启用；可在数据来源页手动更新')
            if value['blocked'].get(endpoint): raise DomainError('该理杏仁端点已暂停；请先核对权限/额度再恢复')
            if value['counts'].get(endpoint, 0) >= value['limit']: raise DomainError('该理杏仁端点已到本机预算上限；本次未请求')
            raw = self.read(fd, 'token')
            if not raw: raise DomainError('尚未在本机配置理杏仁Token')
            # Reserve before sending. Failed, timed-out and interrupted calls still consume a slot.
            value['counts'][endpoint] = value['counts'].get(endpoint, 0)+1
            value['revision'] += 1; value['last_attempt'] = now(); self.store_state(fd, value)
        error = None; result = None; blocked = None
        try:
            if transport:
                status, obj = transport(endpoint, dict(params, token=raw.decode('ascii')))
            else:
                import requests
                with requests.Session() as session:
                    session.trust_env = False
                    with session.post(ROOT_URL+endpoint, json=dict(params, token=raw.decode('ascii')),
                                      timeout=(8, 25), allow_redirects=False, stream=True) as response:
                        status = response.status_code
                        chunks=[]; size=0
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > 32*1024*1024: raise ValueError()
                            chunks.append(chunk)
                        obj = json.loads(b''.join(chunks))
            if raw.decode('ascii') in json.dumps(obj, ensure_ascii=False): error = '理杏仁响应包含敏感认证信息，已丢弃且未保存'
            elif status in (401,403,429): blocked = 'permission_or_quota'; error = '理杏仁权限或额度受限；已暂停该端点，无自动重试'
            elif status != 200: error = '理杏仁请求未成功；旧数据保留，无自动重试'
            elif not isinstance(obj, dict) or obj.get('code') != 1:
                blocked = 'provider_rejected'; error = '理杏仁拒绝请求；已暂停该端点，请本地核对权限与额度'
            elif not isinstance(obj.get('data'), list): error = '理杏仁响应结构不符，旧数据保留'
            else: result = obj['data']
        except Exception:
            error = '理杏仁网络或响应解析失败；本次已计数，无自动重试'
        finally:
            raw = None
        if blocked: self.block(endpoint, blocked)
        # Raise outside the exception block so neither request nor secret can enter chained logs.
        if error: raise DomainError(error) from None
        return result
