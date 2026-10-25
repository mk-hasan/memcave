"""Dreamflow stages and the idempotent pipeline."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel

from memcave.domain.api import (
    AddMemoryRequest,
    EvidenceInput,
    RunDreamflowRequest,
    UpdateMemoryRequest,
)
from memcave.domain.enums import (
    AuditAction,
    CandidateStatus,
    ConflictResolution,
    ConflictType,
    DreamflowState,
    EventType,
    ItemStatus,
    MemoryCategory,
    MemorySource,
    MemoryStatus,
    ProcessingState,
    ProposedAction,
    ResolutionStrategy,
    RunStatus,
)
from memcave.domain.errors import PolicyRejectedError
from memcave.domain.models import (
    AuditEvent,
    DreamflowItem,
    DreamflowRun,
    Memory,
    MemoryCandidate,
    MemoryConflict,
    ModelRequest,
    Session,
    SessionEvent,
    TenantScope,
)
from memcave.dreamflow.schemas import ExtractionBatch
from memcave.ports.protocols import ModelProvider
from memcave.services.instructions import classify_text
from memcave.services.operations import Deps, add_memory, memories_for_index, update_memory
from memcave.services.render import render_index
from memcave.services.text import content_hash, event_search_text, subject_key

PIPELINE_VERSION = "0.1"
_CONTRADICTION = ("instead of", "no longer", "do not", "not ", "that was wrong")


class SessionSelector:
    async def select(self, deps: Deps, request: RunDreamflowRequest) -> list[Session]:
        scope = TenantScope(tenant_id=request.tenant_id, user_id=request.user_id)
        return await deps.episodic.eligible_sessions(
            scope, now=deps.clock.now(), max_age=deps.retention.max_age()
        )


class CandidateExtractor:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    async def extract(
        self,
        deps: Deps,
        run: DreamflowRun,
        session: Session,
        *,
        use_model: bool,
    ) -> list[MemoryCandidate]:
        scope = TenantScope(tenant_id=session.tenant_id, user_id=session.user_id)
        events, _cursor = await deps.episodic.list_events(
            scope,
            session_id=session.session_id,
            event_type=None,
            processing_state=ProcessingState.UNPROCESSED,
            start=None,
            end=None,
            limit=500,
            cursor=None,
            user_id=session.user_id,
        )
        found: list[MemoryCandidate] = []
        for event in events:
            candidate = _from_event(run, session, event)
            if candidate is not None:
                found.append(candidate)
        if use_model and self.model is not None:
            found.extend(await self._from_model(deps, run, session, events))
        return found

    async def _from_model(
        self,
        deps: Deps,
        run: DreamflowRun,
        session: Session,
        events: list[SessionEvent],
    ) -> list[MemoryCandidate]:
        if self.model is None:
            return []
        transcript = "\n".join(
            f"{event.event_id} {event.event_type.value}: {event_search_text(event.payload)}"
            for event in events
        )
        request = ModelRequest(
            task="extract",
            system_prompt="Return JSON matching the extraction batch schema.",
            user_prompt=transcript,
            temperature=0.0,
            seed=0,
        )
        parsed = await _complete_validated(self.model, request, ExtractionBatch)
        if parsed is None:
            return []
        event_ids = {event.event_id for event in events}
        candidates: list[MemoryCandidate] = []
        for item in parsed.candidates:
            if any(event_id not in event_ids for event_id in item.evidence_event_ids):
                continue
            candidates.append(
                MemoryCandidate(
                    candidate_id=uuid4(),
                    dreamflow_run_id=run.run_id,
                    tenant_id=session.tenant_id,
                    user_id=session.user_id,
                    session_id=session.session_id,
                    category=item.category,
                    title=item.title,
                    content=item.content,
                    structured_content=_object_dict(item.structured_content),
                    confidence=item.confidence,
                    importance=item.importance,
                    valid_from=item.valid_from,
                    valid_until=item.valid_until,
                    evidence_event_ids=item.evidence_event_ids,
                    status=CandidateStatus.EXTRACTED,
                )
            )
        return candidates


class CandidateValidator:
    def validate(
        self, session: Session, candidates: list[MemoryCandidate]
    ) -> list[MemoryCandidate]:
        kept: list[MemoryCandidate] = []
        for candidate in candidates:
            if not candidate.content.strip() or not candidate.evidence_event_ids:
                candidate.status = CandidateStatus.REJECTED
                candidate.rejection_reason = "missing content or evidence"
                kept.append(candidate)
                continue
            if candidate.session_id != session.session_id:
                candidate.status = CandidateStatus.REJECTED
                candidate.rejection_reason = "evidence is outside the session"
                kept.append(candidate)
                continue
            kept.append(candidate)
        return kept


class CandidateClassifier:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    async def classify(
        self, candidates: list[MemoryCandidate], *, use_model: bool
    ) -> list[MemoryCandidate]:
        for candidate in candidates:
            if candidate.status is CandidateStatus.REJECTED:
                continue
            ruled = classify_text(candidate.content, candidate.structured_content)
            candidate.category = ruled if candidate.model_category is None else candidate.category
            if use_model and self.model is not None:
                advice = await _complete_validated(
                    self.model,
                    ModelRequest(
                        task="classify",
                        system_prompt="Return JSON with a category field.",
                        user_prompt=candidate.content,
                        temperature=0.0,
                        seed=0,
                    ),
                    ExtractionBatch,
                )
                if advice is not None and advice.candidates:
                    model_category = advice.candidates[0].category
                    candidate.model_category = model_category
                    if model_category is not ruled:
                        candidate.status = CandidateStatus.NEEDS_REVIEW
                        candidate.category = ruled
            if candidate.structured_content and _looks_like_workflow(candidate.structured_content):
                candidate.category = MemoryCategory.WORKFLOW
        return candidates


class DuplicateDetector:
    async def mark(self, deps: Deps, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        for candidate in candidates:
            if candidate.status in {CandidateStatus.REJECTED, CandidateStatus.NEEDS_REVIEW}:
                continue
            scope = TenantScope(tenant_id=candidate.tenant_id, user_id=candidate.user_id)
            digest = content_hash(
                candidate.tenant_id, candidate.user_id, candidate.category, candidate.content
            )
            existing = await deps.derived.find_by_hash(
                scope,
                user_id=candidate.user_id,
                category=candidate.category,
                content_hash=digest,
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE],
            )
            if existing is None:
                existing = await _cosine_duplicate(deps, scope, candidate)
            if existing is not None and not _contradicts(candidate.content, existing.content):
                candidate.status = CandidateStatus.DUPLICATE
                candidate.proposed_action = ProposedAction.MERGE
                candidate.target_memory_id = existing.memory_id
        return candidates


class ConflictDetector:
    async def mark(self, deps: Deps, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        for candidate in candidates:
            if candidate.status in {CandidateStatus.REJECTED, CandidateStatus.DUPLICATE}:
                continue
            scope = TenantScope(tenant_id=candidate.tenant_id, user_id=candidate.user_id)
            memories = await deps.derived.list_user_memories(scope, candidate.user_id)
            active = [item for item in memories if item.status is MemoryStatus.ACTIVE]
            target = _conflict_target(candidate, active)
            if target is None:
                continue
            candidate.status = CandidateStatus.CONFLICT
            candidate.target_memory_id = target.memory_id
            candidate.conflict_type = _conflict_type(candidate, target)
            if candidate.confidence < deps.config.review_confidence_threshold:
                candidate.status = CandidateStatus.NEEDS_REVIEW
                candidate.resolution_strategy = ResolutionStrategy.RULE
        return candidates


class ConsolidationPlanner:
    def plan(self, deps: Deps, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        threshold = deps.config.auto_resolve_confidence_threshold
        review = deps.config.review_confidence_threshold
        for candidate in candidates:
            if candidate.status is CandidateStatus.REJECTED:
                candidate.proposed_action = ProposedAction.SKIP
                continue
            if candidate.status is CandidateStatus.DUPLICATE:
                candidate.proposed_action = ProposedAction.MERGE
                continue
            if candidate.status is CandidateStatus.NEEDS_REVIEW or candidate.confidence < review:
                candidate.status = CandidateStatus.NEEDS_REVIEW
                candidate.proposed_action = ProposedAction.SKIP
                continue
            if candidate.status is CandidateStatus.CONFLICT:
                if (
                    candidate.conflict_type is ConflictType.TEMPORAL_CHANGE
                    and candidate.confidence >= threshold
                    and candidate.valid_from is not None
                ):
                    candidate.proposed_action = ProposedAction.NEW_VERSION
                elif candidate.confidence >= review:
                    candidate.proposed_action = ProposedAction.SUPERSEDE
                else:
                    candidate.proposed_action = ProposedAction.SKIP
                    candidate.status = CandidateStatus.NEEDS_REVIEW
                continue
            candidate.proposed_action = ProposedAction.CREATE
            candidate.status = CandidateStatus.ACCEPTED
        return candidates


class MemoryWriter:
    async def apply(
        self, deps: Deps, session: Session, candidates: list[MemoryCandidate]
    ) -> list[str]:
        notes: list[str] = []
        scope = TenantScope(tenant_id=session.tenant_id, user_id=session.user_id)
        for candidate in candidates:
            await deps.derived.save_candidate(candidate)
            if candidate.proposed_action is ProposedAction.SKIP:
                notes.append(f"review:{candidate.candidate_id}")
                if candidate.status is CandidateStatus.NEEDS_REVIEW and candidate.content.strip():
                    await self._create(deps, candidate, MemoryStatus.CANDIDATE)
                continue
            if candidate.proposed_action is ProposedAction.MERGE and candidate.target_memory_id:
                await self._merge(deps, scope, candidate)
                notes.append(f"merge:{candidate.target_memory_id}")
                continue
            if candidate.proposed_action in {ProposedAction.SUPERSEDE, ProposedAction.NEW_VERSION}:
                await self._version(deps, candidate)
                notes.append(f"version:{candidate.target_memory_id}")
                continue
            if candidate.proposed_action is ProposedAction.CREATE:
                try:
                    created = await self._create(deps, candidate, MemoryStatus.ACTIVE)
                except PolicyRejectedError:
                    notes.append(
                        "suppressed:"
                        + content_hash(
                            candidate.tenant_id,
                            candidate.user_id,
                            candidate.category,
                            candidate.content,
                        )
                    )
                    continue
                notes.append(f"create:{created.memory_id}")
        await deps.episodic.set_processing_state(
            scope,
            [event_id for candidate in candidates for event_id in candidate.evidence_event_ids],
            ProcessingState.PROCESSED,
        )
        return notes

    async def _create(self, deps: Deps, candidate: MemoryCandidate, status: MemoryStatus) -> Memory:
        return await add_memory(
            deps,
            AddMemoryRequest(
                tenant_id=candidate.tenant_id,
                user_id=candidate.user_id,
                category=candidate.category,
                title=candidate.title or candidate.content[:80],
                content=candidate.content,
                structured_content=candidate.structured_content,
                confidence=candidate.confidence,
                importance=candidate.importance,
                valid_from=candidate.valid_from,
                valid_until=candidate.valid_until,
                source=MemorySource.DREAMFLOW,
                status=status,
                allow_suppressed=False,
                evidence=_evidence(candidate),
                actor="dreamflow",
            ),
        )

    async def _merge(self, deps: Deps, scope: TenantScope, candidate: MemoryCandidate) -> None:
        if candidate.target_memory_id is None:
            return
        memory = await deps.derived.get_memory(
            scope,
            candidate.target_memory_id,
            version_number=None,
            as_of=None,
            include_shared=True,
        )
        if memory is None:
            return
        for item in _evidence(candidate):
            from memcave.domain.models import MemoryEvidence

            await deps.derived.add_evidence(
                MemoryEvidence(
                    evidence_id=uuid4(),
                    memory_id=memory.memory_id,
                    version_id=memory.version_id,
                    tenant_id=candidate.tenant_id,
                    session_id=item.session_id,
                    event_id=item.event_id,
                    quote=item.quote,
                    weight=item.weight,
                    created_at=deps.clock.now(),
                )
            )

    async def _version(self, deps: Deps, candidate: MemoryCandidate) -> None:
        if candidate.target_memory_id is None:
            await self._create(deps, candidate, MemoryStatus.ACTIVE)
            return
        scope = TenantScope(tenant_id=candidate.tenant_id, user_id=candidate.user_id)
        current = await deps.derived.get_memory(
            scope,
            candidate.target_memory_id,
            version_number=None,
            as_of=None,
            include_shared=True,
        )
        if current is None:
            await self._create(deps, candidate, MemoryStatus.ACTIVE)
            return
        temporal = candidate.proposed_action is ProposedAction.NEW_VERSION
        await update_memory(
            deps,
            UpdateMemoryRequest(
                tenant_id=candidate.tenant_id,
                user_id=candidate.user_id,
                memory_id=current.memory_id,
                expected_version=current.version_number,
                title=candidate.title,
                content=candidate.content,
                structured_content=candidate.structured_content,
                category=candidate.category,
                confidence=candidate.confidence,
                importance=candidate.importance,
                valid_from=candidate.valid_from,
                valid_until=candidate.valid_until,
                temporal=temporal,
                evidence=_evidence(candidate),
                actor="dreamflow",
            ),
        )
        await deps.derived.save_conflict(
            MemoryConflict(
                conflict_id=uuid4(),
                tenant_id=candidate.tenant_id,
                memory_id=current.memory_id,
                candidate_id=candidate.candidate_id,
                conflict_type=candidate.conflict_type or ConflictType.CONTRADICTION,
                resolution=ConflictResolution.VERSIONED,
                resolution_strategy=candidate.resolution_strategy,
                notes="versioned by rule; automatic resolution is not certain",
                created_at=deps.clock.now(),
            )
        )


class IndexRegenerator:
    async def render(self, deps: Deps, request: RunDreamflowRequest) -> str:
        if request.user_id is None:
            return "# Memory index\n"
        memories = await memories_for_index(
            deps, request.tenant_id, request.user_id, deps.clock.now(), True
        )
        return render_index(memories)


class RunReporter:
    def finish(
        self,
        run: DreamflowRun,
        *,
        notes: list[str],
        failures: list[str],
        index_markdown: str,
        candidates: list[MemoryCandidate],
    ) -> DreamflowRun:
        rejected = [item for item in candidates if item.status is CandidateStatus.REJECTED]
        review = [item for item in candidates if item.status is CandidateStatus.NEEDS_REVIEW]
        if failures and notes:
            status = RunStatus.PARTIAL
        elif failures:
            status = RunStatus.FAILED
        else:
            status = RunStatus.SUCCEEDED
        return run.model_copy(
            update={
                "status": status,
                "finished_at": run.finished_at,
                "error": "; ".join(failures) or None,
                "report": {
                    "notes": notes,
                    "failures": failures,
                    "rejected": [item.rejection_reason for item in rejected],
                    "needs_review": [str(item.candidate_id) for item in review],
                    "index_markdown": index_markdown,
                    "automatic_resolution_is_uncertain": True,
                },
            }
        )


class DreamflowPipeline:
    def __init__(self, model: ModelProvider | None = None) -> None:
        self.selector = SessionSelector()
        self.extractor = CandidateExtractor(model)
        self.validator = CandidateValidator()
        self.classifier = CandidateClassifier(model)
        self.duplicates = DuplicateDetector()
        self.conflicts = ConflictDetector()
        self.planner = ConsolidationPlanner()
        self.writer = MemoryWriter()
        self.index = IndexRegenerator()
        self.reporter = RunReporter()

    async def run(self, deps: Deps, request: RunDreamflowRequest) -> DreamflowRun:
        scope = TenantScope(tenant_id=request.tenant_id, user_id=request.user_id)
        sessions = await self.selector.select(deps, request)
        key = request.idempotency_key or _idempotency_key(request, sessions)
        existing = await deps.derived.get_run_by_key(scope, key)
        if existing is not None and existing.status is RunStatus.SUCCEEDED:
            return existing
        now = deps.clock.now()
        run = DreamflowRun(
            run_id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            status=RunStatus.RUNNING,
            pipeline_version=PIPELINE_VERSION,
            idempotency_key=key,
            started_at=now,
        )
        await deps.derived.save_run(run)
        notes: list[str] = []
        failures: list[str] = []
        all_candidates: list[MemoryCandidate] = []
        for session in sessions:
            try:
                produced, written = await self._session(
                    deps, run, session, use_model=request.use_model
                )
                all_candidates.extend(produced)
                notes.extend(written)
                notes.append(f"session:{session.session_id}")
                session_row = session.model_copy(
                    update={
                        "dreamflow_state": DreamflowState.PROCESSED,
                        "dreamflow_run_id": run.run_id,
                    }
                )
                await deps.episodic.save_session(session_row)
            except Exception as exc:  # noqa: BLE001 - one session must not abort the run
                failures.append(f"{session.session_id}: {exc}")
                await deps.episodic.save_session(
                    session.model_copy(
                        update={
                            "dreamflow_state": DreamflowState.FAILED,
                            "dreamflow_run_id": run.run_id,
                        }
                    )
                )
                await deps.derived.save_item(
                    DreamflowItem(
                        item_id=uuid4(),
                        run_id=run.run_id,
                        tenant_id=session.tenant_id,
                        session_id=session.session_id,
                        stage="session",
                        status=ItemStatus.FAILED,
                        error=str(exc),
                        created_at=deps.clock.now(),
                        updated_at=deps.clock.now(),
                    )
                )
        index_markdown = await self.index.render(deps, request)
        finished = self.reporter.finish(
            run.model_copy(update={"finished_at": deps.clock.now()}),
            notes=notes,
            failures=failures,
            index_markdown=index_markdown,
            candidates=all_candidates,
        )
        await deps.derived.save_run(finished)
        await deps.sink.emit(
            AuditEvent(
                audit_id=uuid4(),
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                actor=request.actor,
                action=AuditAction.DREAMFLOW,
                resource_type="dreamflow_run",
                resource_id=str(finished.run_id),
                occurred_at=deps.clock.now(),
                metadata={"status": finished.status.value, "sessions": len(sessions)},
            )
        )
        return finished

    async def _session(
        self,
        deps: Deps,
        run: DreamflowRun,
        session: Session,
        *,
        use_model: bool,
    ) -> tuple[list[MemoryCandidate], list[str]]:
        extracted = await self.extractor.extract(deps, run, session, use_model=use_model)
        validated = self.validator.validate(session, extracted)
        classified = await self.classifier.classify(validated, use_model=use_model)
        deduped = await self.duplicates.mark(deps, classified)
        conflicted = await self.conflicts.mark(deps, deduped)
        planned = self.planner.plan(deps, conflicted)
        written = await self.writer.apply(deps, session, planned)
        return planned, written


def _from_event(run: DreamflowRun, session: Session, event: SessionEvent) -> MemoryCandidate | None:
    text = event_search_text(event.payload)
    if event.event_type is EventType.STRUCTURED_OUTCOME:
        data = event.payload.get("data")
        structured = data if isinstance(data, dict) else None
        if structured and _looks_like_workflow(structured):
            return MemoryCandidate(
                candidate_id=uuid4(),
                dreamflow_run_id=run.run_id,
                tenant_id=session.tenant_id,
                user_id=session.user_id,
                session_id=session.session_id,
                category=MemoryCategory.WORKFLOW,
                title=str(structured.get("goal") or "workflow"),
                content=text or json.dumps(structured),
                structured_content=structured,
                confidence=0.9,
                importance=0.8,
                evidence_event_ids=[event.event_id],
            )
    if event.event_type is not EventType.USER_MESSAGE or not text:
        return None
    category = classify_text(text)
    if category is MemoryCategory.DOMAIN_KNOWLEDGE and "means" not in text.casefold():
        return None
    return MemoryCandidate(
        candidate_id=uuid4(),
        dreamflow_run_id=run.run_id,
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        session_id=session.session_id,
        category=category,
        title=text[:80],
        content=text,
        confidence=0.7,
        importance=0.5,
        evidence_event_ids=[event.event_id],
    )


def _looks_like_workflow(structured: dict[str, object]) -> bool:
    return any(key in structured for key in ("steps", "tables", "joins"))


def _object_dict(value: dict[str, object] | None) -> dict[str, object] | None:
    return value


def _contradicts(left: str, right: str) -> bool:
    lowered = f"{left} {right}".casefold()
    return any(token in lowered for token in _CONTRADICTION)


def _conflict_target(candidate: MemoryCandidate, memories: list[Memory]) -> Memory | None:
    for memory in memories:
        if memory.category is not candidate.category:
            continue
        if _structured_mismatch(candidate.structured_content, memory.structured_content):
            return memory
        if _contradicts(candidate.content, memory.content) and _shares_topic(
            candidate.content, memory.content
        ):
            return memory
    return None


def _shares_topic(left: str, right: str) -> bool:
    left_tokens = set(left.casefold().split())
    right_tokens = set(right.casefold().split())
    return len(left_tokens & right_tokens) >= 1


def _structured_mismatch(
    left: dict[str, object] | None,
    right: dict[str, object] | None,
) -> bool:
    if not left or not right:
        return False
    shared = set(left) & set(right)
    return any(left[key] != right[key] for key in shared)


def _conflict_type(candidate: MemoryCandidate, memory: Memory) -> ConflictType:
    if candidate.valid_from is not None:
        return ConflictType.TEMPORAL_CHANGE
    if (
        candidate.category is MemoryCategory.PREFERENCE
        or memory.category is MemoryCategory.PREFERENCE
    ):
        return ConflictType.PREFERENCE_OVERRIDE
    return ConflictType.CONTRADICTION


def _evidence(candidate: MemoryCandidate) -> list[EvidenceInput]:
    return [
        EvidenceInput(
            session_id=candidate.session_id, event_id=event_id, quote=candidate.content[:240]
        )
        for event_id in candidate.evidence_event_ids
    ]


def _idempotency_key(request: RunDreamflowRequest, sessions: list[Session]) -> str:
    session_ids = ",".join(sorted(str(session.session_id) for session in sessions))
    raw = f"{PIPELINE_VERSION}|{request.tenant_id}|{request.user_id}|{session_ids}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _cosine_duplicate(
    deps: Deps, scope: TenantScope, candidate: MemoryCandidate
) -> Memory | None:
    vectors = await deps.embeddings.embed([candidate.content])
    if not vectors:
        return None
    matches = await deps.vectors.search(scope, vectors[0], limit=5, owner_kind=None)
    memories = await deps.derived.list_user_memories(scope, candidate.user_id)
    by_version = {memory.version_id: memory for memory in memories}
    for match in matches:
        memory = by_version.get(match.owner_id)
        if memory is None or memory.category is not candidate.category:
            continue
        if subject_key(memory.content) != subject_key(candidate.content):
            continue
        if match.cosine >= deps.config.duplicate_cosine_threshold:
            return memory
    return None


async def _complete_validated[ModelT: BaseModel](
    model: ModelProvider,
    request: ModelRequest,
    schema: type[ModelT],
) -> ModelT | None:
    for _attempt in range(2):
        response = await model.complete_structured(request)
        try:
            return schema.model_validate_json(response.content)
        except ValueError:
            continue
    return None
