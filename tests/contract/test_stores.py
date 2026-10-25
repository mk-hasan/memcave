"""Store contract shared by the in-memory, SQLite, and PostgreSQL adapters."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from tests.conftest import OTHER_USER, TENANT, USER, manager_from_parts, start

from memcave.domain.api import (
    AppendEventRequest,
    DeleteMemoryRequest,
    EndSessionRequest,
    EraseUserDataRequest,
    FetchMemoryRequest,
    ListEventsRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryKind, MemoryStatus
from memcave.domain.errors import NotFoundError
from memcave.domain.models import TenantScope
from memcave.services.memory_manager import MemoryManager


@pytest.fixture(
    params=[
        "memory",
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ]
)
async def contract_manager(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[MemoryManager]:
    if request.param == "memory":
        from memcave.adapters.memory.stores import (
            InMemoryData,
            InMemoryDerivedStore,
            InMemoryEpisodicStore,
            InMemoryEventSink,
            InMemoryVectorStore,
        )

        data = InMemoryData()
        yield manager_from_parts(
            InMemoryEpisodicStore(data),
            InMemoryDerivedStore(data),
            InMemoryVectorStore(data),
            InMemoryEventSink(data),
        )
        return
    from sqlalchemy.ext.asyncio import create_async_engine

    from memcave.adapters.sql.derived import SqlDerivedStore, SqlEventSink, SqlVectorStore
    from memcave.adapters.sql.gateway import SqlEpisodicStore, SqlGateway, prepare_database
    from memcave.db.tables import Base

    if request.param == "postgres":
        dsn = os.environ.get("MEMCAVE_POSTGRES_DSN")
        if not dsn:
            pytest.skip("MEMCAVE_POSTGRES_DSN is not set")
        engine = create_async_engine(dsn)
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/contract.db")
    await prepare_database(engine, 64)
    if request.param == "postgres":
        async with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                await connection.execute(table.delete())
    gateway = SqlGateway(engine)
    try:
        yield manager_from_parts(
            SqlEpisodicStore(gateway),
            SqlDerivedStore(gateway),
            SqlVectorStore(gateway),
            SqlEventSink(gateway),
        )
    finally:
        await engine.dispose()


def _scope() -> TenantScope:
    return TenantScope(tenant_id=TENANT, user_id=USER)


async def _remember(manager: MemoryManager, text: str, *, user: UUID = USER) -> UUID:
    session_id = await start(manager, user=user)
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=user,
            session_id=session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={"instruction_type": "remember", "raw_text": text},
        )
    )
    return session_id


async def test_explicit_memory_round_trip(contract_manager: MemoryManager) -> None:
    await _remember(contract_manager, "remember that I prefer concise answers")
    found = await contract_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="concise answers")
    )
    assert any("concise" in hit.content for hit in found.hits)
    assert "untrusted" in found.context_block.casefold()


async def test_only_the_four_patterns_write_immediately(
    contract_manager: MemoryManager,
) -> None:
    session_id = await start(contract_manager)
    for raw in (
        "please store that I like tea",
        "remember the meeting notes",
        "I said instead of yesterday",
    ):
        await contract_manager.append_event(
            AppendEventRequest(
                tenant_id=TENANT,
                user_id=USER,
                session_id=session_id,
                event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
                payload={"instruction_type": "remember", "raw_text": raw},
            )
        )
    await contract_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "I prefer tea"},
        )
    )
    found = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="tea meeting",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert found.hits == []
    await _remember(contract_manager, "remember that I prefer concise answers")
    await _remember(contract_manager, "forget concise answers")
    await contract_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={
                "instruction_type": "correct",
                "raw_text": "use the orders table instead of the purchases table",
            },
        )
    )
    await contract_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={
                "instruction_type": "temporal_change",
                "raw_text": "as of 2026-06-01 the warehouse is North Dock",
            },
        )
    )
    found = await contract_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="orders warehouse")
    )
    contents = " ".join(hit.content for hit in found.hits)
    assert "orders table" in contents
    assert "North Dock" in contents


async def test_soft_delete_appends_a_version_and_keeps_the_old_one(
    contract_manager: MemoryManager,
) -> None:
    await _remember(contract_manager, "remember that I prefer concise answers")
    found = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    memory_id = found.hits[0].source_id
    original = await contract_manager.fetch_memory(
        FetchMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=memory_id, version_number=1)
    )
    await contract_manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=memory_id)
    )
    versions = sorted(
        await contract_manager.deps.derived.list_versions(_scope(), memory_id),
        key=lambda item: item.version_number,
    )
    assert [item.version_number for item in versions] == [1, 2]
    assert versions[0].status is MemoryStatus.ACTIVE
    assert versions[0].content == original.memory.content
    assert versions[0].content_hash == original.memory.content_hash
    assert versions[1].status is MemoryStatus.DELETED
    assert versions[1].content_hash == original.memory.content_hash
    hidden = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert hidden.hits == []


async def test_explicit_remember_clears_suppression_and_erase_removes_hashes(
    contract_manager: MemoryManager,
) -> None:
    text = "remember that I prefer concise answers"
    await _remember(contract_manager, text)
    found = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    memory_id = found.hits[0].source_id
    await contract_manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=memory_id)
    )
    other = await start(contract_manager, user=OTHER_USER)
    await contract_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=OTHER_USER,
            session_id=other,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "remember that I prefer concise answers"},
        )
    )
    await contract_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=OTHER_USER, session_id=other)
    )
    await contract_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=OTHER_USER, use_model=False)
    )
    other_found = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=OTHER_USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert other_found.hits
    await _remember(contract_manager, text)
    restored = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert restored.hits
    cleared = sorted(
        await contract_manager.deps.derived.list_versions(_scope(), memory_id),
        key=lambda item: item.version_number,
    )
    assert cleared[1].content_hash == ""
    erased = await contract_manager.erase_user_data(
        EraseUserDataRequest(tenant_id=TENANT, user_id=USER)
    )
    assert erased.counts.events >= 1
    assert erased.counts.versions >= 2
    assert erased.counts.evidence >= 1
    assert erased.counts.embeddings >= 1
    assert erased.counts.memories >= 1
    events = await contract_manager.list_events(ListEventsRequest(tenant_id=TENANT, user_id=USER))
    assert events.events == []
    assert await contract_manager.deps.derived.list_memory_ids(_scope(), USER) == []
    with pytest.raises(NotFoundError):
        await contract_manager.fetch_memory(
            FetchMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=memory_id)
        )
    await _remember(contract_manager, text)
    again = await contract_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert again.hits
