"""Add empty market research tables; retain existing ledger and research revisions."""
from alembic import op

revision = '0011_market_waterlevel'
down_revision = '0010_monthly_expenses'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    db.exec_driver_sql('''CREATE TABLE market_snapshots(
        id TEXT PRIMARY KEY, group_id TEXT NOT NULL, created_at TEXT NOT NULL,
        sha256 TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)))''')
    db.exec_driver_sql('CREATE INDEX market_snapshot_group ON market_snapshots(group_id, created_at)')
    db.exec_driver_sql('''CREATE TABLE market_heads(
        group_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES market_snapshots(id))''')
    db.exec_driver_sql('''CREATE TABLE market_reports(
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, sha256 TEXT NOT NULL,
        data TEXT NOT NULL CHECK(json_valid(data)))''')
    db.exec_driver_sql('''CREATE TABLE market_settings(
        id TEXT PRIMARY KEY CHECK(id='settings'), data TEXT NOT NULL CHECK(json_valid(data)))''')
    db.exec_driver_sql("UPDATE meta SET value='12' WHERE key='schema'")


def downgrade():
    raise RuntimeError('市场来源与研究快照须保留，不支持直接降级。')
