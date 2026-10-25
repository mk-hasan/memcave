"""Enumerations for memcave domain objects."""

from enum import StrEnum


class EventType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL_ANSWER = "final_answer"
    USER_FEEDBACK = "user_feedback"
    EXPLICIT_MEMORY_INSTRUCTION = "explicit_memory_instruction"
    STRUCTURED_OUTCOME = "structured_outcome"


class MemoryCategory(StrEnum):
    PROFILE = "profile"
    PREFERENCE = "preference"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    PROJECT = "project"
    WORKFLOW = "workflow"
    CORRECTION = "correction"
    CONSTRAINT = "constraint"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    STALE = "stale"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemoryScope(StrEnum):
    USER = "user"
    TENANT = "tenant"


class MemorySource(StrEnum):
    EXPLICIT_INSTRUCTION = "explicit_instruction"
    API = "api"
    DREAMFLOW = "dreamflow"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"


class DreamflowState(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ProcessingState(StrEnum):
    UNPROCESSED = "unprocessed"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"


class InstructionType(StrEnum):
    REMEMBER = "remember"
    FORGET = "forget"
    CORRECT = "correct"
    TEMPORAL_CHANGE = "temporal_change"


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    DERIVED = "derived"
    BOTH = "both"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class CandidateStatus(StrEnum):
    EXTRACTED = "extracted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"


class ProposedAction(StrEnum):
    CREATE = "create"
    NEW_VERSION = "new_version"
    MERGE = "merge"
    SUPERSEDE = "supersede"
    ARCHIVE = "archive"
    SKIP = "skip"


class ConflictType(StrEnum):
    CONTRADICTION = "contradiction"
    TEMPORAL_CHANGE = "temporal_change"
    PREFERENCE_OVERRIDE = "preference_override"
    SCOPE_CLASH = "scope_clash"


class ConflictResolution(StrEnum):
    UNRESOLVED = "unresolved"
    VERSIONED = "versioned"
    MERGED = "merged"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ResolutionStrategy(StrEnum):
    RULE = "rule"
    MODEL_ASSISTED = "model_assisted"
    HUMAN = "human"


class AuditAction(StrEnum):
    READ = "read"
    WRITE = "write"
    UPDATE = "update"
    DELETE = "delete"
    ERASE = "erase"
    EXPORT = "export"
    DREAMFLOW = "dreamflow"


class OwnerKind(StrEnum):
    EVENT = "event"
    MEMORY_VERSION = "memory_version"


class ItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


CATEGORY_ORDER: tuple[MemoryCategory, ...] = (
    MemoryCategory.PROFILE,
    MemoryCategory.PREFERENCE,
    MemoryCategory.DOMAIN_KNOWLEDGE,
    MemoryCategory.PROJECT,
    MemoryCategory.WORKFLOW,
    MemoryCategory.CORRECTION,
    MemoryCategory.CONSTRAINT,
)
