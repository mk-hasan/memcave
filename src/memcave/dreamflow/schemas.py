"""Structured model output for dreamflow."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from memcave.domain.enums import ConflictType, MemoryCategory


class ExtractedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MemoryCategory
    title: str
    content: str
    structured_content: dict[str, object] | None = None
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)


class ExtractionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[ExtractedCandidate]


class ConflictAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_type: ConflictType
    suggested_resolution: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""
