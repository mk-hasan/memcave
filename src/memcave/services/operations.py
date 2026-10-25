"""Shared dependencies and memory operations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    DeleteMemoryRequest,
    EvidenceInput,
    ExportMarkdownRequest,
    FetchMemoryRequest,
    SearchMemoryRequest,
    UpdateMemoryRequest,
)
from memcave.domain.enums import (
    AuditAction,
    EventType,
    InstructionType,
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    OwnerKind,
    ProcessingState,
)
from memcave.domain.errors import NotFoundError, PolicyRejectedError, ScopeError, ValidationError
from memcave.domain.models import (
    AuditEvent,
    EmbeddingRecord,
    EraseCounts,
    InstructionPayload,
    Memory,
    MemoryEvidence,
    MemoryRecord,
    MemoryVersion,
    RetrievalConfig,
    RetrievalHit,
    ScoreParts,
    SessionEvent,
    TenantScope,
)
from memcave.ports.protocols import (
    Clock,
    DerivedMemoryStore,
    EmbeddingProvider,
    EpisodicStore,
    EventSink,
    MemoryWritePolicy,
    RedactionPolicy,
    Reranker,
    RetentionPolicy,
    Tracer,
    VectorStore,
)
from memcave.services.instructions import immediate_instruction, parse_instruction
from memcave.services.render import render_context, render_index
from memcave.services.scoring import (
    episodic_importance,
    min_max,
    recency_score,
    score_parts,
    window_contains,
)
from memcave.services.text import (
    allocate_logical_path,
    content_hash,
    estimate_tokens,
    event_search_text,
    lexical_score,
    query_fingerprint,
    redact_text,
)


@dataclass
class Deps:
    episodic: EpisodicStore
    derived: DerivedMemoryStore
    vectors: VectorStore
    embeddings: EmbeddingProvider
    clock: Clock
    sink: EventSink
    redaction: RedactionPolicy
    retention: RetentionPolicy
    write_policy: MemoryWritePolicy
    tracer: Tracer
    reranker: Reranker
    config: RetrievalConfig


def _scope(tenant_id: UUID, user_id: UUID | None) -> TenantScope:
    return TenantScope(tenant_id=tenant_id, user_id=user_id)


async def _audit(deps: Deps, event: AuditEvent) -> None:
    await deps.sink.emit(event)


async def _embed(
    deps: Deps,
    scope: TenantScope,
    text: str,
    owner_kind: OwnerKind,
    owner_id: UUID,
    user_id: UUID | None,
) -> UUID:
    vectors = await deps.embeddings.embed([text])
    if len(vectors) != 1 or len(vectors[0]) != deps.embeddings.dimension:
        from memcave.domain.errors import ProviderError

        raise ProviderError("embedding provider returned an unexpected vector")
    embedding_id = uuid4()
    await deps.vectors.upsert(
        EmbeddingRecord(
            embedding_id=embedding_id,
            tenant_id=scope.tenant_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            user_id=user_id,
            dimension=deps.embeddings.dimension,
            vector=vectors[0],
            created_at=deps.clock.now(),
        )
    )
    return embedding_id


def _evidence_from(
    scope: TenantScope,
    memory_id: UUID,
    version_id: UUID,
    inputs: list[EvidenceInput],
    now: object,
) -> list[MemoryEvidence]:
    from datetime import datetime

    if not isinstance(now, datetime):
        raise ValidationError("internal clock value is invalid")
    rows: list[MemoryEvidence] = []
    for item in inputs:
        quote = item.quote
        if quote is not None:
            quote, _, _ = redact_text(quote)
        rows.append(
            MemoryEvidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
                version_id=version_id,
                tenant_id=scope.tenant_id,
                session_id=item.session_id,
                event_id=item.event_id,
                quote=quote,
                weight=item.weight,
                created_at=now,
            )
        )
    return rows


async def add_memory(deps: Deps, request: AddMemoryRequest) -> Memory:
    scope = _scope(request.tenant_id, request.user_id)
    user_id = None if request.scope is MemoryScope.TENANT else request.user_id
    if request.scope is MemoryScope.USER and user_id is None:
        raise ScopeError("user-scoped memory requires a user id")
    deps.write_policy.check_scope(scope, request.tenant_id, user_id)
    title, _, _ = deps.redaction.redact_text(request.title)
    content, _, _ = deps.redaction.redact_text(request.content)
    deps.write_policy.check_content(title, content)
    structured, _, _ = deps.redaction.redact_value(request.structured_content)
    if structured is not None and not isinstance(structured, dict):
        raise ValidationError("structured content must be an object")
    now = deps.clock.now()
    digest = content_hash(request.tenant_id, user_id, request.category, content)
    if not request.allow_suppressed:
        suppressed = await deps.derived.find_by_hash(
            scope,
            user_id=user_id,
            category=request.category,
            content_hash=digest,
            statuses=[MemoryStatus.DELETED],
        )
        if suppressed is not None:
            raise PolicyRejectedError("dreamflow will not recreate a forgotten memory")
    else:
        suppressed = await deps.derived.find_by_hash(
            scope,
            user_id=user_id,
            category=request.category,
            content_hash=digest,
            statuses=[MemoryStatus.DELETED],
        )
        if suppressed is not None:
            await deps.derived.clear_hash(scope, suppressed.memory_id)
    duplicate = await deps.derived.find_by_hash(
        scope,
        user_id=user_id,
        category=request.category,
        content_hash=digest,
        statuses=[MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE],
    )
    if duplicate is not None:
        raise PolicyRejectedError("an active memory already has this content")
    paths = await deps.derived.existing_paths(scope, user_id)
    path = allocate_logical_path(request.category, title, paths, request.logical_path)
    memory_id = uuid4()
    version_id = uuid4()
    embedding_id = await _embed(
        deps, scope, f"{title}\n{content}", OwnerKind.MEMORY_VERSION, version_id, user_id
    )
    record = MemoryRecord(
        memory_id=memory_id,
        tenant_id=request.tenant_id,
        scope=request.scope,
        user_id=user_id,
        category=request.category,
        logical_path=path,
        status=request.status,
        current_version_number=1,
        content_hash=digest,
        created_at=now,
        updated_at=now,
        metadata=dict(request.metadata),
    )
    version = MemoryVersion(
        version_id=version_id,
        memory_id=memory_id,
        tenant_id=request.tenant_id,
        version_number=1,
        title=title,
        content=content,
        structured_content=structured,
        status=request.status,
        confidence=request.confidence,
        importance=request.importance,
        valid_from=request.valid_from or now,
        valid_until=request.valid_until,
        created_at=now,
        source=request.source,
        embedding_id=embedding_id,
        content_hash=digest,
        metadata=dict(request.metadata),
    )
    evidence = _evidence_from(scope, memory_id, version_id, request.evidence, now)
    memory = await deps.derived.insert_memory(record, version, evidence)
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=user_id,
            actor=request.actor,
            action=AuditAction.WRITE,
            resource_type="memory",
            resource_id=str(memory_id),
            occurred_at=now,
            metadata={"version": 1, "source": request.source.value},
        ),
    )
    return memory


async def update_memory(deps: Deps, request: UpdateMemoryRequest) -> Memory:
    scope = _scope(request.tenant_id, request.user_id)
    current = await deps.derived.get_memory(
        scope,
        request.memory_id,
        version_number=None,
        as_of=None,
        include_shared=True,
        include_deleted=False,
    )
    if current is None:
        raise NotFoundError("memory was not found")
    if current.tenant_id != request.tenant_id:
        from memcave.domain.errors import TenantIsolationError

        raise TenantIsolationError("memory belongs to another tenant")
    if current.version_number != request.expected_version:
        from memcave.domain.errors import VersionConflictError

        raise VersionConflictError(
            f"expected version {request.expected_version}, found {current.version_number}"
        )
    title = request.title if request.title is not None else current.title
    content = request.content if request.content is not None else current.content
    title, _, _ = deps.redaction.redact_text(title)
    content, _, _ = deps.redaction.redact_text(content)
    deps.write_policy.check_content(title, content)
    structured = (
        request.structured_content
        if request.structured_content is not None
        else current.structured_content
    )
    redacted_structured, _, _ = deps.redaction.redact_value(structured)
    if redacted_structured is not None and not isinstance(redacted_structured, dict):
        raise ValidationError("structured content must be an object")
    now = deps.clock.now()
    category = request.category or current.category
    digest = content_hash(current.tenant_id, current.user_id, category, content)
    version_id = uuid4()
    embedding_id = await _embed(
        deps, scope, f"{title}\n{content}", OwnerKind.MEMORY_VERSION, version_id, current.user_id
    )
    valid_from = request.valid_from or current.valid_from
    previous_status = MemoryStatus.SUPERSEDED
    previous_until = current.valid_until
    new_status = MemoryStatus.ACTIVE
    if request.temporal:
        if request.valid_from is None:
            raise ValidationError("temporal updates require valid_from")
        previous_status = MemoryStatus.ACTIVE
        previous_until = request.valid_from
        valid_from = request.valid_from
    version = MemoryVersion(
        version_id=version_id,
        memory_id=current.memory_id,
        tenant_id=current.tenant_id,
        version_number=current.version_number + 1,
        predecessor_version_id=current.version_id,
        title=title,
        content=content,
        structured_content=redacted_structured,
        status=new_status,
        confidence=request.confidence if request.confidence is not None else current.confidence,
        importance=request.importance if request.importance is not None else current.importance,
        valid_from=valid_from,
        valid_until=request.valid_until if request.valid_until is not None else None,
        created_at=now,
        source=MemorySource.API,
        embedding_id=embedding_id,
        content_hash=digest,
        metadata={**current.metadata, **request.metadata},
    )
    copied = [
        item.model_copy(
            update={"evidence_id": uuid4(), "version_id": version_id, "created_at": now}
        )
        for item in current.evidence
    ]
    copied.extend(_evidence_from(scope, current.memory_id, version_id, request.evidence, now))
    record = MemoryRecord(
        memory_id=current.memory_id,
        tenant_id=current.tenant_id,
        scope=current.scope,
        user_id=current.user_id,
        category=category,
        logical_path=current.logical_path,
        status=new_status,
        current_version_number=version.version_number,
        content_hash=digest,
        created_at=current.created_at,
        updated_at=now,
        metadata=version.metadata,
    )
    memory = await deps.derived.add_version(
        record,
        version,
        copied,
        previous_version_id=current.version_id,
        previous_status=previous_status,
        previous_valid_until=previous_until,
    )
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor=request.actor,
            action=AuditAction.UPDATE,
            resource_type="memory",
            resource_id=str(current.memory_id),
            occurred_at=now,
            metadata={"version": version.version_number, "temporal": request.temporal},
        ),
    )
    return memory


async def delete_memory(deps: Deps, request: DeleteMemoryRequest) -> Memory:
    scope = _scope(request.tenant_id, request.user_id)
    current = await deps.derived.get_memory(
        scope,
        request.memory_id,
        version_number=None,
        as_of=None,
        include_shared=True,
    )
    if current is None or current.user_id not in {request.user_id, None}:
        raise NotFoundError("memory was not found")
    if current.scope is MemoryScope.TENANT and current.user_id is None:
        pass
    now = deps.clock.now()
    version_id = uuid4()
    version = MemoryVersion(
        version_id=version_id,
        memory_id=current.memory_id,
        tenant_id=current.tenant_id,
        version_number=current.version_number + 1,
        predecessor_version_id=current.version_id,
        title=current.title,
        content=current.content,
        structured_content=current.structured_content,
        status=MemoryStatus.DELETED,
        confidence=current.confidence,
        importance=current.importance,
        valid_from=current.valid_from,
        valid_until=now,
        created_at=now,
        source=current.source,
        embedding_id=current.embedding_id,
        content_hash=current.content_hash,
        metadata=current.metadata,
    )
    record = MemoryRecord(
        memory_id=current.memory_id,
        tenant_id=current.tenant_id,
        scope=current.scope,
        user_id=current.user_id,
        category=current.category,
        logical_path=current.logical_path,
        status=MemoryStatus.DELETED,
        current_version_number=version.version_number,
        content_hash=current.content_hash,
        created_at=current.created_at,
        updated_at=now,
        deleted_at=now,
        metadata=current.metadata,
    )
    copied = [
        item.model_copy(
            update={"evidence_id": uuid4(), "version_id": version_id, "created_at": now}
        )
        for item in current.evidence
    ]
    memory = await deps.derived.add_version(
        record,
        version,
        copied,
        previous_version_id=None,
        previous_status=None,
        previous_valid_until=None,
    )
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor=request.actor,
            action=AuditAction.DELETE,
            resource_type="memory",
            resource_id=str(current.memory_id),
            occurred_at=now,
            metadata={"content_hash": current.content_hash},
        ),
    )
    return memory


async def fetch_memory(deps: Deps, request: FetchMemoryRequest) -> Memory:
    scope = _scope(request.tenant_id, request.user_id)
    memory = await deps.derived.get_memory(
        scope,
        request.memory_id,
        version_number=request.version_number,
        as_of=request.as_of or deps.clock.now(),
        include_shared=request.include_shared,
    )
    if memory is None:
        raise NotFoundError("memory was not found")
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor=request.actor,
            action=AuditAction.READ,
            resource_type="memory",
            resource_id=str(memory.memory_id),
            occurred_at=deps.clock.now(),
            metadata={"version": memory.version_number},
        ),
    )
    return memory


def _visible(
    memory: Memory, *, as_of: object, include_candidates: bool, min_confidence: float
) -> bool:
    from datetime import datetime

    if not isinstance(as_of, datetime):
        return False
    if memory.status in {
        MemoryStatus.DELETED,
        MemoryStatus.ARCHIVED,
        MemoryStatus.SUPERSEDED,
        MemoryStatus.STALE,
    }:
        return False
    if memory.status is MemoryStatus.CANDIDATE and not include_candidates:
        return False
    if memory.confidence < min_confidence:
        return False
    return window_contains(memory.valid_from, memory.valid_until, as_of)


async def search_memory(
    deps: Deps, request: SearchMemoryRequest
) -> tuple[list[RetrievalHit], bool, str, UUID, str | None]:
    if not 1 <= request.top_k <= 50:
        raise ValidationError("top_k must be between 1 and 50")
    if not 1 <= request.token_budget <= 8000:
        raise ValidationError("token_budget must be between 1 and 8000")
    scope = _scope(request.tenant_id, request.user_id)
    as_of = request.as_of or deps.clock.now()
    query = request.query_text
    if deps.config.redact_queries:
        query, _, _ = deps.redaction.redact_text(query)
    span = deps.tracer.start_span(
        "memcave.search",
        {"memcave.tenant_id": str(request.tenant_id)},
    )
    try:
        embedded = await deps.embeddings.embed([query])
        query_vector = embedded[0]
        vector_matches = await deps.vectors.search(scope, query_vector, limit=200, owner_kind=None)
        cosine_by_owner = {match.owner_id: match.cosine for match in vector_matches}
        derived_parts: list[tuple[RetrievalHit, object]] = []
        episodic_parts: list[tuple[RetrievalHit, object]] = []
        unavailable: list[str] = []
        if request.memory_kinds in {MemoryKind.BOTH, MemoryKind.DERIVED}:
            memories = await deps.derived.list_memories(
                scope,
                as_of=as_of,
                include_shared=request.include_shared,
                include_candidates=request.include_candidates,
                categories=request.categories,
                min_confidence=request.min_confidence,
                user_id=request.user_id,
            )
            scored: list[tuple[Memory, ScoreParts]] = []
            for memory in memories:
                if not _visible(
                    memory,
                    as_of=as_of,
                    include_candidates=request.include_candidates,
                    min_confidence=request.min_confidence,
                ):
                    continue
                if memory.tenant_id != request.tenant_id:
                    continue
                cosine = cosine_by_owner.get(memory.version_id)
                if cosine is None and memory.embedding_id is not None:
                    cosine = _direct_cosine(query_vector, cosine)
                parts = score_parts(
                    vector=cosine,
                    lexical=lexical_score(query, f"{memory.title}\n{memory.content}"),
                    recency=recency_score(
                        memory.updated_at, as_of, deps.config.recency_half_life_days
                    ),
                    importance=memory.importance,
                    confidence=memory.confidence,
                    weights=deps.config.derived_weights,
                )
                scored.append((memory, parts))
            min_max([part for _, part in scored])
            for memory, parts in scored:
                derived_parts.append((_hit_from_memory(memory, parts), parts))
                unavailable.extend(parts.unavailable)
        if request.memory_kinds in {MemoryKind.BOTH, MemoryKind.EPISODIC}:
            events, _cursor = await deps.episodic.list_events(
                scope,
                session_id=None,
                event_type=None,
                processing_state=None,
                start=None,
                end=None,
                limit=200,
                cursor=None,
                user_id=request.user_id,
            )
            event_scored: list[tuple[SessionEvent, str, ScoreParts]] = []
            for event in events:
                if event.tenant_id != request.tenant_id or event.user_id != request.user_id:
                    continue
                if not deps.retention.allows(event.occurred_at, as_of):
                    continue
                text = event_search_text(event.payload)
                if not text:
                    continue
                parts = score_parts(
                    vector=cosine_by_owner.get(event.event_id),
                    lexical=lexical_score(query, text),
                    recency=recency_score(
                        event.occurred_at, as_of, deps.config.recency_half_life_days
                    ),
                    importance=episodic_importance(event.event_type),
                    confidence=None,
                    weights=deps.config.episodic_weights,
                )
                event_scored.append((event, text, parts))
            min_max([part for _, _, part in event_scored])
            for event, text, parts in event_scored:
                episodic_parts.append((_hit_from_event(event, text, parts), parts))
                unavailable.extend(parts.unavailable)
        ranked = [hit for hit, _part in derived_parts + episodic_parts]
        ranked.sort(
            key=lambda hit: (
                -hit.score,
                -hit.score_components.get("importance", 0.0),
                hit.title,
                hit.content,
                str(hit.source_id),
            )
        )
        reranker_error = None
        try:
            reranked = await deps.reranker.rerank(query, ranked)
            ranked = list(reranked)
        except Exception as exc:  # noqa: BLE001 - reranker failures keep fused order
            reranker_error = type(exc).__name__
        selected: list[RetrievalHit] = []
        used = 0
        truncated = False
        for hit in ranked:
            if len(selected) >= request.top_k:
                truncated = True
                break
            if used + hit.token_estimate > request.token_budget and selected:
                truncated = True
                break
            if used + hit.token_estimate > request.token_budget and not selected:
                selected.append(hit)
                truncated = True
                break
            selected.append(hit)
            used += hit.token_estimate
        for index, hit in enumerate(selected, start=1):
            hit.rank = index
        context = render_context(selected)
        redacted_query, _, _ = deps.redaction.redact_text(request.query_text)
        audit_id = uuid4()
        await _audit(
            deps,
            AuditEvent(
                audit_id=audit_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                actor=request.actor,
                action=AuditAction.READ,
                resource_type="search",
                resource_id=None,
                occurred_at=deps.clock.now(),
                metadata={
                    "query_hash": query_fingerprint(request.query_text),
                    "query_redacted": redacted_query[:500],
                    "hit_ids": [str(hit.hit_id) for hit in selected],
                },
            ),
        )
        return selected, truncated, context, audit_id, reranker_error
    finally:
        span.end()


def _direct_cosine(query_vector: list[float], cosine: float | None) -> float | None:
    del query_vector
    return cosine


def _hit_from_memory(memory: Memory, parts: object) -> RetrievalHit:
    if not isinstance(parts, ScoreParts):
        raise ValidationError("internal score was not produced")
    content = memory.content
    return RetrievalHit(
        hit_id=uuid4(),
        kind=MemoryKind.DERIVED,
        tenant_id=memory.tenant_id,
        user_id=memory.user_id,
        scope=memory.scope,
        source_id=memory.memory_id,
        title=memory.title,
        content=content,
        category=memory.category.value,
        source=memory.source.value,
        structured_content=memory.structured_content,
        score=parts.normalized,
        score_components={
            **parts.components,
            "fused": parts.fused,
            "normalized": parts.normalized,
            **{f"weight_{name}": value for name, value in parts.weights.items()},
        },
        token_estimate=estimate_tokens(f"{memory.title}\n{content}"),
        evidence_ids=[item.evidence_id for item in memory.evidence],
        explanation="derived_weighted_sum",
        occurred_at=memory.updated_at,
    )


def _hit_from_event(event: SessionEvent, text: str, parts: object) -> RetrievalHit:
    if not isinstance(parts, ScoreParts):
        raise ValidationError("internal score was not produced")
    return RetrievalHit(
        hit_id=uuid4(),
        kind=MemoryKind.EPISODIC,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        scope=MemoryScope.USER,
        source_id=event.event_id,
        title=event.event_type.value,
        content=text,
        category=event.event_type.value,
        source="episode",
        score=parts.normalized,
        score_components={
            **parts.components,
            "fused": parts.fused,
            "normalized": parts.normalized,
            "importance": episodic_importance(event.event_type),
            **{f"weight_{name}": value for name, value in parts.weights.items()},
        },
        token_estimate=estimate_tokens(text),
        explanation="episodic_weighted_sum",
        occurred_at=event.occurred_at,
    )


async def list_candidate_memories(deps: Deps, tenant_id: UUID, user_id: UUID) -> list[Memory]:
    scope = _scope(tenant_id, user_id)
    as_of = deps.clock.now()
    memories = await deps.derived.list_memories(
        scope,
        as_of=as_of,
        include_shared=False,
        include_candidates=True,
        categories=None,
        min_confidence=0.0,
        user_id=user_id,
    )
    return [
        memory
        for memory in memories
        if memory.status is MemoryStatus.CANDIDATE
        and memory.user_id == user_id
        and _visible(memory, as_of=as_of, include_candidates=True, min_confidence=0.0)
    ]


async def load_review_memory(deps: Deps, tenant_id: UUID, user_id: UUID, memory_id: UUID) -> Memory:
    """Load the current version, including a candidate that search would hide."""
    scope = _scope(tenant_id, user_id)
    memory = await deps.derived.get_memory(
        scope,
        memory_id,
        version_number=None,
        as_of=None,
        include_shared=False,
    )
    if memory is None or memory.user_id != user_id:
        raise NotFoundError("memory was not found")
    return memory


async def accept_candidate(deps: Deps, tenant_id: UUID, user_id: UUID, memory_id: UUID) -> Memory:
    current = await load_review_memory(deps, tenant_id, user_id, memory_id)
    if current.status is not MemoryStatus.CANDIDATE:
        raise ValidationError("only a candidate memory can be accepted")
    return await update_memory(
        deps,
        UpdateMemoryRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_id=memory_id,
            expected_version=current.version_number,
            title=current.title,
            content=current.content,
            actor="review",
        ),
    )


async def reject_candidate(deps: Deps, tenant_id: UUID, user_id: UUID, memory_id: UUID) -> Memory:
    current = await load_review_memory(deps, tenant_id, user_id, memory_id)
    if current.status is not MemoryStatus.CANDIDATE:
        raise ValidationError("only a candidate memory can be rejected")
    return await delete_memory(
        deps,
        DeleteMemoryRequest(
            tenant_id=tenant_id, user_id=user_id, memory_id=memory_id, actor="review"
        ),
    )


async def memories_for_index(
    deps: Deps, tenant_id: UUID, user_id: UUID, as_of: object, include_shared: bool
) -> list[Memory]:
    from datetime import datetime

    if not isinstance(as_of, datetime):
        raise ValidationError("as_of must be a datetime")
    scope = _scope(tenant_id, user_id)
    memories = await deps.derived.list_memories(
        scope,
        as_of=as_of,
        include_shared=include_shared,
        include_candidates=False,
        categories=None,
        min_confidence=0.0,
        user_id=user_id,
    )
    return [
        memory
        for memory in memories
        if _visible(memory, as_of=as_of, include_candidates=False, min_confidence=0.0)
    ]


def index_markdown(memories: list[Memory]) -> str:
    return render_index(memories)


async def export_markdown(deps: Deps, request: ExportMarkdownRequest) -> tuple[str, list[str], str]:
    as_of = request.as_of or deps.clock.now()
    memories = await memories_for_index(
        deps, request.tenant_id, request.user_id, as_of, request.include_shared
    )
    markdown = render_index(memories)
    root, files = await asyncio.to_thread(_write_export, request.output_dir, memories, markdown)
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor=request.actor,
            action=AuditAction.EXPORT,
            resource_type="memory_index",
            resource_id=None,
            occurred_at=deps.clock.now(),
            metadata={"files": len(files)},
        ),
    )
    return root, files, markdown


def _write_export(output_dir: str, memories: list[Memory], markdown: str) -> tuple[str, list[str]]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    index_path = _safe_target(root, "memory.md")
    index_path.write_text(markdown, encoding="utf-8")
    written.append(str(index_path))
    for memory in memories:
        target = _safe_target(root, f"{memory.logical_path}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"# {memory.title}\n\n"
            f"- category: {memory.category.value}\n"
            f"- status: {memory.status.value}\n"
            f"- confidence: {memory.confidence:.2f}\n\n"
            f"{memory.content}\n"
        )
        if memory.structured_content is not None:
            body += f"\n```json\n{json.dumps(memory.structured_content, indent=2)}\n```\n"
        target.write_text(body, encoding="utf-8")
        written.append(str(target))
    return str(root), written


def _safe_target(root: Path, relative: str) -> Path:
    if "\\" in relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValidationError("export path escapes the output directory")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValidationError("export path escapes the output directory")
    return target


async def erase_user(
    deps: Deps, tenant_id: UUID, user_id: UUID, actor: str
) -> tuple[EraseCounts, UUID]:
    scope = _scope(tenant_id, user_id)
    events, _cursor = await deps.episodic.list_events(
        scope,
        session_id=None,
        event_type=None,
        processing_state=None,
        start=None,
        end=None,
        limit=10_000,
        cursor=None,
        user_id=user_id,
    )
    owner_ids = [event.event_id for event in events]
    for memory_id in await deps.derived.list_memory_ids(scope, user_id):
        versions = await deps.derived.list_versions(scope, memory_id)
        owner_ids.extend(version.version_id for version in versions)
    removed_embeddings = await deps.vectors.delete_owners(scope, owner_ids)
    derived_counts = await deps.derived.erase_user(scope)
    sessions, event_count = await deps.episodic.erase_user(scope)
    counts = derived_counts.model_copy(
        update={
            "sessions": sessions,
            "events": event_count,
            "embeddings": removed_embeddings,
        }
    )
    audit_id = uuid4()
    await _audit(
        deps,
        AuditEvent(
            audit_id=audit_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor=actor,
            action=AuditAction.ERASE,
            resource_type="user",
            resource_id=str(user_id),
            occurred_at=deps.clock.now(),
            metadata=counts.model_dump(),
        ),
    )
    return counts, audit_id


async def append_event(
    deps: Deps, request: AppendEventRequest
) -> tuple[SessionEvent, Memory | None]:
    scope = _scope(request.tenant_id, request.user_id)
    session = await deps.episodic.get_session(scope, request.session_id)
    if session is None:
        raise NotFoundError("session was not found")
    if session.tenant_id != request.tenant_id or session.user_id != request.user_id:
        from memcave.domain.errors import TenantIsolationError

        raise TenantIsolationError("session is outside the caller scope")
    redacted_payload, _, _ = deps.redaction.redact_value(request.payload)
    if not isinstance(redacted_payload, dict):
        raise ValidationError("event payload must be an object")
    now = request.occurred_at or deps.clock.now()
    if now.tzinfo is None:
        raise ValidationError("occurred_at must be timezone-aware")
    event = SessionEvent(
        event_id=uuid4(),
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        session_id=request.session_id,
        sequence=0,
        event_type=request.event_type,
        occurred_at=now,
        payload=redacted_payload,
        processing_state=ProcessingState.UNPROCESSED,
        metadata=dict(request.metadata),
    )
    stored = await deps.episodic.append_event(event)
    text = event_search_text(stored.payload)
    if text:
        await _embed(deps, scope, text, OwnerKind.EVENT, stored.event_id, request.user_id)
    memory: Memory | None = None
    try:
        if request.event_type is EventType.EXPLICIT_MEMORY_INSTRUCTION:
            raw = stored.payload.get("raw_text")
            if isinstance(raw, str) and immediate_instruction(raw):
                memory = await _apply_explicit(deps, scope, stored, request.actor)
                await deps.episodic.set_processing_state(
                    scope, [stored.event_id], ProcessingState.PROCESSED
                )
                stored = stored.model_copy(update={"processing_state": ProcessingState.PROCESSED})
    except Exception:
        await deps.episodic.delete_event(scope, stored.event_id)
        await deps.vectors.delete_owners(scope, [stored.event_id])
        raise
    await _audit(
        deps,
        AuditEvent(
            audit_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor=request.actor,
            action=AuditAction.WRITE,
            resource_type="session_event",
            resource_id=str(stored.event_id),
            occurred_at=deps.clock.now(),
            metadata={"event_type": request.event_type.value},
        ),
    )
    return stored, memory


async def _apply_explicit(
    deps: Deps,
    scope: TenantScope,
    event: SessionEvent,
    actor: str,
) -> Memory | None:
    try:
        payload = InstructionPayload.model_validate(event.payload)
    except Exception as exc:
        raise ValidationError("explicit instruction payload is invalid") from exc
    parsed = parse_instruction(payload)
    evidence = [
        EvidenceInput(
            session_id=event.session_id, event_id=event.event_id, quote=parsed.content[:240]
        )
    ]
    if parsed.instruction_type is InstructionType.FORGET:
        return await _forget(deps, scope, event, parsed, actor)
    if parsed.instruction_type is InstructionType.CORRECT:
        return await _correct(deps, scope, event, parsed, evidence, actor)
    if parsed.instruction_type is InstructionType.TEMPORAL_CHANGE and not parsed.ambiguous:
        return await _temporal(deps, scope, event, parsed, evidence, actor)
    if parsed.category is None:
        raise ValidationError("instruction did not resolve a category")
    return await add_memory(
        deps,
        AddMemoryRequest(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            category=parsed.category,
            title=parsed.title,
            content=parsed.content,
            structured_content=parsed.structured_content,
            confidence=parsed.confidence,
            importance=parsed.importance,
            valid_from=parsed.valid_from,
            valid_until=parsed.valid_until,
            evidence=evidence,
            source=MemorySource.EXPLICIT_INSTRUCTION,
            status=parsed.status,
            allow_suppressed=True,
            actor=actor,
        ),
    )


async def _forget(
    deps: Deps,
    scope: TenantScope,
    event: SessionEvent,
    parsed: object,
    actor: str,
) -> Memory | None:
    from memcave.domain.enums import MemoryCategory
    from memcave.domain.models import ParsedInstruction

    if not isinstance(parsed, ParsedInstruction):
        raise ValidationError("instruction was not parsed")
    category = parsed.category or MemoryCategory.DOMAIN_KNOWLEDGE
    memories = await deps.derived.list_user_memories(scope, event.user_id)
    active = [item for item in memories if item.status is MemoryStatus.ACTIVE]
    if parsed.target_memory_id is not None:
        return await delete_memory(
            deps,
            DeleteMemoryRequest(
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                memory_id=parsed.target_memory_id,
                actor=actor,
            ),
        )
    needle = (parsed.forget_needle or "").casefold()
    matches = [
        item
        for item in active
        if needle and (needle in item.title.casefold() or needle in item.content.casefold())
    ]
    if len(matches) == 1:
        return await delete_memory(
            deps,
            DeleteMemoryRequest(
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                memory_id=matches[0].memory_id,
                actor=actor,
            ),
        )
    return await add_memory(
        deps,
        AddMemoryRequest(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            category=category,
            title=parsed.title,
            content=parsed.content,
            confidence=0.4,
            importance=0.3,
            source=MemorySource.EXPLICIT_INSTRUCTION,
            status=MemoryStatus.CANDIDATE,
            evidence=[
                EvidenceInput(
                    session_id=event.session_id, event_id=event.event_id, quote=parsed.content[:240]
                )
            ],
            actor=actor,
        ),
    )


async def _correct(
    deps: Deps,
    scope: TenantScope,
    event: SessionEvent,
    parsed: object,
    evidence: list[EvidenceInput],
    actor: str,
) -> Memory:
    from memcave.domain.enums import MemoryCategory
    from memcave.domain.models import ParsedInstruction

    if not isinstance(parsed, ParsedInstruction) or parsed.category is None:
        raise ValidationError("correction did not resolve")
    target = await _match_target(deps, scope, event.user_id, parsed, marker="instead of")
    if target is None:
        return await add_memory(
            deps,
            AddMemoryRequest(
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                category=parsed.category or MemoryCategory.CORRECTION,
                title=parsed.title,
                content=parsed.content,
                confidence=parsed.confidence,
                importance=parsed.importance,
                source=MemorySource.EXPLICIT_INSTRUCTION,
                evidence=evidence,
                actor=actor,
            ),
        )
    return await update_memory(
        deps,
        UpdateMemoryRequest(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            memory_id=target.memory_id,
            expected_version=target.version_number,
            title=parsed.title,
            content=parsed.content,
            category=MemoryCategory.CORRECTION,
            confidence=parsed.confidence,
            importance=parsed.importance,
            evidence=evidence,
            actor=actor,
        ),
    )


async def _temporal(
    deps: Deps,
    scope: TenantScope,
    event: SessionEvent,
    parsed: object,
    evidence: list[EvidenceInput],
    actor: str,
) -> Memory:
    from memcave.domain.models import ParsedInstruction

    if (
        not isinstance(parsed, ParsedInstruction)
        or parsed.category is None
        or parsed.valid_from is None
    ):
        raise ValidationError("temporal instruction did not resolve")
    target = await _match_target(deps, scope, event.user_id, parsed, marker="as of")
    if target is None:
        return await add_memory(
            deps,
            AddMemoryRequest(
                tenant_id=event.tenant_id,
                user_id=event.user_id,
                category=parsed.category,
                title=parsed.title,
                content=parsed.content,
                confidence=parsed.confidence,
                importance=parsed.importance,
                valid_from=parsed.valid_from,
                source=MemorySource.EXPLICIT_INSTRUCTION,
                evidence=evidence,
                actor=actor,
            ),
        )
    return await update_memory(
        deps,
        UpdateMemoryRequest(
            tenant_id=event.tenant_id,
            user_id=event.user_id,
            memory_id=target.memory_id,
            expected_version=target.version_number,
            title=parsed.title,
            content=parsed.content,
            confidence=parsed.confidence,
            importance=parsed.importance,
            valid_from=parsed.valid_from,
            temporal=True,
            evidence=evidence,
            actor=actor,
        ),
    )


async def _match_target(
    deps: Deps,
    scope: TenantScope,
    user_id: UUID,
    parsed: object,
    marker: str,
) -> Memory | None:
    from memcave.domain.models import ParsedInstruction

    if not isinstance(parsed, ParsedInstruction):
        return None
    if parsed.target_memory_id is not None:
        return await deps.derived.get_memory(
            scope,
            parsed.target_memory_id,
            version_number=None,
            as_of=None,
            include_shared=True,
        )
    memories = await deps.derived.list_user_memories(scope, user_id)
    active = [item for item in memories if item.status is MemoryStatus.ACTIVE]
    if marker == "instead of" and "instead of" in parsed.content.casefold():
        needle = parsed.content.casefold().split("instead of", 1)[1].strip(" .")
        matches = [item for item in active if needle and needle in item.content.casefold()]
        if len(matches) == 1:
            return matches[0]
    if len(active) == 1 and marker == "as of":
        return active[0]
    return None
