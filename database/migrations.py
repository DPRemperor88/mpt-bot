"""Последовательные миграции SQLite. Старые установки принимаются как версия 0."""
from sqlalchemy import text

from .models import Base, Delivery


async def migrate(conn) -> None:
    await conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"))
    version = (await conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations"))).scalar_one()
    if version > 2:
        raise RuntimeError("База новее этой версии бота; откат кода запрещён")
    if version < 1:
        tables = [table for table in Base.metadata.sorted_tables if table is not Delivery.__table__]
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        await conn.execute(text("INSERT INTO schema_migrations(version) VALUES (1)"))
    if version < 2:
        await conn.run_sync(lambda sync: Delivery.__table__.create(sync, checkfirst=True))
        await conn.execute(text("INSERT INTO schema_migrations(version) VALUES (2)"))
