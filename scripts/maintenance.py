#!/usr/bin/env python3
"""Offline ledger maintenance. Never run against a running instance."""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.backup_archive import check_size, file_sha256, write_archive
from backend.common import DomainError, now
from backend.environment import LABELS, LedgerProcessLock, claim_environment
from backend.migration import SCHEMA, schema_version
from backend.notes import NoteFiles, references
from backend.service import Service
from backend.store import Store


def snapshot(directory, target, *, protected=False):
    """Snapshot under the ledger lock. Protected upgrade copies preserve the current database at any size."""
    directory, target = Path(directory), Path(target)
    if target.exists() or target.is_symlink():
        raise DomainError('备份目标已存在，拒绝覆盖')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=target.parent) as temp:
        copy = Path(temp) / 'portfolio.sqlite'
        source = sqlite3.connect((directory/'portfolio.sqlite').resolve().as_uri()+'?mode=ro', uri=True)
        dest = sqlite3.connect(copy)
        try:
            source.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise DomainError('数据库完整性检查失败')
            dest.row_factory = sqlite3.Row
            paths = references(dest)
            schema = int(dest.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0])
        finally:
            dest.close(); source.close()
        if not protected:
            check_size(copy.stat().st_size, '备份数据库')
        partial = Path(temp) / 'backup.zip'
        notes = NoteFiles(directory)
        write_archive(partial, copy, ((name, notes.read_bytes(name)) for name in sorted(paths)),
                      schema=schema, application='知行财富')
        if not protected:
            check_size(partial.stat().st_size, '备份ZIP')
        with partial.open('rb') as handle: os.fsync(handle.fileno())
        os.link(partial, target)
    return {'backup': str(target), 'schema': schema, 'notes':len(paths),
            'sha256':file_sha256(target)}


def inspect(directory):
    directory = Path(directory)
    db = sqlite3.connect((directory/'portfolio.sqlite').resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory=sqlite3.Row
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts={name: db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        paths=references(db)
        for name in paths:NoteFiles(directory).read_bytes(name)
        return {'schema':schema_version(directory/'portfolio.sqlite'),
                'revision':db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0],
                'tables':counts,'note_versions':len(paths),'integrity':db.execute('PRAGMA quick_check').fetchone()[0],
                'database_bytes':(directory/'portfolio.sqlite').stat().st_size}
    finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['snapshot','migrate','inspect','restore'])
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--environment',choices=LABELS,required=True)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    claim_environment(args.data_dir,args.environment)
    with LedgerProcessLock(args.data_dir):
        if args.action=='snapshot':
            if not args.archive:parser.error('snapshot 需要 --archive')
            result=snapshot(args.data_dir,args.archive)
        elif args.action=='inspect':
            result=inspect(args.data_dir)
        elif args.action=='restore':
            if not args.archive:parser.error('restore 需要 --archive')
            result=Service(Store(args.data_dir)).restore(args.archive)
        else:
            before=None
            audit_before=None
            if (args.data_dir/'portfolio.sqlite').exists():
                label='protected-upgrade-'+now().replace(':','-')+'.zip'
                before=snapshot(args.data_dir,args.data_dir/'backups'/label, protected=True)
                if schema_version(args.data_dir/'portfolio.sqlite') == 6:
                    from backend.storage_audit import capture
                    audit_before=capture(args.data_dir)
            # The complete snapshot above already protects DB and note history together.
            store=Store(args.data_dir, migration_backup=False)
            service=Service(store)
            state=store.state()
            from backend.ledger import Ledger
            for portfolio in state['portfolios']:Ledger(state,portfolio).validate()
            db=sqlite3.connect(store.path)
            try:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                db.execute('VACUUM')
            finally:db.close()
            result={'before':before,'after':inspect(args.data_dir)}
            if audit_before:
                from backend.storage_audit import capture, verify
                result['storage_verification']=verify(audit_before,capture(args.data_dir))
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except DomainError as exc:raise SystemExit(str(exc)) from None
