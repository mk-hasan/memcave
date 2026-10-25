"""Load fixture history into a MemoryManager."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from memcave.domain.api import AppendEventRequest, EndSessionRequest, StartSessionRequest
from memcave.domain.enums import EventType
from memcave.domain.models import BenchmarkCase, MemcaveModel
from memcave.services.memory_manager import MemoryManager
from memcave.services.text import event_search_text


class FixtureEvent(MemcaveModel):
    event_type: EventType
    payload: dict[str, Any]
    occurred_at: datetime | None = None


class FixtureSession(MemcaveModel):
    title: str = "session"
    events: list[FixtureEvent]
    memory_opt_out: bool = False


class FixtureDecoy(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    sessions: list[FixtureSession]


_SESSIONS = TypeAdapter(list[FixtureSession])
_DECOYS = TypeAdapter(list[FixtureDecoy])


async def ingest_case(manager: MemoryManager, case: BenchmarkCase) -> str:
    """Store the case history and decoys. Returns the raw history text."""
    history = await _ingest_sessions(
        manager, case.tenant_id, case.user_id, _SESSIONS.validate_python(case.history)
    )
    for decoy in _DECOYS.validate_python(case.decoys):
        await _ingest_sessions(manager, decoy.tenant_id, decoy.user_id, decoy.sessions)
    return history


async def _ingest_sessions(
    manager: MemoryManager,
    tenant_id: UUID,
    user_id: UUID,
    sessions: list[FixtureSession],
) -> str:
    lines: list[str] = []
    for spec in sessions:
        started = await manager.start_session(
            StartSessionRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                title=spec.title,
                memory_opt_out=spec.memory_opt_out,
            )
        )
        for event in spec.events:
            await manager.append_event(
                AppendEventRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=started.session.session_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    occurred_at=event.occurred_at,
                )
            )
            lines.extend(_history_lines(event.event_type, event.payload))
        await manager.end_session(
            EndSessionRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=started.session.session_id,
            )
        )
    return "\n".join(lines)


def _history_lines(event_type: EventType, payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if event_type is EventType.TOOL_CALL:
        name = payload.get("tool_name")
        if isinstance(name, str) and name:
            lines.append(f"TOOL: {name}")
    text = event_search_text(payload)
    if text:
        lines.append(text)
    return lines
