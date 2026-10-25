"""Ports. Implementations live in adapters."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from memcave.domain.enums import (
    EventType,
    MemoryCategory,
    MemoryStatus,
    OwnerKind,
    ProcessingState,
)
from memcave.domain.models import (
    AuditEvent,
    DreamflowItem,
    DreamflowRun,
    EmbeddingRecord,
    EraseCounts,
    Memory,
    MemoryCandidate,
    MemoryConflict,
    MemoryEvidence,
    MemoryRecord,
    MemoryVersion,
    ModelRequest,
    ModelResponse,
    RetrievalHit,
    Session,
    SessionEvent,
    Tenant,
    TenantScope,
    User,
    VectorMatch,
)


class EpisodicStore(Protocol):
    async def upsert_tenant(self, tenant: Tenant) -> Tenant: ...

    async def upsert_user(self, user: User) -> User: ...

    async def get_user(self, scope: TenantScope, user_id: UUID) -> User | None: ...

    async def create_session(self, session: Session) -> Session: ...

    async def get_session(self, scope: TenantScope, session_id: UUID) -> Session | None: ...

    async def list_sessions(
        self,
        scope: TenantScope,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Session], str | None]: ...

    async def save_session(self, session: Session) -> Session: ...

    async def append_event(self, event: SessionEvent) -> SessionEvent: ...

    async def delete_event(self, scope: TenantScope, event_id: UUID) -> None: ...

    async def list_events(
        self,
        scope: TenantScope,
        *,
        session_id: UUID | None,
        event_type: EventType | None,
        processing_state: ProcessingState | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        cursor: str | None,
        user_id: UUID | None,
    ) -> tuple[list[SessionEvent], str | None]: ...

    async def set_processing_state(
        self,
        scope: TenantScope,
        event_ids: Sequence[UUID],
        state: ProcessingState,
    ) -> None: ...

    async def eligible_sessions(
        self,
        scope: TenantScope,
        *,
        now: datetime,
        max_age: timedelta | None,
    ) -> list[Session]: ...

    async def erase_user(self, scope: TenantScope) -> tuple[int, int]: ...


class DerivedMemoryStore(Protocol):
    async def insert_memory(
        self,
        record: MemoryRecord,
        version: MemoryVersion,
        evidence: Sequence[MemoryEvidence],
    ) -> Memory: ...

    async def add_version(
        self,
        record: MemoryRecord,
        version: MemoryVersion,
        evidence: Sequence[MemoryEvidence],
        *,
        previous_version_id: UUID | None,
        previous_status: MemoryStatus | None,
        previous_valid_until: datetime | None,
    ) -> Memory: ...

    async def add_evidence(self, evidence: MemoryEvidence) -> None: ...

    async def get_memory(
        self,
        scope: TenantScope,
        memory_id: UUID,
        *,
        version_number: int | None,
        as_of: datetime | None,
        include_shared: bool,
        include_deleted: bool = False,
    ) -> Memory | None: ...

    async def list_memories(
        self,
        scope: TenantScope,
        *,
        as_of: datetime,
        include_shared: bool,
        include_candidates: bool,
        categories: Sequence[MemoryCategory] | None,
        min_confidence: float,
        user_id: UUID | None,
    ) -> list[Memory]: ...

    async def find_by_hash(
        self,
        scope: TenantScope,
        *,
        user_id: UUID | None,
        category: MemoryCategory,
        content_hash: str,
        statuses: Sequence[MemoryStatus],
    ) -> Memory | None: ...

    async def clear_hash(self, scope: TenantScope, memory_id: UUID) -> None: ...

    async def list_versions(self, scope: TenantScope, memory_id: UUID) -> list[MemoryVersion]: ...

    async def list_memory_ids(self, scope: TenantScope, user_id: UUID) -> list[UUID]: ...

    async def list_user_memories(self, scope: TenantScope, user_id: UUID) -> list[Memory]: ...

    async def save_conflict(self, conflict: MemoryConflict) -> None: ...

    async def save_run(self, run: DreamflowRun) -> None: ...

    async def get_run_by_key(self, scope: TenantScope, key: str) -> DreamflowRun | None: ...

    async def save_item(self, item: DreamflowItem) -> None: ...

    async def list_items(self, scope: TenantScope, run_id: UUID) -> list[DreamflowItem]: ...

    async def save_candidate(self, candidate: MemoryCandidate) -> None: ...

    async def list_candidates(self, scope: TenantScope, run_id: UUID) -> list[MemoryCandidate]: ...

    async def erase_user(self, scope: TenantScope) -> EraseCounts: ...

    async def existing_paths(self, scope: TenantScope, user_id: UUID | None) -> set[str]: ...


class VectorStore(Protocol):
    async def upsert(self, record: EmbeddingRecord) -> None: ...

    async def search(
        self,
        scope: TenantScope,
        vector: Sequence[float],
        *,
        limit: int,
        owner_kind: OwnerKind | None,
    ) -> list[VectorMatch]: ...

    async def delete_owners(self, scope: TenantScope, owner_ids: Sequence[UUID]) -> int: ...


class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class ModelProvider(Protocol):
    async def complete_structured(self, request: ModelRequest) -> ModelResponse: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class EventSink(Protocol):
    async def emit(self, event: AuditEvent) -> None: ...

    async def list_audits(self, scope: TenantScope) -> list[AuditEvent]: ...


class RedactionPolicy(Protocol):
    def redact_text(self, text: str) -> tuple[str, bool, list[str]]: ...

    def redact_value(self, value: object) -> tuple[object, bool, list[str]]: ...


class RetentionPolicy(Protocol):
    def max_age(self) -> timedelta | None: ...

    def allows(self, occurred_at: datetime, now: datetime) -> bool: ...


class MemoryWritePolicy(Protocol):
    def check_content(self, title: str, content: str) -> None: ...

    def check_scope(self, scope: TenantScope, tenant_id: UUID, user_id: UUID | None) -> None: ...


class Reranker(Protocol):
    async def rerank(self, query: str, hits: Sequence[RetrievalHit]) -> Sequence[RetrievalHit]: ...


class Span(Protocol):
    def set_attribute(self, key: str, value: str | int | float | bool) -> None: ...

    def record_exception(self, exc: BaseException) -> None: ...

    def end(self) -> None: ...


class Tracer(Protocol):
    def start_span(
        self,
        name: str,
        attributes: Mapping[str, str | int | float | bool] | None = None,
    ) -> Span: ...
