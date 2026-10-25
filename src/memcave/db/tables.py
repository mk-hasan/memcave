"""SQLAlchemy tables shared by SQLite and PostgreSQL."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_tenant", "tenant_id", "user_id"),
        Index(
            "uq_users_external",
            "tenant_id",
            "external_id",
            unique=True,
            sqlite_where=text("external_id IS NOT NULL"),
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    memory_opt_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_time", "tenant_id", "user_id", "started_at"),
        Index("ix_sessions_dreamflow", "tenant_id", "dreamflow_state", "ended_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dreamflow_state: Mapped[str] = mapped_column(String(32))
    dreamflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class SessionEventRow(Base):
    __tablename__ = "session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_event_sequence"),
        Index("ix_events_user_time", "tenant_id", "user_id", "occurred_at"),
        Index("ix_events_processing", "session_id", "processing_state", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    processing_state: Mapped[str] = mapped_column(String(32))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="")


class MemoryRecordRow(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'user' AND user_id IS NOT NULL) OR (scope = 'tenant' AND user_id IS NULL)",
            name="ck_memory_scope_user",
        ),
        Index("ix_memory_status", "tenant_id", "user_id", "status", "category"),
        Index("ix_memory_path", "tenant_id", "logical_path"),
        Index(
            "uq_memory_hash_active",
            "tenant_id",
            "scope",
            text("coalesce(user_id, '00000000-0000-0000-0000-000000000000')"),
            "category",
            "content_hash",
            unique=True,
            sqlite_where=text("status IN ('active', 'candidate')"),
            postgresql_where=text("status IN ('active', 'candidate')"),
        ),
    )

    memory_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    scope: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    logical_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    current_version_number: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class MemoryVersionRow(Base):
    __tablename__ = "memory_versions"
    __table_args__ = (
        UniqueConstraint("memory_id", "version_number", name="uq_memory_version"),
        Index("ix_versions_window", "tenant_id", "status", "valid_from", "valid_until"),
        Index("ix_versions_number", "memory_id", "version_number"),
    )

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    version_number: Mapped[int] = mapped_column(Integer)
    predecessor_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    structured_content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    importance: Mapped[float] = mapped_column(Float)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32))
    embedding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="")


class MemoryEvidenceRow(Base):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        Index("ix_evidence_version", "version_id"),
        Index("ix_evidence_event", "event_id"),
        Index("ix_evidence_memory", "tenant_id", "memory_id"),
    )

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(36))
    version_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryEmbeddingRow(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", name="uq_embedding_owner"),
        Index("ix_embeddings_tenant", "tenant_id", "owner_kind"),
    )

    embedding_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    owner_kind: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dimension: Mapped[int] = mapped_column(Integer)
    embedding_json: Mapped[list[float]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DreamflowRunRow(Base):
    __tablename__ = "dreamflow_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_dreamflow_key"),)

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    pipeline_version: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DreamflowItemRow(Base):
    __tablename__ = "dreamflow_items"
    __table_args__ = (UniqueConstraint("run_id", "session_id", "stage", name="uq_dreamflow_item"),)

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_user_time", "tenant_id", "user_id", "occurred_at"),
        Index("ix_audit_resource", "tenant_id", "resource_type", "resource_id"),
    )

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(32))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class BenchmarkRunRow(Base):
    __tablename__ = "benchmark_runs"

    benchmark_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    package_version: Mapped[str] = mapped_column(String(32))
    strategies_json: Mapped[list[str]] = mapped_column(JSON)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class BenchmarkResultRow(Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (Index("ix_benchmark_case", "benchmark_run_id", "strategy", "case_id"),)

    result_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    benchmark_run_id: Mapped[str] = mapped_column(String(36))
    case_id: Mapped[str] = mapped_column(String(200))
    strategy: Mapped[str] = mapped_column(String(64))
    repetition: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class EngineMetadataRow(Base):
    __tablename__ = "engine_metadata"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class MemoryCandidateRow(Base):
    __tablename__ = "memory_candidates"

    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dreamflow_run_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class MemoryConflictRow(Base):
    __tablename__ = "memory_conflicts"

    conflict_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    memory_id: Mapped[str] = mapped_column(String(36))
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conflict_type: Mapped[str] = mapped_column(String(64))
    resolution: Mapped[str] = mapped_column(String(32))
    resolution_strategy: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
