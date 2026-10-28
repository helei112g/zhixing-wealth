"""Separate borrowing dates from balance dates; support receivable records.

Schema 5 prevents older family readers from silently omitting receivables.
Existing balances and investment facts are unchanged; unknown dates stay null.
"""
import json
from alembic import op

revision = '0004_family_simple'
down_revision = '0003_family'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for identity, payload in db.exec_driver_sql('SELECT id, data FROM family_items').fetchall():
        item = json.loads(payload)
        for version in item['versions']:
            version.setdefault('started_on', None)
            version.setdefault('due_on', None)
        db.exec_driver_sql('UPDATE family_items SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False, separators=(',', ':')), identity))
    db.exec_driver_sql("UPDATE meta SET value='5' WHERE key='schema'")
    db.exec_driver_sql("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def downgrade():
    raise RuntimeError('旧版本无法完整读取应收款，不能仅回退代码；请先准备数据兼容方案。')
