from alembic import context

connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('请通过管理工具执行迁移，以保证锁、完整备份和版本校验。')
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
