"""Explicit, hash-checked cleanup of rebuildable files; never prune ledger history."""
import hashlib
import json
import os
import shutil
import stat
import zipfile
from collections import defaultdict
from pathlib import Path

from backend.common import DomainError, now
from backend.environment import LedgerProcessLock


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def inventory(path):
    """Hash names, contents and symlink targets without following symlinks."""
    path = Path(path)
    entries = [path]
    if path.is_dir() and not path.is_symlink():
        for base, dirs, files in os.walk(path, followlinks=False):
            entries.extend(Path(base) / name for name in dirs + files)
    result = hashlib.sha256()
    size = 0
    for entry in sorted(set(entries)):
        mode = entry.lstat().st_mode
        relative = '.' if entry == path else entry.relative_to(path).as_posix()
        if stat.S_ISLNK(mode):
            record = ['link', relative, os.readlink(entry)]
        elif stat.S_ISDIR(mode):
            record = ['dir', relative]
        elif stat.S_ISREG(mode):
            size += entry.stat().st_size
            record = ['file', relative, file_hash(entry)]
        else:
            raise DomainError('清理候选含特殊文件，拒绝处理')
        result.update(json.dumps(record, ensure_ascii=False).encode())
    return {'sha256': result.hexdigest(), 'bytes': size}


def safe_path(root, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ('.', '..') for p in parts):
        raise DomainError('清理路径必须位于工作区')
    path = root
    for part in parts:
        path /= part
        if path.is_symlink():
            raise DomainError('清理目标或父目录不能是符号链接')
    if not path.is_relative_to(root):
        raise DomainError('清理路径超出工作区')
    return path


def retained_releases(root):
    releases = root / 'runtime/releases'
    current = root / 'runtime/current'
    keep = set()
    if current.is_symlink():
        if current.resolve().parent != releases.resolve():
            raise DomainError('当前版本指针超出发布目录')
        keep.add(current.resolve().name)
    deploy = root / 'runtime/last-deployment.json'
    if deploy.is_file():
        previous = json.loads(deploy.read_text()).get('previous')
        if previous: keep.add(previous)
    for record in (root / 'runtime/.runtime/server.json', root / 'local/dev/.runtime/server.json', root / 'local/test/.runtime/server.json'):
        if record.is_file():
            running = Path(json.loads(record.read_text()).get('root', ''))
            if running.parent == releases: keep.add(running.name)
    schemas = defaultdict(list)
    valid = {}
    failed = set()
    for path in releases.iterdir() if releases.is_dir() else []:
        if path.is_symlink() or not path.is_dir(): continue
        manifest = path / 'release.json'
        if manifest.is_file() and not manifest.is_symlink():
            info = json.loads(manifest.read_text())
            if info.get('release') != path.name or not info.get('checks', {}).get('browser'):
                continue
            valid[path.name] = info
            schemas[info['schema']].append((info.get('built_at', ''), path.name))
        elif (path / 'prepare-failed.json').is_file():
            failed.add(path.name)
    current_schema = valid.get(current.resolve().name, {}).get('schema') if current.is_symlink() else None
    for schema, values in schemas.items():
        # Keep recovery software for every historical schema, plus two builds
        # supporting the current schema. The deployment predecessor is pinned.
        count = 2 if schema == current_schema else 1
        keep.update(name for _, name in sorted(values, reverse=True)[:count])
    return keep, set(valid) | failed


def duplicate_pairs(directory):
    archives = []
    for path in sorted(directory.glob('*.zip')):
        if path.is_symlink() or path.name.endswith('.notes.zip'): continue
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or 'portfolio.sqlite' not in names or 'manifest.json' not in names: continue
                manifest = json.loads(archive.read('manifest.json'))
                database = archive.read('portfolio.sqlite')
                if archive.testzip() is not None or hashlib.sha256(database).hexdigest() != manifest.get('sha256'): continue
                hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names if name not in ('portfolio.sqlite', 'manifest.json')}
                if hashes != manifest.get('note_files', {}): continue
                archives.append((path, manifest['sha256'], hashes))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    for database in sorted(directory.glob('manual-*.sqlite')):
        notes = database.with_suffix('.notes.zip')
        if database.is_symlink() or notes.is_symlink() or not notes.is_file(): continue
        try:
            with zipfile.ZipFile(notes) as archive:
                if archive.testzip() is not None: continue
                hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
            checksum = file_hash(database)
            witness = next((p for p, db_hash, note_hashes in archives if db_hash == checksum and note_hashes == hashes), None)
            if witness: yield database, notes, witness
        except (OSError, zipfile.BadZipFile):
            continue


def make_plan(workspace):
    root = Path(workspace).resolve()
    keep, known = retained_releases(root)
    candidates = []

    def add(path, kind, witness=None):
        relative = path.relative_to(root).as_posix()
        path = safe_path(root, relative)
        if not path.exists(): return
        item = {'path': relative, 'kind': kind, **inventory(path)}
        if witness:
            item.update(witness=witness.relative_to(root).as_posix(), witness_sha256=file_hash(witness))
        candidates.append(item)

    for pattern in ('local/test/*/.native-notifications/module-cache', 'local/work/*/native/module-cache'):
        for path in sorted(root.glob(pattern)):
            add(path, 'compiler_cache')
    removed = known - keep
    for name in sorted(removed):
        add(root / 'runtime/releases' / name, 'inactive_release')
        add(root / 'local/work' / name, 'superseded_verification')
    for location in ('runtime/data/backups', 'local/dev/data/backups'):
        directory = safe_path(root, location)
        if directory.is_dir():
            for database, notes, witness in duplicate_pairs(directory):
                add(database, 'duplicate_manual_backup', witness)
                add(notes, 'duplicate_manual_backup', witness)
    paths = {Path(item['path']) for item in candidates}
    candidates = [item for item in candidates if not any(parent in paths for parent in Path(item['path']).parents)]
    return {'format': 1, 'created_at': now(), 'workspace': str(root),
            'kept_releases': sorted(keep), 'candidates': candidates}


def apply_plan(workspace, plan):
    root = Path(workspace).resolve()
    if plan.get('format') != 1 or plan.get('workspace') != str(root):
        raise DomainError('清单版本或所属工作区不匹配')
    with LedgerProcessLock(root / 'runtime/.runtime'):
        current = {r['path']: r for r in make_plan(root)['candidates']}
        requested = plan.get('candidates', [])
        if len({r['path'] for r in requested}) != len(requested):
            raise DomainError('清单含重复路径')
        # Validate the whole plan before removing its first file. A changed
        # current release, witness backup or candidate invalidates the plan.
        for item in requested:
            if current.get(item['path']) != item:
                raise DomainError('清理清单已变化，请重新生成和核对；未执行删除')
        deleted = []
        for item in requested:
            path = safe_path(root, item['path'])
            if inventory(path) != {k: item[k] for k in ('sha256', 'bytes')}:
                raise DomainError('候选内容在清理期间变化，已停止后续删除')
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
            deleted.append(item['path'])
        return {'deleted': deleted, 'bytes': sum(r['bytes'] for r in requested), 'kept_releases': make_plan(root)['kept_releases']}
