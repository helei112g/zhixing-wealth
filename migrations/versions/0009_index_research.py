"""Add public research snapshots and durable update policies without business rewrites."""
from alembic import op
revision = '0009_index_research'
down_revision = '0008_retire_legacy_labels'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for sql in (
        'CREATE TABLE research_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
        'CREATE TABLE research_price_chunks(sha256 TEXT PRIMARY KEY,data TEXT NOT NULL CHECK(json_valid(data)))',
        'CREATE TABLE research_indices(id TEXT PRIMARY KEY,data TEXT NOT NULL CHECK(json_valid(data)))',
        '''CREATE TABLE research_snapshots(id TEXT PRIMARY KEY,index_id TEXT NOT NULL REFERENCES research_indices(id),
           module TEXT NOT NULL,created_at TEXT NOT NULL,sha256 TEXT NOT NULL,data TEXT NOT NULL CHECK(json_valid(data)))''',
        'CREATE INDEX research_snapshot_scope ON research_snapshots(index_id,module,created_at)',
        '''CREATE TABLE research_heads(index_id TEXT NOT NULL REFERENCES research_indices(id),module TEXT NOT NULL,
           snapshot_id TEXT NOT NULL REFERENCES research_snapshots(id),PRIMARY KEY(index_id,module))''',
        'CREATE TABLE data_update_policies(id TEXT PRIMARY KEY,data TEXT NOT NULL CHECK(json_valid(data)))',
        'CREATE TABLE data_update_receipts(id TEXT PRIMARY KEY,digest TEXT NOT NULL,job_id TEXT NOT NULL,result TEXT NOT NULL CHECK(json_valid(result)))',
        'CREATE TABLE data_update_tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL,data TEXT NOT NULL CHECK(json_valid(data)))',
    ): db.exec_driver_sql(sql)
    db.exec_driver_sql("UPDATE meta SET value='10' WHERE key='schema'")


def downgrade():
    raise RuntimeError('研究快照须保留；回退使用兼容版本或独立恢复流程。')
