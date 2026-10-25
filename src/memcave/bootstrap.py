"""Composition root. Domain packages do not import this module."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from memcave.adapters.embeddings.factory import build_embedding_provider
from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.adapters.models.factory import build_model_provider
from memcave.domain.models import RetrievalConfig
from memcave.ports.protocols import EmbeddingProvider, ModelProvider
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
    embedding_model: str = ""
    model_provider: str = "fake"
    model_name: str = "fake-memcave"
    model: str = ""
    openai_model: str = "gpt-4o-mini"
    base_url: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    timeout: float = 60.0
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


@dataclass(frozen=True, slots=True)
class ProviderLink:
    """Connection values for an OpenAI-compatible server. The repr omits the key."""

    base_url: str
    api_key: str
    timeout: float

    def __repr__(self) -> str:
        return f"ProviderLink(base_url={self.base_url!r}, timeout={self.timeout})"


def provider_link(settings: Settings) -> ProviderLink:
    secret = settings.api_key
    key = secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)
    return ProviderLink(base_url=settings.base_url, api_key=key, timeout=settings.timeout)


def configured_embeddings(settings: Settings, dimension: int | None = None) -> EmbeddingProvider:
    link = provider_link(settings)
    return build_embedding_provider(
        settings.embedding_provider,
        settings.embedding_dimension if dimension is None else dimension,
        base_url=link.base_url,
        api_key=link.api_key,
        model=settings.embedding_model,
        timeout=link.timeout,
    )


def configured_model(settings: Settings) -> ModelProvider:
    link = provider_link(settings)
    return build_model_provider(
        settings.model_provider,
        model=settings.model,
        fallback_model=settings.model_name,
        base_url=link.base_url,
        api_key=link.api_key,
        timeout=link.timeout,
    )


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
        embedding_provider=configured_embeddings(current),
        model_provider=configured_model(current),
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
