"""Read-only installation/deployment evidence. No ledger values leave this module."""
import hashlib
import json
import sqlite3
from pathlib import Path

from .common import DomainError
from .notes import NoteFiles, references

SYSTEM_TABLES = {'meta', 'alembic_version', 'jobs'}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def capture(directory, baseline=None):
    directory = Path(directory).resolve()
    database = directory/'portfolio.sqlite'
    if not database.is_file() or database.is_symlink():
        raise DomainError('核验要求已有的独立账本；不会创建或清空数据库')
    db = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables, counts = {}, {}
        for name in names:
            count = db.execute('SELECT COUNT(*) FROM '+quoted(name)).fetchone()[0]
            counts[name] = count
            if name in SYSTEM_TABLES: continue
            columns = [r[1] for r in db.execute('PRAGMA table_info('+quoted(name)+')')]
            # Additive migrations may add columns, but every existing field and
            # row must retain its exact value and association with the same row.
            expected = (baseline or {}).get('tables', {}).get(name, {}).get('columns', columns)
            if not set(expected).issubset(columns):
                raise DomainError('升级删除了既有字段，停止开放业务：'+name)
            rows = db.execute('SELECT '+','.join(map(quoted, expected))+' FROM '+quoted(name)).fetchall()
            serialized = sorted(json.dumps(tuple(r), ensure_ascii=False, separators=(',', ':')) for r in rows)
            tables[name] = {'columns': expected, 'count': count, 'sha256': digest(serialized)}
        metadata = dict(db.execute('SELECT key,value FROM meta'))
        schema = int(metadata.pop('schema'))
        # Schema and Alembic history may advance. Revision, app identity and all
        # other metadata must remain unchanged by a deployment.
        note_files = NoteFiles(directory)
        note_hashes = {name: hashlib.sha256(note_files.read_bytes(name)).hexdigest()
                       for name in sorted(references(db))}
        return {'schema': schema, 'integrity': integrity, 'tables': tables, 'counts': counts,
                'metadata_sha256': digest(metadata), 'notes_sha256': digest(note_hashes),
                'note_versions': len(note_hashes)}
    finally:
        db.close()


def verify(before, after):
    changed = [name for name, value in before['tables'].items() if after['tables'].get(name) != value]
    changed += [name for name, value in after['tables'].items() if name not in before['tables'] and value['count']]
    changed += [key for key in ('metadata_sha256', 'notes_sha256', 'note_versions') if before[key] != after[key]]
    if before['integrity'] != 'ok' or after['integrity'] != 'ok': changed.append('integrity')
    if changed:
        raise DomainError('升级数据核验不一致，禁止开放业务；差异项：'+','.join(changed))
    return {'preserved': True, 'tables_checked': len(before['tables']), 'note_versions': before['note_versions']}


def require_empty(directory):
    result = capture(directory)
    nonempty = {name: count for name, count in result['counts'].items()
                if name not in ('meta', 'alembic_version') and count}
    notes = Path(directory)/'notes'
    if notes.exists() and any(p.is_file() or p.is_symlink() for p in notes.rglob('*')):
        nonempty['note_files'] = True
    if nonempty or result['integrity'] != 'ok':
        raise DomainError('首次安装不是空账本；不会清空已有数据：'+','.join(nonempty))
    return {'empty': True, 'schema': result['schema'], 'tables_checked': len(result['counts']), 'note_versions': 0}
