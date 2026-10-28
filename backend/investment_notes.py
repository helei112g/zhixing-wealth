"""Portfolio-and-instrument scoped notes; never store document text in SQLite."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from .common import DomainError, ZONE
from .notes import validate_text, validate_path


class InvestmentNotes:
    def __init__(self, store):
        self.store = store

    def scope(self, db, portfolio_id, instrument_id):
        if not db.execute('SELECT 1 FROM allocations WHERE portfolio_id=? AND instrument_id=?',
                          (portfolio_id, instrument_id)).fetchone():
            raise DomainError('标的不属于当前组合')

    def index(self, db, portfolio_id, instrument_id, note_id, *, deleted=False):
        self.scope(db, portfolio_id, instrument_id)
        row = db.execute('SELECT * FROM investment_notes WHERE id=? AND portfolio_id=? AND instrument_id=? AND deleted=?',
                         (note_id, portfolio_id, instrument_id, int(deleted))).fetchone()
        if not row:
            raise DomainError('当前组合、标的下找不到这篇笔记')
        if list(validate_path(row['path'])[1:4]) != [portfolio_id, instrument_id, note_id]:
            raise DomainError('笔记索引与所属组合、标的不一致')
        return dict(row)

    @staticmethod
    def revision(db):
        return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def list(self, portfolio_id, instrument_id, *, page=1, page_size=20):
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise DomainError('page 必须是正整数')
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise DomainError('page_size 必须为1至100')
        with self.store.connection() as db:
            self.scope(db, portfolio_id, instrument_id)
            items = []
            for row in db.execute('SELECT * FROM investment_notes WHERE portfolio_id=? AND instrument_id=? AND deleted=0',
                                  (portfolio_id, instrument_id)):
                try:
                    if list(validate_path(row['path'])[1:4]) != [portfolio_id, instrument_id, row['id']]:
                        raise DomainError('笔记索引与所属组合、标的不一致')
                    note = self.store.note_files.read(row['path'])
                    body = note.pop('body')
                    note['excerpt'] = re.sub(r'(?m)^\s*(?:#{1,6}\s+|\d+\.\s+)', '', body).replace('**', '').strip()[:160]
                    items.append(dict(note, available=True))
                except DomainError as exc:
                    # Do not hide a record when its disk file is unavailable.
                    items.append(dict(row, title='笔记文件暂时无法读取', excerpt='', created_at='', updated_at='', available=False, error=str(exc)))
            items.sort(key=lambda n: (n['created_at'], n['id']), reverse=True)
            total = len(items)
            count = max(1, (total + page_size - 1) // page_size)
            page = min(page, count)
            return {'items': items[(page - 1) * page_size:page * page_size], 'total': total,
                    'page': page, 'page_size': page_size, 'page_count': count,
                    'portfolio_id': portfolio_id, 'instrument_id': instrument_id, 'revision': self.revision(db)}

    def count(self, portfolio_id, instrument_id):
        with self.store.connection() as db:
            self.scope(db, portfolio_id, instrument_id)
            return db.execute('SELECT COUNT(*) FROM investment_notes WHERE portfolio_id=? AND instrument_id=? AND deleted=0',
                              (portfolio_id, instrument_id)).fetchone()[0]

    def get(self, portfolio_id, instrument_id, note_id):
        with self.store.connection() as db:
            row = self.index(db, portfolio_id, instrument_id, note_id)
            return {**self.store.note_files.read(row['path']), 'revision': self.revision(db)}

    def save(self, portfolio_id, instrument_id, payload, note_id=None):
        title, body = validate_text(payload.get('title'), payload.get('body'))
        revision = payload.get('expected_revision')
        if revision is None:
            raise DomainError('保存笔记必须提供 expected_revision，请先读取当前笔记')
        path = None
        with self.store.lock:
            try:
                with self.store.transaction(expected_revision=revision) as db:
                    self.scope(db, portfolio_id, instrument_id)
                    before = self.index(db, portfolio_id, instrument_id, note_id) if note_id else None
                    previous = self.store.note_files.read(before['path']) if before else None
                    stamp = datetime.now(ZONE).isoformat(timespec='microseconds')
                    meta = {'id': note_id or uuid.uuid4().hex, 'portfolio_id': portfolio_id, 'instrument_id': instrument_id,
                            'title': title, 'created_at': previous['created_at'] if previous else stamp, 'updated_at': stamp}
                    path = self.store.note_files.write(meta, body)
                    record = {k: meta[k] for k in ('id', 'portfolio_id', 'instrument_id')}
                    record.update(path=path, deleted=0)
                    db.execute('INSERT INTO investment_notes(id,portfolio_id,instrument_id,path,deleted) VALUES (:id,:portfolio_id,:instrument_id,:path,:deleted) '
                               'ON CONFLICT(id) DO UPDATE SET path=excluded.path', record)
                    self.store.audit(db, 'investment_note', meta['id'], before, record, '编辑投资笔记' if before else '发布投资笔记')
            except Exception:
                if path:
                    self.store.note_files.location(path).unlink(missing_ok=True)
                raise
            return self.get(portfolio_id, instrument_id, meta['id'])

    def set_deleted(self, portfolio_id, instrument_id, note_id, *, deleted, expected_revision):
        if expected_revision is None:
            raise DomainError('修改笔记必须提供 expected_revision')
        with self.store.lock:
            with self.store.transaction(expected_revision=expected_revision) as db:
                before = self.index(db, portfolio_id, instrument_id, note_id, deleted=not deleted)
                # Verify the referenced file before changing its visibility.
                self.store.note_files.read(before['path'])
                after = dict(before, deleted=int(deleted))
                db.execute('UPDATE investment_notes SET deleted=? WHERE id=?', (int(deleted), note_id))
                self.store.audit(db, 'investment_note', note_id, before, after, '删除投资笔记' if deleted else '撤销删除投资笔记')
            with self.store.connection() as db:
                return {'id': note_id, 'portfolio_id': portfolio_id, 'instrument_id': instrument_id,
                        'deleted': deleted, 'revision': self.revision(db)}
