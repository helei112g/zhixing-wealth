"""Immutable Markdown documents. SQLite stores only scope, path and deletion state."""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

from .common import DomainError

MAX_NOTE_BYTES = 1024 * 1024
MAX_NOTES_BYTES = 128 * 1024 * 1024
MAX_NOTE_FILES = 9998
PREFIX = '<!-- xingzhi-note-v1 '
ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')


def validate_text(title, body):
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100 or '\n' in title or '\r' in title:
        raise DomainError('笔记标题须为1至100字，不能包含换行')
    if not isinstance(body, str) or not body.strip() or len(body) > 200000 or '\x00' in body:
        raise DomainError('笔记正文须为1至200000字，不能包含空字符')
    if '\x00' in title:
        raise DomainError('笔记标题不能包含空字符')
    return title.strip(), body


def validate_path(path):
    if not isinstance(path, str):
        raise DomainError('笔记文件路径无效')
    parts = PurePosixPath(path).parts
    if (len(parts) != 5 or parts[0] != 'notes' or '/'.join(parts) != path
            or not all(ID.fullmatch(x) for x in parts[1:4])
            or not re.fullmatch(r'[a-f0-9]{32}\.md', parts[4])):
        raise DomainError('笔记文件路径无效')
    return parts


def encode_note(meta, body):
    title, body = validate_text(meta['title'], body)
    meta = dict(meta, title=title)
    header = json.dumps(meta, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e')
    data = (PREFIX + header + ' -->\n# ' + title + '\n\n' + body).encode('utf-8')
    if len(data) > MAX_NOTE_BYTES:
        raise DomainError('单篇笔记文件不能超过1 MiB')
    return data


def decode_note(data, path):
    parts = validate_path(path)
    try:
        if len(data) > MAX_NOTE_BYTES:
            raise ValueError('size')
        text = data.decode('utf-8')
        header, content = text.split('\n', 1)
        if not header.startswith(PREFIX) or not header.endswith(' -->'):
            raise ValueError('header')
        meta = json.loads(header[len(PREFIX):-4])
        if not isinstance(meta, dict) or set(meta) != {'id', 'portfolio_id', 'instrument_id', 'title', 'created_at', 'updated_at'}:
            raise ValueError('metadata')
        if [meta['portfolio_id'], meta['instrument_id'], meta['id']] != list(parts[1:4]):
            raise ValueError('scope')
        for key in ('created_at', 'updated_at'):
            if not isinstance(meta[key], str) or datetime.fromisoformat(meta[key]).tzinfo is None:
                raise ValueError('date')
        heading = '# ' + meta['title'] + '\n\n'
        if not content.startswith(heading):
            raise ValueError('title')
        body = content[len(heading):]
        validate_text(meta['title'], body)
        return {**meta, 'body': body, 'path': path}
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise DomainError('笔记文件结构或所属组合、标的不匹配，请从完整备份恢复') from None


class NoteFiles:
    def __init__(self, directory):
        self.directory = Path(directory)

    def location(self, path, *, create=False):
        parts = validate_path(path)
        current = self.directory
        for part in parts[:-1]:
            current /= part
            if current.is_symlink():
                raise DomainError('笔记目录不能是符号链接')
            if create:
                current.mkdir(mode=0o700, exist_ok=True)
            if current.exists() and not current.is_dir():
                raise DomainError('笔记目录不可用')
        target = current / parts[-1]
        if target.is_symlink():
            raise DomainError('笔记文件不能是符号链接')
        return target

    def read_bytes(self, path):
        target = self.location(path)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_NOTE_BYTES:
                    raise DomainError('笔记文件不是普通文件或超过1 MiB')
                data = f.read(MAX_NOTE_BYTES + 1)
            decode_note(data, path)
            return data
        except OSError:
            raise DomainError('无法读取笔记文件，请检查数据目录，或在新数据目录恢复完整备份') from None

    def read(self, path):
        return decode_note(self.read_bytes(path), path)

    def install(self, path, data):
        """Never replace an existing version, including during restore."""
        decode_note(data, path)
        target = self.location(path, create=True)
        if target.exists():
            if self.read_bytes(path) != data:
                raise DomainError('同一路径存在不同笔记内容，恢复已停止，现有数据未覆盖')
            return
        temp = target.with_suffix('.pending-' + uuid.uuid4().hex)
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            # Hard-link publishes the completed file atomically without replacing another version.
            os.link(temp, target)
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            temp.unlink(missing_ok=True)

    def write(self, meta, body):
        path = '/'.join(('notes', meta['portfolio_id'], meta['instrument_id'], meta['id'], uuid.uuid4().hex + '.md'))
        self.install(path, encode_note(meta, body))
        return path


def references(db):
    """All live/deleted documents and prior versions needed by this DB snapshot."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investment_notes'").fetchone():
        return set()
    paths = set()
    rows = [dict(r) for r in db.execute('SELECT * FROM investment_notes')]
    scopes = {tuple(r) for r in db.execute('SELECT portfolio_id,instrument_id FROM allocations')}
    for row in rows:
        parts = validate_path(row['path'])
        if (list(parts[1:4]) != [row['portfolio_id'], row['instrument_id'], row['id']]
                or (row['portfolio_id'], row['instrument_id']) not in scopes
                or row['deleted'] not in (0, 1)):
            raise DomainError('笔记索引与组合、标的不一致')
        paths.add(row['path'])
    for r in db.execute("SELECT before_data,after_data FROM revisions WHERE entity='investment_note'"):
        for value in r:
            if value is not None:
                try:
                    record = json.loads(value)
                    parts = validate_path(record['path'])
                    if (list(parts[1:4]) != [record['portfolio_id'], record['instrument_id'], record['id']]
                            or (record['portfolio_id'], record['instrument_id']) not in scopes):
                        raise DomainError('笔记修订索引所属范围不一致')
                except (KeyError, TypeError, ValueError):
                    raise DomainError('笔记修订索引无效') from None
                paths.add(record['path'])
    if len(paths) > MAX_NOTE_FILES:
        raise DomainError('笔记及历史版本超过9998个文件，无法生成完整备份')
    return paths
