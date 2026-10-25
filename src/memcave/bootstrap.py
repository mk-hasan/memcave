"""Composition root. Domain packages do not import this module."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from memcave.adapters.embeddings.factory import build_embedding_provider
from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.adapters.models.fake import FakeModelProvider
from memcave.domain.models import RetrievalConfig
from memcave.services.memory_manager import MemoryManager
from memcave.services.runtime import (
    DefaultWritePolicy,
    KeepForeverRetention,
    NoOpTracer,
    PatternRedaction,
    SystemClock,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMCAVE_", extra="ignore")

    storage_backend: str = "sqlite"
    storage_dsn: str = "sqlite+aiosqlite:///./.memcave/memcave.db"
    embedding_provider: str = "fake"
    embedding_dimension: int = 64
    model_provider: str = "fake"
    model_name: str = "fake-memcave"
    openai_model: str = "gpt-4o-mini"
    temperature: float = 0.0
    seed: int = 0
    top_k: int = 8
    token_budget: int = 2000
    recency_half_life_days: float = 30.0
    redact_queries: bool = True
    review_confidence_threshold: float = 0.60
    auto_resolve_confidence_threshold: float = 0.85
    duplicate_cosine_threshold: float = 0.92
    config_path: Path = Field(default=Path(".memcave/config.toml"))


def retrieval_config(settings: Settings) -> RetrievalConfig:
    return RetrievalConfig(
        top_k=settings.top_k,
        token_budget=settings.token_budget,
        recency_half_life_days=settings.recency_half_life_days,
        redact_queries=settings.redact_queries,
        review_confidence_threshold=settings.review_confidence_threshold,
        auto_resolve_confidence_threshold=settings.auto_resolve_confidence_threshold,
        duplicate_cosine_threshold=settings.duplicate_cosine_threshold,
    )


def in_memory_manager(settings: Settings | None = None) -> MemoryManager:
    current = settings or Settings()
    data = InMemoryData()
    return MemoryManager(
        episodic_store=InMemoryEpisodicStore(data),
        derived_store=InMemoryDerivedStore(data),
        vector_store=InMemoryVectorStore(data),
        embedding_provider=build_embedding_provider(
            current.embedding_provider, current.embedding_dimension
        ),
        model_provider=FakeModelProvider(model_name=current.model_name),
        clock=SystemClock(),
        event_sink=InMemoryEventSink(data),
        redaction_policy=PatternRedaction(),
        retention_policy=KeepForeverRetention(),
        write_policy=DefaultWritePolicy(),
        tracer=NoOpTracer(),
        retrieval_config=retrieval_config(current),
    )


async def build_manager(settings: Settings | None = None) -> MemoryManager:
    current = settings or Settings()
    if current.storage_backend == "memory":
        return in_memory_manager(current)
    if current.storage_backend == "sqlite":
        from memcave.adapters.sqlite import open_sqlite

        return await open_sqlite(current)
    if current.storage_backend == "postgres":
        from memcave.adapters.postgres import open_postgres

        return await open_postgres(current)
    raise ValueError(f"unknown storage backend {current.storage_backend}")
