"""Transactional Alembic migrations; backups are owned by the caller."""
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL

from .common import DomainError

SCHEMA = 7
HEAD = '0006_storage'
ROOT = Path(__file__).resolve().parents[1]


def schema_version(path):
    path = Path(path)
    if not path.exists():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise DomainError('数据库不属于本应用，拒绝初始化')
            return None
        row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if not row or row[0] not in tuple(map(str, range(1, SCHEMA + 1))):
            raise DomainError('账本版本不受支持，未修改现有数据')
        return int(row[0])
    finally:
        db.close()


def migrate(path):
    path = Path(path).resolve()
    version = schema_version(path)
    if version == SCHEMA:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            try:
                row = db.execute('SELECT version_num FROM alembic_version').fetchone()
            except sqlite3.DatabaseError:
                row = None
            if not row or row[0] != HEAD:
                raise DomainError('数据库版本与迁移记录不一致，拒绝启动')
            indexes = {item[1]: item[2] for item in db.execute("PRAGMA index_list('fx')")}
            columns = [item[2] for item in db.execute("PRAGMA index_info('fx_daily_unique')")]
            if indexes.get('fx_daily_unique') != 1 or columns != ['currency', 'date']:
                raise DomainError('日频汇率唯一约束缺失，拒绝启动')
            from .family_domain import TABLES
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(TABLES).issubset(names):
                raise DomainError('家庭资料数据表缺失，拒绝启动')
        return False
    engine = create_engine(URL.create('sqlite+pysqlite', database=str(path)))

    @event.listens_for(engine, 'connect')
    def setup(dbapi, _):
        dbapi.isolation_level = None
        dbapi.execute('PRAGMA foreign_keys=ON')
        dbapi.execute('PRAGMA busy_timeout=15000')

    @event.listens_for(engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN IMMEDIATE')

    try:
        with engine.begin() as connection:
            cfg = Config()
            cfg.set_main_option('script_location', str(ROOT / 'migrations').replace('%', '%%'))
            cfg.attributes['connection'] = connection
            command.upgrade(cfg, HEAD)
            if connection.exec_driver_sql("SELECT value FROM meta WHERE key='schema'").scalar() != str(SCHEMA):
                raise DomainError('迁移记录与数据库版本不一致，事务已回滚')
            if connection.exec_driver_sql('PRAGMA quick_check').scalar() != 'ok':
                raise DomainError('迁移后完整性检查失败，事务已回滚')
    finally:
        engine.dispose()
    return True
