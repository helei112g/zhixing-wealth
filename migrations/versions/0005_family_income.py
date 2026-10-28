"""Income is a flow; recurring payment rules do not change asset balances."""
from alembic import op
revision = '0005_family_income'
down_revision = '0004_family_simple'
branch_labels = depends_on = None

def upgrade():
    db = op.get_bind()
    db.exec_driver_sql('CREATE TABLE family_income(id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data)))')
    db.exec_driver_sql("UPDATE meta SET value='6' WHERE key='schema'")
    db.exec_driver_sql("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

def downgrade():
    raise RuntimeError('旧代码无法读取收入与自动付款设置，不支持直接降级。')
