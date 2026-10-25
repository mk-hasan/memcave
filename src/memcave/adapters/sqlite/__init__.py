"""SQLite composition."""

from sqlalchemy.ext.asyncio import create_async_engine

from memcave.adapters.embeddings.fake import FakeEmbeddingProvider
from memcave.adapters.models.fake import FakeModelProvider
from memcave.adapters.sql.derived import SqlDerivedStore, SqlEventSink, SqlVectorStore
from memcave.adapters.sql.gateway import SqlEpisodicStore, SqlGateway, prepare_database
from memcave.bootstrap import Settings, retrieval_config
from memcave.services.memory_manager import MemoryManager
from memcave.services.runtime import (
    DefaultWritePolicy,
    KeepForeverRetention,
    NoOpTracer,
    PatternRedaction,
    SystemClock,
)


async def open_sqlite(settings: Settings) -> MemoryManager:
    engine = create_async_engine(settings.storage_dsn)
    await prepare_database(engine, settings.embedding_dimension)
    gateway = SqlGateway(engine)
    return _manager(settings, gateway)


def _manager(settings: Settings, gateway: SqlGateway) -> MemoryManager:
    provider: object
    if settings.model_provider == "openai":
        from memcave.adapters.models.openai import OpenAIModelProvider

        provider = OpenAIModelProvider(settings.openai_model)
    else:
        provider = FakeModelProvider(model_name=settings.model_name)
    return MemoryManager(
        episodic_store=SqlEpisodicStore(gateway),
        derived_store=SqlDerivedStore(gateway),
        vector_store=SqlVectorStore(gateway),
        embedding_provider=FakeEmbeddingProvider(settings.embedding_dimension),
        model_provider=provider,  # type: ignore[arg-type]
        clock=SystemClock(),
        event_sink=SqlEventSink(gateway),
        redaction_policy=PatternRedaction(),
        retention_policy=KeepForeverRetention(),
        write_policy=DefaultWritePolicy(),
        tracer=NoOpTracer(),
        retrieval_config=retrieval_config(settings),
    )
