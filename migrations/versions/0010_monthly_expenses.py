"""Add empty monthly expense tables; preserve all prior rows and revisions."""
from alembic import op
revision = '0010_monthly_expenses'
down_revision = '0009_index_research'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for table in ('family_expense_plans', 'family_expense_months'):
        db.exec_driver_sql(f'CREATE TABLE {table}(id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data)))')
    db.exec_driver_sql("UPDATE meta SET value='11' WHERE key='schema'")


def downgrade():
    raise RuntimeError('收支计划及月度历史须保留，不支持直接降级。')
