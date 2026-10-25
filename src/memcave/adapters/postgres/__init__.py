"""PostgreSQL composition. Imports optional drivers only when opened."""

from memcave.adapters.sql.gateway import SqlGateway, prepare_database
from memcave.adapters.sqlite import _manager
from memcave.bootstrap import Settings


async def open_postgres(settings: Settings) -> object:
    import asyncpg  # noqa: F401
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(settings.storage_dsn)
    await prepare_database(engine, settings.embedding_dimension)
    return _manager(settings, SqlGateway(engine))
