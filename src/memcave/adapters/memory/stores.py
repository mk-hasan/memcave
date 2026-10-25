"""In-memory adapters used by tests and the memory CLI backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from memcave.domain.enums import (
    DreamflowState,
    EventType,
    MemoryCategory,
    MemoryScope,
    MemoryStatus,
    OwnerKind,
    ProcessingState,
    SessionStatus,
)
from memcave.domain.errors import NotFoundError, TenantIsolationError
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
    Session,
    SessionEvent,
    Tenant,
    TenantScope,
    User,
    VectorMatch,
)
from memcave.services.text import (
    cosine_similarity,
    decode_cursor,
    encode_cursor,
    resolve_subject_key,
)


@dataclass
class InMemoryData:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)
    sessions: dict[UUID, Session] = field(default_factory=dict)
    events: dict[UUID, SessionEvent] = field(default_factory=dict)
    records: dict[UUID, MemoryRecord] = field(default_factory=dict)
    versions: dict[UUID, MemoryVersion] = field(default_factory=dict)
    evidence: dict[UUID, MemoryEvidence] = field(default_factory=dict)
    conflicts: dict[UUID, MemoryConflict] = field(default_factory=dict)
    embeddings: dict[UUID, EmbeddingRecord] = field(default_factory=dict)
    runs: dict[UUID, DreamflowRun] = field(default_factory=dict)
    items: list[DreamflowItem] = field(default_factory=list)
    candidates: list[MemoryCandidate] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _memory_view(
    record: MemoryRecord,
    version: MemoryVersion,
    evidence: list[MemoryEvidence],
) -> Memory:
    return Memory(
        memory_id=record.memory_id,
        version_id=version.version_id,
        tenant_id=record.tenant_id,
        scope=record.scope,
        user_id=record.user_id,
        category=record.category,
        title=version.title,
        content=version.content,
        structured_content=version.structured_content,
        status=version.status,
        confidence=version.confidence,
        importance=version.importance,
        valid_from=version.valid_from,
        valid_until=version.valid_until,
        created_at=record.created_at,
        updated_at=version.created_at,
        evidence=evidence,
        version_number=version.version_number,
        predecessor_version_id=version.predecessor_version_id,
        embedding_id=version.embedding_id,
        metadata=version.metadata,
        subject_key=resolve_subject_key(
            category=record.category,
            title=version.title,
            content=version.content,
            structured_content=version.structured_content,
            metadata=version.metadata,
        ),
        logical_path=record.logical_path,
        source=version.source,
        content_hash=version.content_hash,
        deleted_at=record.deleted_at,
        record_status=record.status,
    )


def _window(version: MemoryVersion, as_of: datetime) -> bool:
    if version.valid_from > as_of:
        return False
    return version.valid_until is None or version.valid_until > as_of


class InMemoryEpisodicStore:
    def __init__(self, data: InMemoryData) -> None:
        self.data = data

    async def upsert_tenant(self, tenant: Tenant) -> Tenant:
        async with self.data.lock:
            self.data.tenants[tenant.tenant_id] = tenant
            return tenant

    async def upsert_user(self, user: User) -> User:
        async with self.data.lock:
            self.data.users[user.user_id] = user
            return user

    async def get_user(self, scope: TenantScope, user_id: UUID) -> User | None:
        user = self.data.users.get(user_id)
        if user is None or user.tenant_id != scope.tenant_id:
            return None
        return user

    async def create_session(self, session: Session) -> Session:
        async with self.data.lock:
            self.data.sessions[session.session_id] = session
            return session

    async def get_session(self, scope: TenantScope, session_id: UUID) -> Session | None:
        session = self.data.sessions.get(session_id)
        if session is None or session.tenant_id != scope.tenant_id:
            return None
        return session

    async def list_sessions(
        self,
        scope: TenantScope,
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Session], str | None]:
        rows = [
            session
            for session in self.data.sessions.values()
            if session.tenant_id == scope.tenant_id
            and (scope.user_id is None or session.user_id == scope.user_id)
        ]
        return _page(rows, limit, cursor, lambda item: (item.started_at, item.session_id))

    async def save_session(self, session: Session) -> Session:
        async with self.data.lock:
            current = self.data.sessions.get(session.session_id)
            if current is None or current.tenant_id != session.tenant_id:
                raise NotFoundError("session was not found")
            self.data.sessions[session.session_id] = session
            return session

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        async with self.data.lock:
            session = self.data.sessions.get(event.session_id)
            if (
                session is None
                or session.tenant_id != event.tenant_id
                or session.user_id != event.user_id
            ):
                raise TenantIsolationError("event scope does not match the session")
            sequence = 1 + max(
                (
                    existing.sequence
                    for existing in self.data.events.values()
                    if existing.session_id == event.session_id
                ),
                default=0,
            )
            stored = event.model_copy(update={"sequence": sequence})
            self.data.events[stored.event_id] = stored
            return stored

    async def delete_event(self, scope: TenantScope, event_id: UUID) -> None:
        async with self.data.lock:
            event = self.data.events.get(event_id)
            if event is None:
                return
            if event.tenant_id != scope.tenant_id:
                raise TenantIsolationError("event belongs to another tenant")
            del self.data.events[event_id]

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
    ) -> tuple[list[SessionEvent], str | None]:
        rows = []
        for event in self.data.events.values():
            if event.tenant_id != scope.tenant_id:
                continue
            if user_id is not None and event.user_id != user_id:
                continue
            if session_id is not None and event.session_id != session_id:
                continue
            if event_type is not None and event.event_type is not event_type:
                continue
            if processing_state is not None and event.processing_state is not processing_state:
                continue
            if start is not None and event.occurred_at < start:
                continue
            if end is not None and event.occurred_at >= end:
                continue
            rows.append(event)
        return _page(rows, limit, cursor, lambda item: (item.occurred_at, item.event_id))

    async def set_processing_state(
        self,
        scope: TenantScope,
        event_ids: list[UUID] | tuple[UUID, ...],
        state: ProcessingState,
    ) -> None:
        async with self.data.lock:
            for event_id in event_ids:
                event = self.data.events.get(event_id)
                if event is None or event.tenant_id != scope.tenant_id:
                    continue
                self.data.events[event_id] = event.model_copy(update={"processing_state": state})

    async def eligible_sessions(
        self,
        scope: TenantScope,
        *,
        now: datetime,
        max_age: timedelta | None,
    ) -> list[Session]:
        rows: list[Session] = []
        for session in self.data.sessions.values():
            if session.tenant_id != scope.tenant_id:
                continue
            if scope.user_id is not None and session.user_id != scope.user_id:
                continue
            if session.status is not SessionStatus.ENDED:
                continue
            if session.dreamflow_state not in {DreamflowState.PENDING, DreamflowState.FAILED}:
                continue
            if session.metadata.get("memory_opt_out") is True:
                continue
            user = self.data.users.get(session.user_id)
            if user is not None and user.memory_opt_out:
                continue
            anchor = session.ended_at or session.started_at
            if max_age is not None and now - anchor > max_age:
                continue
            rows.append(session)
        return rows

    async def erase_user(self, scope: TenantScope) -> tuple[int, int]:
        async with self.data.lock:
            if scope.user_id is None:
                return 0, 0
            session_ids = [
                session.session_id
                for session in self.data.sessions.values()
                if session.tenant_id == scope.tenant_id and session.user_id == scope.user_id
            ]
            event_ids = [
                event.event_id
                for event in self.data.events.values()
                if event.tenant_id == scope.tenant_id and event.user_id == scope.user_id
            ]
            for session_id in session_ids:
                del self.data.sessions[session_id]
            for event_id in event_ids:
                del self.data.events[event_id]
            return len(session_ids), len(event_ids)


class InMemoryDerivedStore:
    def __init__(self, data: InMemoryData) -> None:
        self.data = data

    def _evidence_for(self, version_id: UUID) -> list[MemoryEvidence]:
        return [item for item in self.data.evidence.values() if item.version_id == version_id]

    def _versions_for(self, memory_id: UUID) -> list[MemoryVersion]:
        return [item for item in self.data.versions.values() if item.memory_id == memory_id]

    def _visible_record(
        self, scope: TenantScope, record: MemoryRecord, include_shared: bool, user_id: UUID | None
    ) -> bool:
        if record.tenant_id != scope.tenant_id:
            return False
        if record.scope is MemoryScope.TENANT:
            return include_shared
        return user_id is None or record.user_id == user_id

    async def insert_memory(
        self,
        record: MemoryRecord,
        version: MemoryVersion,
        evidence: list[MemoryEvidence] | tuple[MemoryEvidence, ...],
    ) -> Memory:
        async with self.data.lock:
            self._reject_duplicate(record)
            self.data.records[record.memory_id] = record
            self.data.versions[version.version_id] = version
            for item in evidence:
                self.data.evidence[item.evidence_id] = item
            return _memory_view(record, version, list(evidence))

    def _reject_duplicate(self, record: MemoryRecord) -> None:
        if record.status not in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}:
            return
        for existing in self.data.records.values():
            if existing.memory_id == record.memory_id:
                continue
            if existing.status not in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}:
                continue
            if (
                existing.tenant_id == record.tenant_id
                and existing.scope == record.scope
                and existing.user_id == record.user_id
                and existing.category == record.category
                and existing.content_hash == record.content_hash
            ):
                from memcave.domain.errors import PolicyRejectedError

                raise PolicyRejectedError("active content hash already exists")

    async def add_version(
        self,
        record: MemoryRecord,
        version: MemoryVersion,
        evidence: list[MemoryEvidence] | tuple[MemoryEvidence, ...],
        *,
        previous_version_id: UUID | None,
        previous_status: MemoryStatus | None,
        previous_valid_until: datetime | None,
    ) -> Memory:
        async with self.data.lock:
            if previous_version_id is not None and previous_status is not None:
                previous = self.data.versions[previous_version_id]
                self.data.versions[previous_version_id] = previous.model_copy(
                    update={"status": previous_status, "valid_until": previous_valid_until}
                )
            self.data.records[record.memory_id] = record
            self.data.versions[version.version_id] = version
            for item in evidence:
                self.data.evidence[item.evidence_id] = item
            return _memory_view(record, version, list(evidence))

    async def add_evidence(self, evidence: MemoryEvidence) -> None:
        async with self.data.lock:
            self.data.evidence[evidence.evidence_id] = evidence

    async def get_memory(
        self,
        scope: TenantScope,
        memory_id: UUID,
        *,
        version_number: int | None,
        as_of: datetime | None,
        include_shared: bool,
        include_deleted: bool = False,
    ) -> Memory | None:
        record = self.data.records.get(memory_id)
        if record is None or not self._visible_record(scope, record, include_shared, scope.user_id):
            return None
        version = self._select_version(record, version_number, as_of)
        if version is None:
            return None
        if version.status is MemoryStatus.DELETED and not include_deleted:
            return None
        return _memory_view(record, version, self._evidence_for(version.version_id))

    def _select_version(
        self,
        record: MemoryRecord,
        version_number: int | None,
        as_of: datetime | None,
    ) -> MemoryVersion | None:
        versions = self._versions_for(record.memory_id)
        if version_number is not None:
            for version in versions:
                if version.version_number == version_number:
                    return version
            return None
        if as_of is None:
            for version in versions:
                if version.version_number == record.current_version_number:
                    return version
            return None
        matching = [
            version
            for version in versions
            if version.status is MemoryStatus.ACTIVE and _window(version, as_of)
        ]
        if not matching:
            return None
        return max(matching, key=lambda item: (item.valid_from, item.version_number))

    async def list_memories(
        self,
        scope: TenantScope,
        *,
        as_of: datetime,
        include_shared: bool,
        include_candidates: bool,
        categories: list[MemoryCategory] | tuple[MemoryCategory, ...] | None,
        min_confidence: float,
        user_id: UUID | None,
    ) -> list[Memory]:
        found: list[Memory] = []
        allowed = {MemoryStatus.ACTIVE}
        if include_candidates:
            allowed.add(MemoryStatus.CANDIDATE)
        for record in self.data.records.values():
            if record.status is MemoryStatus.DELETED:
                continue
            if not self._visible_record(scope, record, include_shared, user_id):
                continue
            if categories is not None and record.category not in categories:
                continue
            for version in self._versions_for(record.memory_id):
                if version.status not in allowed:
                    continue
                if version.confidence < min_confidence or not _window(version, as_of):
                    continue
                found.append(_memory_view(record, version, self._evidence_for(version.version_id)))
        return found

    async def find_by_hash(
        self,
        scope: TenantScope,
        *,
        user_id: UUID | None,
        category: MemoryCategory,
        content_hash: str,
        statuses: list[MemoryStatus] | tuple[MemoryStatus, ...],
    ) -> Memory | None:
        for record in self.data.records.values():
            if record.tenant_id != scope.tenant_id or record.category is not category:
                continue
            if record.user_id != user_id:
                continue
            if record.status not in statuses or record.content_hash != content_hash:
                continue
            version = self._select_version(record, None, None)
            if version is None:
                continue
            return _memory_view(record, version, self._evidence_for(version.version_id))
        return None

    async def clear_hash(self, scope: TenantScope, memory_id: UUID) -> None:
        async with self.data.lock:
            record = self.data.records.get(memory_id)
            if record is None or record.tenant_id != scope.tenant_id:
                return
            self.data.records[memory_id] = record.model_copy(update={"content_hash": ""})
            for version in self._versions_for(memory_id):
                if version.status is MemoryStatus.DELETED:
                    self.data.versions[version.version_id] = version.model_copy(
                        update={"content_hash": ""}
                    )

    async def list_versions(self, scope: TenantScope, memory_id: UUID) -> list[MemoryVersion]:
        record = self.data.records.get(memory_id)
        if record is None or record.tenant_id != scope.tenant_id:
            return []
        if scope.user_id is not None and record.user_id not in {None, scope.user_id}:
            return []
        return sorted(self._versions_for(memory_id), key=lambda item: item.version_number)

    async def list_memory_ids(self, scope: TenantScope, user_id: UUID) -> list[UUID]:
        return [
            record.memory_id
            for record in self.data.records.values()
            if record.tenant_id == scope.tenant_id and record.user_id == user_id
        ]

    async def list_user_memories(self, scope: TenantScope, user_id: UUID) -> list[Memory]:
        found: list[Memory] = []
        for record in self.data.records.values():
            if record.tenant_id != scope.tenant_id or record.user_id != user_id:
                continue
            if record.status is MemoryStatus.DELETED:
                continue
            version = self._select_version(record, None, None)
            if version is None:
                continue
            found.append(_memory_view(record, version, self._evidence_for(version.version_id)))
        return found

    async def save_conflict(self, conflict: MemoryConflict) -> None:
        async with self.data.lock:
            self.data.conflicts[conflict.conflict_id] = conflict

    async def save_run(self, run: DreamflowRun) -> None:
        async with self.data.lock:
            self.data.runs[run.run_id] = run

    async def get_run_by_key(self, scope: TenantScope, key: str) -> DreamflowRun | None:
        for run in self.data.runs.values():
            if run.tenant_id == scope.tenant_id and run.idempotency_key == key:
                return run
        return None

    async def save_item(self, item: DreamflowItem) -> None:
        async with self.data.lock:
            self.data.items = [
                existing for existing in self.data.items if existing.item_id != item.item_id
            ]
            self.data.items.append(item)

    async def list_items(self, scope: TenantScope, run_id: UUID) -> list[DreamflowItem]:
        return [
            item
            for item in self.data.items
            if item.tenant_id == scope.tenant_id and item.run_id == run_id
        ]

    async def save_candidate(self, candidate: MemoryCandidate) -> None:
        async with self.data.lock:
            self.data.candidates = [
                existing
                for existing in self.data.candidates
                if existing.candidate_id != candidate.candidate_id
            ]
            self.data.candidates.append(candidate)

    async def list_candidates(self, scope: TenantScope, run_id: UUID) -> list[MemoryCandidate]:
        return [
            item
            for item in self.data.candidates
            if item.tenant_id == scope.tenant_id and item.dreamflow_run_id == run_id
        ]

    async def erase_user(self, scope: TenantScope) -> EraseCounts:
        async with self.data.lock:
            if scope.user_id is None:
                return EraseCounts()
            memory_ids = [
                record.memory_id
                for record in self.data.records.values()
                if record.tenant_id == scope.tenant_id and record.user_id == scope.user_id
            ]
            version_ids = [
                version.version_id
                for version in self.data.versions.values()
                if version.memory_id in memory_ids
            ]
            evidence_ids = [
                item.evidence_id
                for item in self.data.evidence.values()
                if item.memory_id in memory_ids
            ]
            conflict_ids = [
                item.conflict_id
                for item in self.data.conflicts.values()
                if item.memory_id in memory_ids
            ]
            items_before = len(self.data.items)
            self.data.items = [
                item
                for item in self.data.items
                if not (
                    item.tenant_id == scope.tenant_id
                    and (
                        item.session_id
                        in {
                            session.session_id
                            for session in self.data.sessions.values()
                            if session.user_id == scope.user_id
                        }
                        or item.payload.get("user_id") == str(scope.user_id)
                    )
                )
            ]
            removed_items = items_before - len(self.data.items)
            self.data.candidates = [
                item
                for item in self.data.candidates
                if not (item.tenant_id == scope.tenant_id and item.user_id == scope.user_id)
            ]
            for memory_id in memory_ids:
                del self.data.records[memory_id]
            for version_id in version_ids:
                del self.data.versions[version_id]
            for evidence_id in evidence_ids:
                del self.data.evidence[evidence_id]
            for conflict_id in conflict_ids:
                del self.data.conflicts[conflict_id]
            return EraseCounts(
                memories=len(memory_ids),
                versions=len(version_ids),
                evidence=len(evidence_ids),
                conflicts=len(conflict_ids),
                dreamflow_items=removed_items,
            )

    async def existing_paths(self, scope: TenantScope, user_id: UUID | None) -> set[str]:
        return {
            record.logical_path
            for record in self.data.records.values()
            if record.tenant_id == scope.tenant_id and record.user_id == user_id
        }


class InMemoryVectorStore:
    def __init__(self, data: InMemoryData) -> None:
        self.data = data

    async def upsert(self, record: EmbeddingRecord) -> None:
        async with self.data.lock:
            for existing in list(self.data.embeddings.values()):
                if (
                    existing.owner_kind == record.owner_kind
                    and existing.owner_id == record.owner_id
                ):
                    del self.data.embeddings[existing.embedding_id]
            self.data.embeddings[record.embedding_id] = record

    async def search(
        self,
        scope: TenantScope,
        vector: list[float] | tuple[float, ...],
        *,
        limit: int,
        owner_kind: OwnerKind | None,
    ) -> list[VectorMatch]:
        matches: list[VectorMatch] = []
        for record in self.data.embeddings.values():
            if record.tenant_id != scope.tenant_id:
                continue
            if owner_kind is not None and record.owner_kind is not owner_kind:
                continue
            matches.append(
                VectorMatch(
                    owner_kind=record.owner_kind,
                    owner_id=record.owner_id,
                    cosine=cosine_similarity(list(vector), record.vector),
                )
            )
        matches.sort(key=lambda item: item.cosine, reverse=True)
        return matches[:limit]

    async def delete_owners(
        self, scope: TenantScope, owner_ids: list[UUID] | tuple[UUID, ...]
    ) -> int:
        async with self.data.lock:
            wanted = set(owner_ids)
            removed = [
                record.embedding_id
                for record in self.data.embeddings.values()
                if record.tenant_id == scope.tenant_id and record.owner_id in wanted
            ]
            for embedding_id in removed:
                del self.data.embeddings[embedding_id]
            return len(removed)


class InMemoryEventSink:
    def __init__(self, data: InMemoryData) -> None:
        self.data = data

    async def emit(self, event: AuditEvent) -> None:
        async with self.data.lock:
            self.data.audits.append(event)

    async def list_audits(self, scope: TenantScope) -> list[AuditEvent]:
        return [event for event in self.data.audits if event.tenant_id == scope.tenant_id]


def _page[T](
    rows: list[T],
    limit: int,
    cursor: str | None,
    key_fn: object,
) -> tuple[list[T], str | None]:
    if not callable(key_fn):
        return [], None
    ordered = sorted(rows, key=key_fn)
    if cursor is not None:
        moment, identifier = decode_cursor(cursor)
        ordered = [item for item in ordered if key_fn(item) > (moment, identifier)]
    page = ordered[:limit]
    if len(ordered) <= limit or not page:
        return page, None
    last_key = key_fn(page[-1])
    return page, encode_cursor(last_key[0], last_key[1])
