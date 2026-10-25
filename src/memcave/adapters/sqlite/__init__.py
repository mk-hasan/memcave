"""SQLite composition."""

from sqlalchemy.ext.asyncio import create_async_engine

from memcave.adapters.sql.derived import SqlDerivedStore, SqlEventSink, SqlVectorStore
from memcave.adapters.sql.gateway import SqlEpisodicStore, SqlGateway, prepare_database
from memcave.bootstrap import Settings, configured_embeddings, configured_model, retrieval_config
from memcave.ports.protocols import EmbeddingProvider
from memcave.services.memory_manager import MemoryManager
from memcave.services.runtime import (
    DefaultWritePolicy,
    KeepForeverRetention,
    NoOpTracer,
    PatternRedaction,
    SystemClock,
)


async def open_sqlite(settings: Settings) -> MemoryManager:
    embeddings = configured_embeddings(settings)
    engine = create_async_engine(settings.storage_dsn)
    await prepare_database(engine, embeddings.dimension)
    gateway = SqlGateway(engine)
    return _manager(settings, gateway, embeddings)


def _manager(
    settings: Settings, gateway: SqlGateway, embeddings: EmbeddingProvider
) -> MemoryManager:
    return MemoryManager(
        episodic_store=SqlEpisodicStore(gateway),
        derived_store=SqlDerivedStore(gateway),
        vector_store=SqlVectorStore(gateway),
        embedding_provider=embeddings,
        model_provider=configured_model(settings),
        clock=SystemClock(),
        event_sink=SqlEventSink(gateway),
        redaction_policy=PatternRedaction(),
        retention_policy=KeepForeverRetention(),
        write_policy=DefaultWritePolicy(),
        tracer=NoOpTracer(),
        retrieval_config=retrieval_config(settings),
    )
