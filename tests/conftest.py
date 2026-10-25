"""Shared test helpers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from memcave.adapters.embeddings.fake import FakeEmbeddingProvider
from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.adapters.models.fake import FakeModelProvider
from memcave.adapters.sql.derived import SqlDerivedStore, SqlEventSink, SqlVectorStore
from memcave.adapters.sql.gateway import SqlEpisodicStore, SqlGateway, prepare_database
from memcave.domain.api import StartSessionRequest
from memcave.services.memory_manager import MemoryManager
from memcave.services.runtime import (
    DefaultWritePolicy,
    FixedClock,
    KeepForeverRetention,
    NoOpTracer,
    PatternRedaction,
)

TENANT = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_USER = UUID("00000000-0000-4000-8000-0000000000b1")
OTHER_TENANT = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 1, 15, tzinfo=UTC)


def manager_from_parts(
    episodic: object,
    derived: object,
    vectors: object,
    sink: object,
) -> MemoryManager:
    return MemoryManager(
        episodic_store=episodic,  # type: ignore[arg-type]
        derived_store=derived,  # type: ignore[arg-type]
        vector_store=vectors,  # type: ignore[arg-type]
        embedding_provider=FakeEmbeddingProvider(),
        model_provider=FakeModelProvider(),
        clock=FixedClock(NOW),
        event_sink=sink,  # type: ignore[arg-type]
        redaction_policy=PatternRedaction(),
        retention_policy=KeepForeverRetention(),
        write_policy=DefaultWritePolicy(),
        tracer=NoOpTracer(),
    )


@pytest.fixture
def memory_manager() -> MemoryManager:
    data = InMemoryData()
    return manager_from_parts(
        InMemoryEpisodicStore(data),
        InMemoryDerivedStore(data),
        InMemoryVectorStore(data),
        InMemoryEventSink(data),
    )


@pytest.fixture
async def sqlite_manager(tmp_path: object) -> MemoryManager:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/memcave.db")
    await prepare_database(engine, 64)
    gateway = SqlGateway(engine)
    return manager_from_parts(
        SqlEpisodicStore(gateway),
        SqlDerivedStore(gateway),
        SqlVectorStore(gateway),
        SqlEventSink(gateway),
    )


async def start(manager: MemoryManager, tenant: UUID = TENANT, user: UUID = USER) -> UUID:
    response = await manager.start_session(
        StartSessionRequest(tenant_id=tenant, user_id=user, title="session")
    )
    return response.session.session_id


def new_id() -> UUID:
    return uuid4()
