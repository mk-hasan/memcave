"""Retrieval filters, subject keys, workflow injection, and index evidence."""

from collections.abc import Sequence
from datetime import timedelta

from tests.conftest import NOW, TENANT, USER, manager_from_parts, start

from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    EvidenceInput,
    FetchMemoryRequest,
    GetMemoryIndexRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryCategory, MemoryKind, MemorySource, MemoryStatus
from memcave.domain.models import TenantScope
from memcave.services.memory_manager import MemoryManager


class ConstantEmbedding:
    dimension = 64

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, *([0.0] * 63)] for _ in texts]


def _manager(embedding: object | None = None) -> MemoryManager:
    data = InMemoryData()
    manager = manager_from_parts(
        InMemoryEpisodicStore(data),
        InMemoryDerivedStore(data),
        InMemoryVectorStore(data),
        InMemoryEventSink(data),
    )
    if embedding is not None:
        manager.deps.embeddings = embedding  # type: ignore[assignment]
    return manager


async def _end_and_message(
    manager: MemoryManager, content: str, subject: str | None = None
) -> None:
    session_id = await start(manager)
    payload: dict[str, str] = {"content": content}
    if subject is not None:
        payload["subject_key"] = subject
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload=payload,
        )
    )
    from memcave.domain.api import EndSessionRequest

    await manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )


async def _stored(manager: MemoryManager) -> list[str]:
    memories = await manager.deps.derived.list_user_memories(
        TenantScope(tenant_id=TENANT, user_id=USER), USER
    )
    return [item.content for item in memories]


async def test_inactive_memories_are_absent_from_index_and_search(
    memory_manager: MemoryManager,
) -> None:
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="expired note",
            content="EXPIRED_SECRET_FACT",
            valid_until=NOW - timedelta(days=1),
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="future note",
            content="NOT_YET_VALID_FACT",
            valid_from=NOW + timedelta(days=2),
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="archived note",
            content="ARCHIVED_SECRET_FACT",
            status=MemoryStatus.ARCHIVED,
        )
    )
    deleted = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="deleted note",
            content="DELETED_SECRET_FACT",
        )
    )
    from memcave.domain.api import DeleteMemoryRequest

    await memory_manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=deleted.memory.memory_id)
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="kept note",
            content="still valid",
        )
    )
    index = await memory_manager.get_memory_index(
        GetMemoryIndexRequest(tenant_id=TENANT, user_id=USER)
    )
    assert "kept note" in index.markdown
    for needle in (
        "EXPIRED_SECRET_FACT",
        "NOT_YET_VALID_FACT",
        "ARCHIVED_SECRET_FACT",
        "DELETED_SECRET_FACT",
    ):
        assert needle not in index.markdown
        found = await memory_manager.search_memory(
            SearchMemoryRequest(
                tenant_id=TENANT,
                user_id=USER,
                query_text=needle,
                memory_kinds=MemoryKind.DERIVED,
            )
        )
        assert all(needle not in hit.content and needle not in hit.title for hit in found.hits)
        assert needle not in found.context_block


async def test_correction_outranks_an_older_episode(memory_manager: MemoryManager) -> None:
    session_id = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "shipping purchases table revenue notes"},
            occurred_at=NOW - timedelta(days=10),
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="handbook",
            content="shipping purchases table revenue notes",
            metadata={"subject_key": "handbook"},
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.CORRECTION,
            title="replacement",
            content="Use the orders table.",
            metadata={"subject_key": "shipping"},
            source=MemorySource.EXPLICIT_INSTRUCTION,
            valid_from=NOW,
            importance=0.1,
        )
    )
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="shipping purchases table revenue notes",
        )
    )
    assert found.hits[0].content == "Use the orders table."
    assert found.hits[0].score_components["subject_boost"] >= 2
    episode = next(hit for hit in found.hits if hit.kind is MemoryKind.EPISODIC)
    assert found.hits[0].score > episode.score


async def test_later_valid_from_outranks_the_same_subject(memory_manager: MemoryManager) -> None:
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="old widgets",
            content="widgets manual exhaustive catalog",
            metadata={"subject_key": "widgets"},
            valid_from=NOW - timedelta(days=10),
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="new widgets",
            content="widgets",
            metadata={"subject_key": "widgets"},
            valid_from=NOW,
        )
    )
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="widgets manual exhaustive catalog",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert found.hits[0].title == "new widgets"
    assert found.hits[0].score_components["subject_boost"] >= 2
    older = next(hit for hit in found.hits if hit.title == "old widgets")
    assert "subject_boost" not in older.score_components


