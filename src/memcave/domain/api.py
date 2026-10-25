"""Typed requests and responses for MemoryManager."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from memcave.domain.enums import (
    EventType,
    MemoryCategory,
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    ProcessingState,
)
from memcave.domain.models import (
    DreamflowRun,
    EraseCounts,
    MemcaveModel,
    Memory,
    RetrievalHit,
    Session,
    SessionEvent,
)


class StartSessionRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    tenant_name: str | None = None
    user_display_name: str | None = None
    external_id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    memory_opt_out: bool = False
    session_id: UUID | None = None


class StartSessionResponse(MemcaveModel):
    session: Session


class AppendEventRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    event_type: EventType
    payload: dict[str, Any]
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: str = "user"


class AppendEventResponse(MemcaveModel):
    event: SessionEvent
    memory: Memory | None = None


class EndSessionRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    actor: str = "user"


class EndSessionResponse(MemcaveModel):
    session: Session


class ListSessionsRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID | None = None
    limit: int = 50
    cursor: str | None = None


class ListSessionsResponse(MemcaveModel):
    sessions: list[Session]
    next_cursor: str | None = None


class ListEventsRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID | None = None
    session_id: UUID | None = None
    event_type: EventType | None = None
    processing_state: ProcessingState | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 50
    cursor: str | None = None


class ListEventsResponse(MemcaveModel):
    events: list[SessionEvent]
    next_cursor: str | None = None


class SearchMemoryRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    query_text: str
    categories: list[MemoryCategory] | None = None
    memory_kinds: MemoryKind = MemoryKind.BOTH
    top_k: int = 8
    token_budget: int = 2000
    as_of: datetime | None = None
    include_shared: bool = True
    include_candidates: bool = False
    min_confidence: float = 0.0
    actor: str = "agent"


class SearchMemoryResponse(MemcaveModel):
    hits: list[RetrievalHit]
    truncated: bool
    context_block: str
    audit_id: UUID
    reranker_error: str | None = None
    unavailable_components: list[str] = Field(default_factory=list)


class FetchMemoryRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    memory_id: UUID
    version_number: int | None = None
    as_of: datetime | None = None
    include_shared: bool = True
    actor: str = "agent"


class FetchMemoryResponse(MemcaveModel):
    memory: Memory


class EvidenceInput(MemcaveModel):
    session_id: UUID | None = None
    event_id: UUID | None = None
    quote: str | None = None
    weight: float = 1.0


class AddMemoryRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID | None = None
    scope: MemoryScope = MemoryScope.USER
    category: MemoryCategory
    title: str
    content: str
    structured_content: dict[str, Any] | None = None
    confidence: float = 1.0
    importance: float = 0.5
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(default_factory=list)
    source: MemorySource = MemorySource.API
    status: MemoryStatus = MemoryStatus.ACTIVE
    logical_path: str | None = None
    allow_suppressed: bool = True
    actor: str = "user"


class AddMemoryResponse(MemcaveModel):
    memory: Memory


class UpdateMemoryRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    memory_id: UUID
    expected_version: int
    title: str | None = None
    content: str | None = None
    structured_content: dict[str, Any] | None = None
    category: MemoryCategory | None = None
    confidence: float | None = None
    importance: float | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    temporal: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(default_factory=list)
    actor: str = "user"


class UpdateMemoryResponse(MemcaveModel):
    memory: Memory


class DeleteMemoryRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    memory_id: UUID
    actor: str = "user"


class DeleteMemoryResponse(MemcaveModel):
    memory: Memory


class IndexEntry(MemcaveModel):
    logical_path: str
    title: str
    category: MemoryCategory
    status: MemoryStatus
    confidence: float
    summary: str


class GetMemoryIndexRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    as_of: datetime | None = None
    include_shared: bool = True


class GetMemoryIndexResponse(MemcaveModel):
    entries: list[IndexEntry]
    markdown: str


class RunDreamflowRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID | None = None
    use_model: bool = False
    actor: str = "system"
    idempotency_key: str | None = None


class RunDreamflowResponse(MemcaveModel):
    run: DreamflowRun


class ExportMarkdownRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    output_dir: str
    as_of: datetime | None = None
    include_shared: bool = True
    actor: str = "user"


class ExportMarkdownResponse(MemcaveModel):
    output_dir: str
    files: list[str]
    markdown: str


class EraseUserDataRequest(MemcaveModel):
    tenant_id: UUID
    user_id: UUID
    actor: str = "user"


class EraseUserDataResponse(MemcaveModel):
    counts: EraseCounts
    audit_id: UUID
