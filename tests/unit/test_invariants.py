"""Invariants that are pipeline behavior rather than a store contract."""

from collections.abc import Sequence

from tests.conftest import OTHER_TENANT, OTHER_USER, TENANT, USER, manager_from_parts, start

from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.adapters.models.fake import FakeModelProvider
from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    EndSessionRequest,
    RunDreamflowRequest,
)
from memcave.domain.enums import EventType, MemoryCategory
from memcave.domain.models import ModelRequest, ModelResponse, TenantScope
from memcave.services.memory_manager import MemoryManager
from memcave.services.text import content_hash, subject_key


class ConstantEmbedding:
    dimension = 64

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, *([0.0] * 63)] for _ in texts]


class FlakyModel(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        if "BOOM" in request.user_prompt and not self.failed:
            self.failed = True
            raise RuntimeError("down")
        return await super().complete_structured(request)


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
    manager: MemoryManager, content: str, subject_key: str | None = None
) -> None:
    session_id = await start(manager)
    payload: dict[str, str] = {"content": content}
    if subject_key is not None:
        payload["subject_key"] = subject_key
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload=payload,
        )
    )
    await manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )


async def _stored(manager: MemoryManager) -> list[str]:
    memories = await manager.deps.derived.list_user_memories(
        TenantScope(tenant_id=TENANT, user_id=USER), USER
    )
    return [item.content for item in memories]


def test_hash_is_tenant_user_category_and_casefolded_content() -> None:
    left = content_hash(TENANT, USER, MemoryCategory.PREFERENCE, "Prefer  Concise")
    right = content_hash(TENANT, USER, MemoryCategory.PREFERENCE, "prefer concise")
    other_user = content_hash(TENANT, OTHER_USER, MemoryCategory.PREFERENCE, "prefer concise")
    assert left == right
    assert left != other_user
    assert left != content_hash(TENANT, None, MemoryCategory.PREFERENCE, "prefer concise")
    assert left != content_hash(OTHER_TENANT, USER, MemoryCategory.PREFERENCE, "prefer concise")
    assert left != content_hash(TENANT, USER, MemoryCategory.PROJECT, "prefer concise")
    assert subject_key("I prefer concise answers") == "concise"
    assert subject_key("Prefer metric units") == "metric"


async def test_similarity_alone_does_not_merge() -> None:
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
    await _end_and_message(manager, "Prefer metric units.")
    result = await manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=False)
    )
    assert any(note.startswith("create:") for note in result.run.report["notes"])
    assert sorted(await _stored(manager)) == [
        "Prefer concise answers.",
        "Prefer metric units.",
    ]
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=OTHER_TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="violet",
            content="Prefer violet notebooks.",
        )
    )
    await _end_and_message(manager, "Prefer violet notebooks.")
    separated = await manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=False)
    )
    assert any(note.startswith("create:") for note in separated.run.report["notes"])
    assert (await _stored(manager)).count("Prefer violet notebooks.") == 1
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.DOMAIN_KNOWLEDGE,
            title="warehouse",
            content="warehouse receiving notes",
        )
    )
    await _end_and_message(manager, "I prefer warehouse lighting")
    await manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=False)
    )
    await _end_and_message(manager, "Prefer concise replies.", subject_key="answer-style")
    merged = await manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=False)
    )
    assert any(note.startswith("merge:") for note in merged.run.report["notes"])
    stored = await _stored(manager)
    assert stored.count("Prefer concise answers.") == 1
    assert "Prefer concise replies." not in stored
    assert "I prefer warehouse lighting" in stored
    assert "warehouse receiving notes" in stored


async def test_retry_resumes_and_a_second_success_creates_nothing(
    memory_manager: MemoryManager,
) -> None:
    flaky = FlakyModel()
    memory_manager.pipeline.extractor.model = flaky
    memory_manager.pipeline.classifier.model = flaky
    await _end_and_message(memory_manager, "I prefer blue widgets")
    await _end_and_message(memory_manager, "BOOM prefer red notebooks")
    first = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert first.run.status.value == "partial"
    assert (await _stored(memory_manager)).count("I prefer blue widgets") == 1
    second = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert second.run.status.value == "succeeded"
    stored = await _stored(memory_manager)
    assert stored.count("I prefer blue widgets") == 1
    assert stored.count("BOOM prefer red notebooks") == 1
    third = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert third.run.status.value == "succeeded"
    assert await _stored(memory_manager) == stored
