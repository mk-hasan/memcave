"""PostgreSQL composition. Imports optional drivers only when opened."""

from memcave.adapters.sql.gateway import SqlGateway, prepare_database
from memcave.adapters.sqlite import _manager
from memcave.bootstrap import Settings, configured_embeddings
from memcave.services.memory_manager import MemoryManager


async def open_postgres(settings: Settings) -> MemoryManager:
    import asyncpg  # noqa: F401
    from sqlalchemy.ext.asyncio import create_async_engine

    embeddings = configured_embeddings(settings)
    engine = create_async_engine(settings.storage_dsn)
    await prepare_database(engine, embeddings.dimension)
    return _manager(settings, SqlGateway(engine), embeddings)
