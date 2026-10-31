from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
import zipfile
from collections import OrderedDict
from copy import deepcopy
from contextlib import contextmanager
from functools import wraps
from inspect import signature
from pathlib import Path

from .common import ConflictError, DomainError, json_dumps, now, today
from .notes import NoteFiles, references, decode_note, MAX_NOTES_BYTES

from .migration import SCHEMA, migrate, schema_version
MAX_BACKUP_DATABASE_BYTES = 512 * 1024 * 1024
JOB_HISTORY_LIMIT = 20
READ_CACHE_LIMIT = 48


def cached_query(method):
    """Cache read projections only; keep validation and argument types distinct."""
    parameters = signature(method)
    def key_of(value):
        if isinstance(value, dict):
            return ('dict', tuple((k, key_of(v)) for k, v in sorted(value.items())))
        if isinstance(value, (list, tuple)):
            return (type(value).__name__, tuple(map(key_of, value)))
        return (type(value).__name__, value)
    @wraps(method)
    def read(self, *args, **kwargs):
        bound = parameters.bind(self, *args, **kwargs)
        bound.apply_defaults()
        key = (method.__module__, method.__qualname__,
               tuple((k, key_of(v)) for k, v in bound.arguments.items() if k != 'self'))
        return self.store.cached_read(key, lambda: method(self, *args, **kwargs),
                                      clock=method.__globals__.get('today'))
    return read


NOTES_TABLE = '''CREATE TABLE IF NOT EXISTS investment_notes(
    id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
    path TEXT UNIQUE NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS notes_scope ON investment_notes(portfolio_id,instrument_id,deleted);'''


