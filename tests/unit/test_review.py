"""Candidate review creates a version or suppresses the hash."""

from tests.conftest import TENANT, USER, start

from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    EndSessionRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryCategory, MemoryKind, MemoryStatus
from memcave.domain.models import TenantScope
from memcave.services.memory_manager import MemoryManager


def _scope() -> TenantScope:
    return TenantScope(tenant_id=TENANT, user_id=USER)


async def test_accept_appends_an_active_version(memory_manager: MemoryManager) -> None:
    created = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="concise",
            content="Prefer concise answers.",
            status=MemoryStatus.CANDIDATE,
        )
    )
    accepted = await memory_manager.accept_review_candidate(TENANT, USER, created.memory.memory_id)
    versions = sorted(
        await memory_manager.deps.derived.list_versions(_scope(), created.memory.memory_id),
        key=lambda item: item.version_number,
    )
    assert accepted.version_number == 2
    assert accepted.status is MemoryStatus.ACTIVE
    assert [item.version_number for item in versions] == [1, 2]
    assert versions[0].content == "Prefer concise answers."
    assert versions[0].version_id == created.memory.version_id
    assert versions[1].status is MemoryStatus.ACTIVE


async def test_reject_suppresses_the_same_content(memory_manager: MemoryManager) -> None:
    created = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="concise",
            content="Prefer concise answers.",
            status=MemoryStatus.CANDIDATE,
        )
    )
    rejected = await memory_manager.reject_review_candidate(TENANT, USER, created.memory.memory_id)
    assert rejected.status is MemoryStatus.DELETED
    session_id = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "Prefer concise answers."},
        )
    )
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    result = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=False)
    )
    assert any(str(note).startswith("suppressed:") for note in result.run.report["notes"])
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="Prefer concise answers.",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert found.hits == []
