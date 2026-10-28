"""Remove identical automatic quote copies without removing distinct source versions."""
import json
from alembic import op

revision = '0006_storage'
down_revision = '0005_family_income'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    seen = set()
    remove = []
    for rid, iid, date, kind, payload in db.exec_driver_sql(
            'SELECT id,instrument_id,date,kind,data FROM prices WHERE manual=0 ORDER BY id DESC'):
        data = json.loads(payload)
        data.pop('fetched_at', None)
        key = (iid, date, kind, json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        if key in seen:
            remove.append((rid,))
        else:
            seen.add(key)
    if remove:
        db.exec_driver_sql('DELETE FROM prices WHERE id=?', remove)
    # The unique index provides exactly the same currency/date lookup.
    db.exec_driver_sql('DROP INDEX IF EXISTS fx_scope')
    db.exec_driver_sql("UPDATE meta SET value='7' WHERE key='schema'")
    db.exec_driver_sql("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def downgrade():
    raise RuntimeError('行情去重不支持直接降级；旧版恢复须在独立目录使用升级前完整备份。')
