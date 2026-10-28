"""Create a new ledger or adopt the verified legacy schema 1/2 without rewriting events."""
import uuid
from alembic import op
from backend.schema import BASE_SCHEMA

revision = '0001_legacy'
down_revision = None
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for statement in BASE_SCHEMA.split(';'):
        if statement.strip():
            db.exec_driver_sql(statement)
    for key, value in (('schema', '2'), ('revision', '0'), ('app_id', str(uuid.uuid4()))):
        db.exec_driver_sql('INSERT OR IGNORE INTO meta VALUES (?,?)', (key, value))
    db.exec_driver_sql("UPDATE meta SET value='2' WHERE key='schema'")


def downgrade():
    raise RuntimeError('不能删除账本；请使用配套完整备份恢复到独立目录。')
