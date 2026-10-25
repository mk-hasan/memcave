"""Core memory behavior."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from tests.conftest import NOW, OTHER_TENANT, OTHER_USER, TENANT, USER, start

from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    DeleteMemoryRequest,
    EndSessionRequest,
    EraseUserDataRequest,
    ExportMarkdownRequest,
    SearchMemoryRequest,
    UpdateMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryCategory, MemoryScope
from memcave.domain.errors import NotFoundError, ValidationError, VersionConflictError
from memcave.services.memory_manager import MemoryManager
from memcave.services.render import FENCE_END, FENCE_START, escape_fence
from memcave.services.text import redact_text, validate_logical_path


async def _remember(manager: MemoryManager, session_id: UUID, text: str) -> None:
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={"instruction_type": "remember", "raw_text": text},
        )
    )


async def test_explicit_preference_is_searchable(memory_manager: MemoryManager) -> None:
    session_id = await start(memory_manager)
    await _remember(memory_manager, session_id, "remember that I prefer concise answers")
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="concise answers")
    )
    assert any("concise" in hit.content for hit in found.hits)
    assert FENCE_START in found.context_block
    assert "untrusted data" in found.context_block


async def test_version_conflict_and_soft_delete(memory_manager: MemoryManager) -> None:
    added = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PROJECT,
            title="Demo project",
            content="The demo project uses shop_demo.",
        )
    )
    with pytest.raises(VersionConflictError):
        await memory_manager.update_memory(
            UpdateMemoryRequest(
                tenant_id=TENANT,
                user_id=USER,
                memory_id=added.memory.memory_id,
                expected_version=9,
                content="changed",
            )
        )
    updated = await memory_manager.update_memory(
        UpdateMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            memory_id=added.memory.memory_id,
            expected_version=1,
            content="The demo project uses shop_demo tables.",
        )
    )
    assert updated.memory.version_number == 2
    assert updated.memory.predecessor_version_id == added.memory.version_id
    deleted = await memory_manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=added.memory.memory_id)
    )
    assert deleted.memory.status.value == "deleted"
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="shop_demo")
    )
    assert found.hits == []


async def test_tenant_isolation(memory_manager: MemoryManager) -> None:
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="Secret preference",
            content="USERA_ONLY_FACT prefers blue notebooks.",
        )
    )
    await start(memory_manager, OTHER_TENANT, OTHER_USER)
    found = await memory_manager.search_memory(
        SearchMemoryRequest(
            tenant_id=OTHER_TENANT,
            user_id=OTHER_USER,
            query_text="USERA_ONLY_FACT notebooks",
        )
    )
    assert found.hits == []


async def test_other_user_is_hidden(memory_manager: MemoryManager) -> None:
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="Private",
            content="USERA_ONLY_FACT stays private.",
        )
    )
    await start(memory_manager, TENANT, OTHER_USER)
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=OTHER_USER, query_text="USERA_ONLY_FACT")
    )
    assert found.hits == []
    shared = await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=None,
            scope=MemoryScope.TENANT,
            category=MemoryCategory.CONSTRAINT,
            title="Shared rule",
            content="Shared teams must use metric units.",
        )
    )
    assert shared.memory.user_id is None
    found_shared = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=OTHER_USER, query_text="metric units")
    )
    assert any(hit.source_id == shared.memory.memory_id for hit in found_shared.hits)


async def test_redaction_and_fence() -> None:
    text, changed, reasons = redact_text(
        "key sk-abcdefghijklmnopqrstuvwxyz and AKIAABCDEFGHIJKLMNOP"
    )
    assert changed
    assert "sk-" not in text
    assert "AKIA" not in text
    assert "openai" in reasons
    escaped = escape_fence(f"before {FENCE_START} inside {FENCE_END}")
    assert FENCE_START not in escaped
    with pytest.raises(ValidationError):
        validate_logical_path("../secrets")


async def test_export_stays_in_directory(memory_manager: MemoryManager, tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    await memory_manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="../../etc/passwd",
            content="Prefer concise answers.",
        )
    )
    exported = await memory_manager.export_markdown(
        ExportMarkdownRequest(tenant_id=TENANT, user_id=USER, output_dir=str(tmp_path))
    )
    for name in exported.files:
        assert Path(name).resolve().is_relative_to(tmp_path.resolve())
    assert (tmp_path / "memory.md").exists()


async def test_erase_removes_user_data(memory_manager: MemoryManager) -> None:
    session_id = await start(memory_manager)
    await _remember(memory_manager, session_id, "remember that I prefer concise answers")
    await memory_manager.erase_user_data(EraseUserDataRequest(tenant_id=TENANT, user_id=USER))
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="concise")
    )
    assert found.hits == []
    with pytest.raises(NotFoundError):
        await memory_manager.end_session(
            EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
        )


async def test_dreamflow_is_idempotent_and_versions_conflicts(
    memory_manager: MemoryManager,
) -> None:
    session_id = await start(memory_manager)
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.STRUCTURED_OUTCOME,
            payload={
                "outcome_type": "workflow",
                "data": {
                    "goal": "quarterly shipped revenue",
                    "steps": [{"tool": "run_query", "purpose": "aggregate"}],
                    "tables": ["orders"],
                },
            },
        )
    )
    await memory_manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "Use the orders table instead of the purchases table."},
        )
    )
    await memory_manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    from memcave.domain.api import RunDreamflowRequest

    first = await memory_manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    second = await memory_manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER))
    assert first.run.report["index_markdown"]
    assert second.run.status.value == "succeeded"
    found = await memory_manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="quarterly shipped revenue")
    )
    assert any("orders" in hit.content or "revenue" in hit.content for hit in found.hits)


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        from memcave.services.runtime import FixedClock

        FixedClock(datetime(2026, 1, 1))


def test_clock_constant() -> None:
    assert NOW.tzinfo == UTC
