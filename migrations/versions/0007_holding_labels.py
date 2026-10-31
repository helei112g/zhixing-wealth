"""Keep optional holding labels separate from plans and historical events."""
from alembic import op

revision = '0007_holding_labels'
down_revision = '0006_storage'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    db.exec_driver_sql('''CREATE TABLE holding_labels(
        portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
        instrument_id TEXT NOT NULL REFERENCES instruments(id),
        data TEXT NOT NULL CHECK(json_valid(data)),
        PRIMARY KEY(portfolio_id,instrument_id))''')
    db.exec_driver_sql("UPDATE meta SET value='8' WHERE key='schema'")


def downgrade():
    raise RuntimeError('旧版本不支持持仓标签；回退须保留标签数据并通过独立恢复流程。')
