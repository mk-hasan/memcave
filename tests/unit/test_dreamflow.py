"""Dreamflow review, suppression, and conflict behavior."""

import json

from tests.conftest import TENANT, USER, start

from memcave.adapters.models.fake import FakeModelProvider
from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    DeleteMemoryRequest,
    EndSessionRequest,
    FetchMemoryRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryCategory, MemoryKind
from memcave.domain.errors import ProviderError
from memcave.domain.models import ModelRequest, ModelResponse
from memcave.services.memory_manager import MemoryManager


async def test_report_keeps_rejected_and_review_candidates(memory_manager: MemoryManager) -> None:
    session_id = await start(memory_manager)
    stored = await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "hello from the fixture"},
        )
    )
    provider = memory_manager.model_provider
    assert isinstance(provider, FakeModelProvider)
    event_id = str(stored.event.event_id)
    provider.scripts["extract"] = json.dumps(
        {
            "candidates": [
                {
                    "category": "preference",
                    "title": "empty",
                    "content": "",
                    "confidence": 0.9,
                    "importance": 0.2,
                    "evidence_event_ids": [event_id],
                },
                {
                    "category": "preference",
                    "title": "maybe",
                    "content": "I prefer careful notes",
                    "confidence": 0.4,
                    "importance": 0.2,
                    "evidence_event_ids": [event_id],
                },
            ]
        }
    )
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    result = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert result.run.report["rejected"]
    assert result.run.report["needs_review"]


async def test_deleted_hash_is_suppressed_until_explicit_remember(
    memory_manager: MemoryManager,
) -> None:
    created = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="concise",
            content="Prefer concise answers.",
        )
    )
    await memory_manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=created.memory.memory_id)
    )
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
    remembered = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=remembered,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={
                "instruction_type": "remember",
                "raw_text": "remember that I prefer concise answers",
                "title": "concise",
                "content": "Prefer concise answers.",
            },
        )
    )
    restored = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="concise answers")
    )
    assert restored.hits


async def test_contradiction_creates_a_new_version(memory_manager: MemoryManager) -> None:
    first = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=first,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "I prefer blue widgets", "subject_key": "widgets"},
        )
    )
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=first)
    )
    await memory_manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    second = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=second,
            event_type=EventType.USER_MESSAGE,
            payload={
                "content": "I prefer red widgets instead of blue widgets",
                "subject_key": "widgets",
            },
        )
    )
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=second)
    )
    await memory_manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="red widgets")
    )
    derived = [hit for hit in found.hits if hit.kind is MemoryKind.DERIVED]
    assert derived
    shown = await memory_manager.fetch_memory(
        FetchMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=derived[0].source_id)
    )
    assert "red widgets" in shown.memory.content
    assert shown.memory.version_number == 2


class _InvalidJsonModel(FakeModelProvider):
    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        if request.task == "extract":
            raise ProviderError("model returned invalid JSON")
        return await super().complete_structured(request)


async def test_invalid_model_json_is_reported_and_not_stored(
    memory_manager: MemoryManager,
) -> None:
    session_id = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "hello from the fixture"},
        )
    )
    provider = memory_manager.model_provider
    assert isinstance(provider, FakeModelProvider)
    provider.scripts["extract"] = "not json"
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    result = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert (
        "model returned invalid JSON; no model memory was written" in result.run.report["failures"]
    )
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="hello from the fixture",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert found.hits == []
    assert "not json" not in result.run.report["index_markdown"]


async def test_invalid_json_keeps_the_rule_memory(memory_manager: MemoryManager) -> None:
    session_id = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "I prefer concise answers"},
        )
    )
    memory_manager.pipeline.extractor.model = _InvalidJsonModel()
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    result = await memory_manager.run_dreamflow(
        RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True)
    )
    assert (
        "model returned invalid JSON; no model memory was written" in result.run.report["failures"]
    )
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            query_text="concise answers",
            memory_kinds=MemoryKind.DERIVED,
        )
    )
    assert found.hits
    assert all("not json" not in hit.content for hit in found.hits)
