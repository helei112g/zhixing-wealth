"""Private-ledger migration evidence: emit counts/hashes, never financial contents."""
import hashlib
import json
import sqlite3
from pathlib import Path

from .common import DomainError, json_dumps
from .ledger import Market
from .notes import NoteFiles, references


def digest(value):
    return hashlib.sha256(json_dumps(value).encode()).hexdigest()


def capture(directory):
    directory = Path(directory)
    db = sqlite3.connect((directory / 'portfolio.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts, hashes = {}, {}
        for name in tables:
            rows = [tuple(r) for r in db.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid')]
            counts[name] = len(rows)
            if name not in ('prices', 'jobs', 'meta', 'alembic_version'):
                hashes[name] = digest(rows)
        stable_meta = [tuple(r) for r in db.execute("SELECT key,value FROM meta WHERE key NOT IN ('schema','revision') ORDER BY key")]
        prices = [dict(json.loads(r['data']), version_id=r['id']) for r in db.execute('SELECT id,data FROM prices ORDER BY id')]
        fx = [dict(json.loads(r['data']), version_id=r['id']) for table in ('fx', 'fx_overrides') if table in tables
              for r in db.execute(f'SELECT id,data FROM {table} ORDER BY id')]
        market = Market({'prices': prices, 'fx': fx})
        effective = [(key, series.rows) for key, series in sorted(market.price_series.items())]
        content = sorted({json_dumps({k: v for k, v in r.items() if k not in ('fetched_at', 'version_id')}) for r in prices if not r.get('manual')})
        note_files = NoteFiles(directory)
        note_hashes = {path: hashlib.sha256(note_files.read_bytes(path)).hexdigest() for path in sorted(references(db))}
        # Only one aggregate hash leaves this function; note paths/contents stay private.
        return {'schema': int(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]),
                'counts': counts, 'business_table_hashes': hashes, 'stable_meta_sha256': digest(stable_meta),
                'effective_prices_sha256': digest(effective), 'distinct_price_versions_sha256': digest(content),
                'manual_prices_sha256': digest([r for r in prices if r.get('manual')]),
                'notes_sha256': digest(note_hashes), 'note_versions': len(note_hashes),
                'integrity': db.execute('PRAGMA quick_check').fetchone()[0],
                'database_bytes': (directory / 'portfolio.sqlite').stat().st_size}
    finally:
        db.close()


def verify(before, after):
    keys = ('business_table_hashes', 'stable_meta_sha256', 'effective_prices_sha256',
            'distinct_price_versions_sha256', 'manual_prices_sha256', 'notes_sha256', 'note_versions')
    changed = [key for key in keys if before[key] != after[key]]
    if changed or before['integrity'] != 'ok' or after['integrity'] != 'ok':
        raise DomainError('存储迁移等价核验失败，禁止开放写入；差异项：' + ','.join(changed))
    return {'equivalent': True, 'checked': list(keys), 'before': before, 'after': after}
