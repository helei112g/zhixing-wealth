"""Independent family records; existing investment facts are never rewritten."""
from alembic import op

revision = '0003_family'
down_revision = '0002_fx_daily'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    for table in ('family_items', 'family_values', 'family_links', 'family_schedules',
                  'family_settings', 'family_reconciliations', 'family_closures', 'family_notification_state'):
        db.exec_driver_sql(f'CREATE TABLE {table}(id TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(json_valid(data)))')
    db.exec_driver_sql("CREATE INDEX family_value_scope ON family_values(json_extract(data,'$.item_id'),json_extract(data,'$.effective_on'))")
    db.exec_driver_sql("CREATE INDEX family_schedule_date ON family_schedules(json_extract(data,'$.due_date'))")
    db.exec_driver_sql("UPDATE meta SET value='4' WHERE key='schema'")
    db.exec_driver_sql("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def downgrade():
    raise RuntimeError('家庭资料不支持删除表降级；使用经过核对的升级前完整恢复点。')