class Store:
    def __init__(self, directory: Path, *, migration_backup=True):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'portfolio.sqlite'
        self.backups = self.directory / 'backups'
        self.backups.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.cache = {}
        self.note_files = NoteFiles(self.directory)
        old_schema = schema_version(self.path)
        if old_schema is not None and old_schema < SCHEMA and migration_backup:
            self.backup('before-schema-upgrade-' + uuid.uuid4().hex)
        migrate(self.path)
        from .research.service import validate_structure
        with self.connection() as db: validate_structure(db)

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=WAL')
            try:
                yield db
            finally:
                db.close()

    @contextmanager
    def transaction(self, *, protected=None, expected_revision=None):
        with self.lock:
            self.backup(protected or f'auto-{today().isoformat()}', once=protected is None)
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    if expected_revision is not None:
                        current = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
                        if str(expected_revision) != current:
                            raise ConflictError('账本已变化，请重新读取、预览并确认；本次未写入。')
                    yield db
                    db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
                    db.commit()
                    self.cache.clear()
                except Exception:
                    db.rollback()
                    raise

    def backup(self, name=None, *, once=False):
        with self.lock:
            label = name or ('manual-' + now().replace(':', '-'))
            if not label.replace('-', '').replace('.', '').replace('+', '').isalnum():
                raise DomainError('备份名称不合法')
            target = self.backups / (label + '.sqlite')
            if target.is_symlink():
                raise DomainError('备份目标不能是符号链接')
            if once and target.exists():
                return target
            while (target.exists() or target.with_suffix('.notes.zip').exists()
                   or target.with_suffix('.notes.zip').is_symlink() or target.with_suffix('.zip').exists()
                   or target.with_suffix('.zip').is_symlink()):
                target = self.backups / (label + '-' + uuid.uuid4().hex[:8] + '.sqlite')
            sidecar = target.with_suffix('.notes.zip')
            try:
                with self.connection() as source:
                    dest = sqlite3.connect(target)
                    try:
                        source.backup(dest)
                        dest.row_factory = sqlite3.Row
                        paths = references(dest)
                    finally:
                        dest.close()
                with zipfile.ZipFile(sidecar, 'x', zipfile.ZIP_DEFLATED) as z:
                    total = 0
                    for path in sorted(paths):
                        data = self.note_files.read_bytes(path)
                        total += len(data)
                        if total > MAX_NOTES_BYTES:
                            raise DomainError('笔记及历史版本超过128 MiB，完整备份未生成')
                        z.writestr(path, data)
            except Exception:
                target.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                raise
            if label.startswith('auto-'):
                old = sorted(self.backups.glob('auto-????-??-??.sqlite'), reverse=True)[30:]
                for p in old:
                    if not p.is_symlink():
                        p.unlink()
                        p.with_suffix('.notes.zip').unlink(missing_ok=True)
            return target

    def state(self, db=None):
        if db is None:
            with self.connection() as connection:
                return self.state(connection)
        def objects(table, where=''):
            return [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {table} {where}')]
        return {
            'portfolios': objects('portfolios'), 'instruments': objects('instruments'),
            'allocations': objects('allocations'),
            'holding_labels': objects('holding_labels') if db.execute("SELECT 1 FROM sqlite_master WHERE name='holding_labels'").fetchone() else [],
            'events': objects('events', 'WHERE deleted=0 ORDER BY date,sequence,id'),
            'prices': [dict(json.loads(r['data']), version_id=r['id']) for r in db.execute('SELECT id,data FROM prices ORDER BY id')],
            'fx': [dict(json.loads(r['data']), version_id=r['id']) for table in ('fx', 'fx_overrides') if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() for r in db.execute(f'SELECT id,data FROM {table} ORDER BY id')],
            'reservations': objects('reservations'), 'reviews': objects('reviews'),
            'corporate_actions': objects('corporate_actions'),
            'revision': int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]),
        }

    def cached_read(self, key, load, *, clock=None):
        """Bounded, private copies keyed by revision/day, cleared by every write.

        Load outside the database mutex. A concurrent write or midnight prevents
        publication under the previous key; it never relabels an old result.
        """
        clock = clock or today
        with self.connection() as db:
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            date = str(clock())
            cache = self.cache.setdefault('read-responses', OrderedDict())
            cache_key = (revision, date, key)
            if cache_key in cache:
                cache.move_to_end(cache_key)
                return deepcopy(cache[cache_key])
        result = load()
        with self.connection() as db:
            current = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if current == revision and str(clock()) == date and self.cache.get('read-responses') is cache:
                cache[cache_key] = deepcopy(result)
                while len(cache) > READ_CACHE_LIMIT:
                    cache.popitem(last=False)
        return result

    def upsert_fx(self, db, item):
        """Called inside BEGIN IMMEDIATE; automatic and daily manual quotes are idempotent."""
        item = dict(item)
        item.pop('providers', None)
        table = 'fx_overrides' if item.get('manual') else 'fx'
        daily = not item.get('manual') or item.get('mode') == 'daily'
        before = None
        if daily:
            condition = " AND json_extract(data,'$.mode')='daily' AND COALESCE(json_extract(data,'$.cancelled'),0)=0" if item.get('manual') else ''
            before = db.execute(f'SELECT id,data FROM {table} WHERE currency=? AND date=?' + condition,
                                (item['currency'], item['date'])).fetchone()
        if before:
            old = json.loads(before['data'])
            meaningful = lambda row: {k: v for k, v in row.items() if k not in ('fetched_at', 'providers')}
            if meaningful(old) == meaningful(item):
                return 'unchanged', old
            db.execute(f'UPDATE {table} SET end_date=?,data=? WHERE id=?',
                       (item.get('end_date'), json_dumps(item), before['id']))
            return 'updated', old
        db.execute(f'INSERT INTO {table}(currency,date,manual,end_date,data) VALUES (?,?,?,?,?)',
                   (item['currency'], item['date'], int(bool(item.get('manual'))), item.get('end_date'), json_dumps(item)))
        return 'inserted', None

    def audit(self, db, entity, entity_id, before, after, reason):
        db.execute('INSERT INTO revisions(entity,entity_id,reason,before_data,after_data,created_at) VALUES (?,?,?,?,?,?)',
                   (entity, entity_id, reason or '记录', json_dumps(before) if before is not None else None,
                    json_dumps(after) if after is not None else None, now()))

    def get_id(self):
        with self.connection() as db:
            return db.execute("SELECT value FROM meta WHERE key='app_id'").fetchone()[0]

    def jobs(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM jobs ORDER BY json_extract(data, '$.started_at') DESC, rowid DESC LIMIT 20")]

    def save_job(self, job):
        with self.connection() as db:
            db.execute('INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (job['id'], json_dumps(job)))
            self.prune_jobs(db)
            db.commit()

    @staticmethod
    def prune_jobs(db):
        # Running jobs are never evicted. Start order, not finish/update order,
        # determines which completed, partial, failed or legacy records survive.
        running = db.execute("SELECT COUNT(*) FROM jobs WHERE json_extract(data,'$.status')='running'").fetchone()[0]
        db.execute("""DELETE FROM jobs WHERE rowid IN (
            SELECT rowid FROM jobs WHERE COALESCE(json_extract(data,'$.status'),'') <> 'running'
            ORDER BY json_extract(data,'$.started_at') DESC,rowid DESC LIMIT -1 OFFSET ?)
            """, (max(0, JOB_HISTORY_LIMIT - running),))

    def recover_jobs(self):
        """Only call at process startup, while the data-directory process lock is held."""
        with self.connection() as db:
            for rid, payload in db.execute("SELECT id,data FROM jobs WHERE json_extract(data,'$.status')='running'").fetchall():
                job = json.loads(payload)
                job.update(status='error', interrupted=True, finished_at=now(),
                           message='上次进程退出，更新任务已中断；可重新同步')
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json_dumps(job), rid))
            self.prune_jobs(db)
            db.commit()

    def validate_backup(self, data: bytes):
        if not data.startswith(b'SQLite format 3\x00') or len(data) > MAX_BACKUP_DATABASE_BYTES:
            raise DomainError('不是受支持的SQLite账本，或解压后文件超过512 MiB')
        temp = self.directory / ('restore-check-' + uuid.uuid4().hex + '.sqlite')
        try:
            temp.write_bytes(data)
            db = sqlite3.connect(f'file:{temp}?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            try:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise DomainError('备份完整性检查失败')
                version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if not version or int(version[0]) not in range(1, SCHEMA + 1):
                    raise DomainError('备份版本不受支持')
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
                    raise DomainError('备份包含非本应用结构')
                required = {'meta', 'portfolios', 'instruments', 'allocations', 'events', 'prices', 'fx',
                            'reservations', 'reviews', 'corporate_actions', 'revisions', 'jobs'}
                found = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required.issubset(found):
                    raise DomainError('备份缺少必要数据表')
                if int(version[0]) >= 2 and 'investment_notes' not in found:
                    raise DomainError('备份缺少投资笔记索引')
                if int(version[0]) >= 3 and not {'fx_overrides', 'alembic_version'}.issubset(found):
                    raise DomainError('备份缺少当前版本迁移结构')
                if int(version[0]) >= 4:
                    from .family_domain import TABLES
                    from .family_service import validate_family_backup
                    if not (set(TABLES) if int(version[0]) >= 6 else set(TABLES)-{'family_income'}).issubset(found):
                        raise DomainError('备份缺少家庭资料表')
                    validate_family_backup(db)
                references(db)
                state = self.state(db)
                if int(version[0]) >= 8:
                    if 'holding_labels' not in found:
                        raise DomainError('备份缺少持仓标签表')
                    for row in db.execute('SELECT portfolio_id,instrument_id,data FROM holding_labels'):
                        payload = json.loads(row['data'])
                        if (payload.get('portfolio_id'), payload.get('instrument_id')) != (row['portfolio_id'], row['instrument_id']):
                            raise DomainError('备份持仓标签的作用域与索引不一致')
                    from .holding_analysis import validate_labels_backup
                    validate_labels_backup(state)
                if int(version[0]) >= 10:
                    from .research.service import validate_backup as validate_research_backup
                    validate_research_backup(db)
                from .ledger import Ledger
                for portfolio in state['portfolios']:
                    Ledger(state, portfolio).validate()
            finally:
                db.close()
            return temp
        except (sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
            temp.unlink(missing_ok=True)
            if isinstance(exc, DomainError):
                raise
            raise DomainError('备份结构或记录无效，现有账本未替换') from None
        except Exception:
            temp.unlink(missing_ok=True)
            raise

    def restore(self, data, note_files=None):
        with self.lock:
            temp = self.validate_backup(data)
            try:
                files = note_files or {}
                check = sqlite3.connect(temp)
                try:
                    check.row_factory = sqlite3.Row
                    paths = references(check)
                finally:
                    check.close()
                if set(files) != paths:
                    raise DomainError('备份笔记文件不完整或含未关联文件，现有账本未替换')
                for path, content in files.items():
                    decode_note(content, path)
                    target = self.note_files.location(path)
                    if target.exists() and self.note_files.read_bytes(path) != content:
                        raise DomainError('备份笔记与现有同路径文件冲突，现有数据未覆盖')
                before = self.backup('before-restore-' + uuid.uuid4().hex)
                with self.connection() as db:
                    previous_revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                    previous_public=dict(db.execute('SELECT key,value FROM research_meta'))
                # Prepare and validate everything before switching the live DB. Immutable files
                # can be installed first: a failed switch leaves the previous DB fully usable.
                prepared = sqlite3.connect(temp)
                try:
                    # The replacement must be a self-contained file, with no WAL left
                    # under the temporary filename after the atomic rename.
                    prepared.execute('PRAGMA journal_mode=DELETE')
                    restored_revision = int(prepared.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                    prepared.execute("UPDATE meta SET value=? WHERE key='revision'", (str(max(previous_revision, restored_revision) + 1),))
                    prepared.commit()
                finally:
                    prepared.close()
                migrate(temp)
                with sqlite3.connect(temp) as public:
                    public.execute('PRAGMA journal_mode=DELETE')
                    restored_public=dict(public.execute('SELECT key,value FROM research_meta'))
                    for key in ('revision','settings_revision'):
                        value=max(int(previous_public.get(key,0)),int(restored_public.get(key,0)))+1
                        public.execute('INSERT INTO research_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
                for path, content in files.items():
                    self.note_files.install(path, content)
                with self.connection() as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                for suffix in ('-wal', '-shm'):
                    Path(str(self.path) + suffix).unlink(missing_ok=True)
                os.replace(temp, self.path)
                self.cache.clear()
            finally:
                temp.unlink(missing_ok=True)
            return before.name
