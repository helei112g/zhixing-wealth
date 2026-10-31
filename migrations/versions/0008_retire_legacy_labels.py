"""Explicitly retire legacy style/theme labels and the mixed region value."""
from alembic import op
from backend.legacy_labels import TABLES, clean_data

revision = '0008_retire_legacy_labels'
down_revision = '0007_holding_labels'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    # Audit history and protected backups remain original evidence. This includes
    # soft-deleted business rows so undelete cannot resurrect retired fields.
    for table in sorted(TABLES):
        for rowid, raw in db.exec_driver_sql(f'SELECT rowid,data FROM {table}').fetchall():
            cleaned = clean_data(raw)
            if cleaned != raw:
                db.exec_driver_sql(f'UPDATE {table} SET data=? WHERE rowid=?', (cleaned, rowid))
    db.exec_driver_sql("UPDATE meta SET value='9' WHERE key='schema'")


def downgrade():
    raise RuntimeError('旧标签已按用户确认清理；恢复须使用升级前完整备份。')
