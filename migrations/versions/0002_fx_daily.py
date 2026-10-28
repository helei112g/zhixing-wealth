"""Keep one automatic rate per currency/date and separate manual valuation overrides."""
import json
from alembic import op

revision = '0002_fx_daily'
down_revision = '0001_legacy'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    db.exec_driver_sql('''CREATE TABLE fx_overrides(id INTEGER PRIMARY KEY AUTOINCREMENT,
        currency TEXT NOT NULL,date TEXT NOT NULL,manual INTEGER NOT NULL,end_date TEXT,data TEXT NOT NULL)''')
    db.exec_driver_sql('INSERT INTO fx_overrides SELECT * FROM fx WHERE manual=1')
    db.exec_driver_sql('DELETE FROM fx WHERE manual=1')
    # Same precedence as Series: latest non-cancelled row; no averaging or changed rates.
    db.exec_driver_sql('''DELETE FROM fx WHERE id NOT IN (
        SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY currency,date
        ORDER BY COALESCE(json_extract(data,'$.cancelled'),0),id DESC) AS rank FROM fx) WHERE rank=1)''')
    db.exec_driver_sql('''DELETE FROM fx_overrides WHERE json_extract(data,'$.mode')='daily'
        AND COALESCE(json_extract(data,'$.cancelled'),0)=0 AND id NOT IN
        (SELECT MAX(id) FROM fx_overrides WHERE json_extract(data,'$.mode')='daily'
        AND COALESCE(json_extract(data,'$.cancelled'),0)=0 GROUP BY currency,date)''')
    for row_id, payload in db.exec_driver_sql('SELECT id,data FROM fx').fetchall():
        item = json.loads(payload)
        item.pop('providers', None)
        db.exec_driver_sql('UPDATE fx SET data=? WHERE id=?',
                           (json.dumps(item, ensure_ascii=False, separators=(',', ':')), row_id))
    db.exec_driver_sql('CREATE UNIQUE INDEX fx_daily_unique ON fx(currency,date)')
    db.exec_driver_sql('CREATE INDEX fx_override_scope ON fx_overrides(currency,date)')
    db.exec_driver_sql('''CREATE UNIQUE INDEX fx_manual_daily_unique ON fx_overrides(currency,date)
        WHERE json_extract(data,'$.mode')='daily' AND COALESCE(json_extract(data,'$.cancelled'),0)=0''')
    db.exec_driver_sql("UPDATE meta SET value='3' WHERE key='schema'")
    db.exec_driver_sql("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def downgrade():
    raise RuntimeError('汇率去重不支持自动降级；旧版恢复须使用升级前完整备份。')