async def test_cosine_merge_requires_matching_subject_keys() -> None:
    manager = _manager(ConstantEmbedding())
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="concise",
            content="Prefer concise answers.",
            metadata={"subject_key": "answer-style"},
        )
    )
    await _end_and_message(manager, "Prefer concise replies.", subject="voice")
    separated = await manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    assert any(note.startswith("create:") for note in separated.run.report["notes"])
    assert not any(note.startswith("merge:") for note in separated.run.report["notes"])
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="alpha",
            content="Prefer alpha notebooks.",
            metadata={"subject_key": ""},
        )
    )
    await _end_and_message(manager, "Prefer beta notebooks.", subject="")
    empty = await manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    assert any(note.startswith("create:") for note in empty.run.report["notes"])
    assert "Prefer beta notebooks." in await _stored(manager)
    await _end_and_message(manager, "Prefer short replies.", subject="answer-style")
    merged = await manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    assert any(note.startswith("merge:") for note in merged.run.report["notes"])
    stored = await _stored(manager)
    assert "Prefer short replies." not in stored
    assert "Prefer concise answers." in stored
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="violet",
            content="Prefer violet notebooks.",
            metadata={"subject_key": "left"},
        )
    )
    await _end_and_message(manager, "Prefer violet notebooks.", subject="right")
    hashed = await manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    assert any(note.startswith("merge:") for note in hashed.run.report["notes"])
    assert (await _stored(manager)).count("Prefer violet notebooks.") == 1


async def test_workflow_steps_follow_the_question_subject(memory_manager: MemoryManager) -> None:
    revenue_steps = [
        {
            "name": "aggregate",
            "tool": "run_query",
            "argument_pattern": "sum(amount)",
            "success_check": "rowcount > 0",
        }
    ]
    tax_steps = [
        {
            "name": "file",
            "tool": "submit",
            "argument_pattern": "form-1040",
            "success_check": "accepted",
        }
    ]
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.WORKFLOW,
            title="Quarterly revenue",
            content="Compute quarterly revenue",
            structured_content={"subject_key": "revenue", "steps": revenue_steps},
        )
    )
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.WORKFLOW,
            title="Tax filing",
            content="File the annual tax form",
            structured_content={"subject_key": "tax", "steps": tax_steps},
        )
    )
    index = await memory_manager.get_memory_index(
        GetMemoryIndexRequest(tenant_id=TENANT, user_id=USER)
    )
    assert "Tax filing" in index.markdown
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="how is revenue calculated",
            top_k=1,
        )
    )
    assert "<<<MEMCAVE_MEMORY>>>" in found.context_block
    assert "workflow-step name=aggregate tool=run_query" in found.context_block
    assert "tool=submit" not in found.context_block
    assert "form-1040" not in found.context_block


async def test_index_line_has_evidence_id_without_the_episode(
    memory_manager: MemoryManager,
) -> None:
    session_id = await start(memory_manager)
    appended = await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "EPISODE_BODY_SHOULD_NOT_APPEAR_IN_INDEX"},
        )
    )
    added = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="kept",
            content="kept fact",
            evidence=[
                EvidenceInput(
                    session_id=session_id,
                    event_id=appended.event.event_id,
                    quote="EPISODE_BODY_SHOULD_NOT_APPEAR_IN_INDEX",
                )
            ],
        )
    )
    index = await memory_manager.get_memory_index(
        GetMemoryIndexRequest(tenant_id=TENANT, user_id=USER)
    )
    line = next(
        item for item in index.markdown.splitlines() if str(appended.event.event_id) in item
    )
    assert line.strip() == f"evidence {appended.event.event_id}"
    assert "EPISODE_BODY_SHOULD_NOT_APPEAR_IN_INDEX" not in line
    assert "EPISODE_BODY_SHOULD_NOT_APPEAR_IN_INDEX" not in index.markdown
    entry = next(item for item in index.entries if item.title == "kept")
    assert entry.evidence_event_id == appended.event.event_id
    fetched = await memory_manager.fetch_memory(
        FetchMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=added.memory.memory_id)
    )
    assert fetched.memory.evidence[0].event_id == appended.event.event_id
    assert fetched.memory.evidence[0].quote == "EPISODE_BODY_SHOULD_NOT_APPEAR_IN_INDEX"
