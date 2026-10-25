"""Persistent and transfer models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from memcave.domain.enums import (
    AuditAction,
    CandidateStatus,
    ConflictResolution,
    ConflictType,
    DreamflowState,
    EventType,
    InstructionType,
    ItemStatus,
    MemoryCategory,
    MemoryKind,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    OwnerKind,
    ProcessingState,
    ProposedAction,
    ResolutionStrategy,
    RunStatus,
    SessionStatus,
)


class MemcaveModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantScope(MemcaveModel):
    tenant_id: UUID
    user_id: UUID | None = None


class Tenant(MemcaveModel):
    tenant_id: UUID
    name: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class User(MemcaveModel):
    user_id: UUID
    tenant_id: UUID
    external_id: str | None = None
    display_name: str | None = None
    memory_opt_out: bool = False
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(MemcaveModel):
    session_id: UUID
    tenant_id: UUID
    user_id: UUID
    title: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    started_at: datetime
    ended_at: datetime | None = None
    dreamflow_state: DreamflowState = DreamflowState.PENDING
    dreamflow_run_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionEvent(MemcaveModel):
    event_id: UUID
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    sequence: int
    event_type: EventType
    occurred_at: datetime
    payload: dict[str, Any]
    processing_state: ProcessingState = ProcessingState.UNPROCESSED
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(MemcaveModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class ToolResult(MemcaveModel):
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    occurred_at: datetime | None = None


class MemoryEvidence(MemcaveModel):
    evidence_id: UUID
    memory_id: UUID
    version_id: UUID
    tenant_id: UUID
    session_id: UUID | None = None
    event_id: UUID | None = None
    quote: str | None = None
    weight: float = Field(default=1.0, ge=0, le=1)
    created_at: datetime


class Memory(MemcaveModel):
    """Public read model joining a record with the selected version."""

    memory_id: UUID
    version_id: UUID
    tenant_id: UUID
    scope: MemoryScope
    user_id: UUID | None
    category: MemoryCategory
    title: str
    content: str
    structured_content: dict[str, Any] | None = None
    status: MemoryStatus
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    valid_from: datetime
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    version_number: int
    predecessor_version_id: UUID | None = None
    embedding_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    subject_key: str = ""
    logical_path: str
    source: MemorySource
    content_hash: str
    deleted_at: datetime | None = None
    record_status: MemoryStatus = MemoryStatus.ACTIVE


class MemoryRecord(MemcaveModel):
    memory_id: UUID
    tenant_id: UUID
    scope: MemoryScope
    user_id: UUID | None
    category: MemoryCategory
    logical_path: str
    status: MemoryStatus
    current_version_number: int
    content_hash: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryVersion(MemcaveModel):
    version_id: UUID
    memory_id: UUID
    tenant_id: UUID
    version_number: int
    predecessor_version_id: UUID | None = None
    title: str
    content: str
    structured_content: dict[str, Any] | None = None
    status: MemoryStatus
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    valid_from: datetime
    valid_until: datetime | None = None
    created_at: datetime
    source: MemorySource
    embedding_id: UUID | None = None
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryCandidate(MemcaveModel):
    candidate_id: UUID
    dreamflow_run_id: UUID
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    category: MemoryCategory
    title: str
    content: str
    structured_content: dict[str, Any] | None = None
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    status: CandidateStatus = CandidateStatus.EXTRACTED
    proposed_action: ProposedAction = ProposedAction.CREATE
    rejection_reason: str | None = None
    model_category: MemoryCategory | None = None
    conflict_type: ConflictType | None = None
    target_memory_id: UUID | None = None
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.RULE


class MemoryConflict(MemcaveModel):
    conflict_id: UUID
    tenant_id: UUID
    memory_id: UUID
    candidate_id: UUID | None = None
    conflict_type: ConflictType
    resolution: ConflictResolution
    resolution_strategy: ResolutionStrategy
    notes: str
    created_at: datetime


class EmbeddingRecord(MemcaveModel):
    embedding_id: UUID
    tenant_id: UUID
    owner_kind: OwnerKind
    owner_id: UUID
    user_id: UUID | None = None
    dimension: int
    vector: list[float]
    created_at: datetime


class VectorMatch(MemcaveModel):
    owner_kind: OwnerKind
    owner_id: UUID
    cosine: float


class AuditEvent(MemcaveModel):
    audit_id: UUID
    tenant_id: UUID
    user_id: UUID | None
    actor: str
    action: AuditAction
    resource_type: str
    resource_id: str | None
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class DreamflowRun(MemcaveModel):
    run_id: UUID
    tenant_id: UUID
    user_id: UUID | None
    status: RunStatus
    pipeline_version: str
    idempotency_key: str
    started_at: datetime
    finished_at: datetime | None = None
    report: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DreamflowItem(MemcaveModel):
    item_id: UUID
    run_id: UUID
    tenant_id: UUID
    session_id: UUID
    stage: str
    status: ItemStatus
    content_hash: str | None = None
    memory_id: UUID | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RetrievalHit(MemcaveModel):
    hit_id: UUID
    kind: MemoryKind
    tenant_id: UUID
    user_id: UUID | None
    scope: MemoryScope | None = None
    source_id: UUID
    title: str
    content: str
    category: str | None = None
    source: str | None = None
    subject_key: str = ""
    structured_content: dict[str, Any] | None = None
    score: float
    score_components: dict[str, float]
    rank: int = 0
    token_estimate: int
    evidence_ids: list[UUID] = Field(default_factory=list)
    explanation: str
    occurred_at: datetime | None = None


class ScoreParts(MemcaveModel):
    components: dict[str, float]
    weights: dict[str, float]
    fused: float
    normalized: float = 0.0
    unavailable: list[str] = Field(default_factory=list)


class InstructionPayload(MemcaveModel):
    instruction_type: InstructionType
    raw_text: str
    category: MemoryCategory | None = None
    title: str | None = None
    content: str | None = None
    target_memory_id: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    structured_content: dict[str, Any] | None = None


class ParsedInstruction(MemcaveModel):
    instruction_type: InstructionType
    category: MemoryCategory | None
    title: str
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    status: MemoryStatus
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    target_memory_id: UUID | None = None
    forget_needle: str | None = None
    ambiguous: bool = False
    structured_content: dict[str, Any] | None = None


class EraseCounts(MemcaveModel):
    sessions: int = 0
    events: int = 0
    memories: int = 0
    versions: int = 0
    evidence: int = 0
    embeddings: int = 0
    conflicts: int = 0
    dreamflow_items: int = 0
    benchmark_results: int = 0


class ModelRequest(MemcaveModel):
    task: str
    system_prompt: str
    user_prompt: str
    temperature: float = 0.0
    seed: int | None = None
    model_name: str = "fake"


class ModelResponse(MemcaveModel):
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model_name: str


class RetrievalConfig(MemcaveModel):
    top_k: int = 8
    token_budget: int = 2000
    recency_half_life_days: float = 30.0
    redact_queries: bool = True
    review_confidence_threshold: float = 0.60
    auto_resolve_confidence_threshold: float = 0.85
    duplicate_cosine_threshold: float = 0.92
    derived_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "vector": 0.30,
            "lexical": 0.20,
            "recency": 0.15,
            "importance": 0.20,
            "confidence": 0.15,
        }
    )
    episodic_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "vector": 0.40,
            "lexical": 0.30,
            "recency": 0.20,
            "importance": 0.10,
        }
    )


class BenchmarkCase(MemcaveModel):
    case_id: str
    category: str
    tenant_id: UUID
    user_id: UUID
    history: list[dict[str, Any]]
    question: str
    expected_facts: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)
    memory_dependent: bool = True
    expected_workflow: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    decoys: list[dict[str, Any]] = Field(default_factory=list)
    as_of: datetime | None = None
    expected_answer: str | None = None


class MetricResult(MemcaveModel):
    name: str
    value: float
    details: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRun(MemcaveModel):
    benchmark_run_id: UUID
    created_at: datetime
    config: dict[str, Any]
    package_version: str
    strategies: list[str]
    report: dict[str, Any] = Field(default_factory=dict)
