"""Replace stored vectors, then update engine_metadata."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memcave.adapters.embeddings.factory import MAX_EMBEDDING_DIMENSION
from memcave.db.tables import (
    EngineMetadataRow,
    MemoryEmbeddingRow,
    MemoryRecordRow,
    MemoryVersionRow,
    SessionEventRow,
)
from memcave.domain.enums import MemoryStatus, OwnerKind
from memcave.domain.errors import ProviderError
from memcave.ports.protocols import EmbeddingProvider


@dataclass(frozen=True)
class RebuildPlan:
    stored_dimension: int
    target_dimension: int
    active_vectors: int


@dataclass(frozen=True)
class _Target:
    tenant_id: str
    user_id: str | None
    owner_kind: str
    owner_id: str
    text: str


def _check_target(dimension: int) -> None:
    if dimension > MAX_EMBEDDING_DIMENSION:
        raise ProviderError(
            f"embedding dimension {dimension} is above {MAX_EMBEDDING_DIMENSION}, "
            "which a standard pgvector HNSW index cannot hold"
        )
    if dimension < 1:
        raise ProviderError("embedding dimension must be positive")


async def plan_rebuild(engine: AsyncEngine, target_dimension: int) -> RebuildPlan:
    """Read the stored dimension and count vectors that a rebuild would replace."""
    _check_target(target_dimension)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        stored = await _stored_dimension(session)
        targets = await _active_targets(session)
    return RebuildPlan(
        stored_dimension=stored,
        target_dimension=target_dimension,
        active_vectors=len(targets),
    )


async def apply_rebuild(
    engine: AsyncEngine,
    provider: EmbeddingProvider,
    target_dimension: int,
    *,
    before_metadata: Callable[[], None] | None = None,
) -> RebuildPlan:
    """Re-embed active rows. Metadata changes only after the new vectors are staged."""
    _check_target(target_dimension)
    if provider.dimension != target_dimension:
        raise ProviderError(
            f"provider dimension {provider.dimension} does not match target {target_dimension}"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        stored = await _stored_dimension(session)
        targets = await _active_targets(session)
    vectors = await _embed_targets(provider, targets, target_dimension)
    async with factory() as session, session.begin():
        if engine.dialect.name == "postgresql":
            await _replace_postgres(session, targets, vectors, target_dimension)
        else:
            await _replace_sqlite(session, targets, vectors, target_dimension)
        await session.flush()
        if before_metadata is not None:
            before_metadata()
        metadata = await session.get(EngineMetadataRow, "embedding_dimension")
        if metadata is None:
            raise ProviderError("engine_metadata has no embedding dimension")
        metadata.value = str(target_dimension)
    return RebuildPlan(
        stored_dimension=stored,
        target_dimension=target_dimension,
        active_vectors=len(targets),
    )


async def _embed_targets(
    provider: EmbeddingProvider,
    targets: Sequence[_Target],
    dimension: int,
) -> list[list[float]]:
    if not targets:
        return []
    vectors = await provider.embed([item.text for item in targets])
    if len(vectors) != len(targets) or any(len(vector) != dimension for vector in vectors):
        raise ProviderError("embedding provider returned an unexpected vector")
    return [list(vector) for vector in vectors]


async def _stored_dimension(session: AsyncSession) -> int:
    row = await session.get(EngineMetadataRow, "embedding_dimension")
    if row is None:
        raise ProviderError("engine_metadata has no embedding dimension")
    return int(row.value)


async def _active_targets(session: AsyncSession) -> list[_Target]:
    memories = (
        await session.execute(
            select(
                MemoryVersionRow.tenant_id,
                MemoryRecordRow.user_id,
                MemoryVersionRow.version_id,
                MemoryVersionRow.search_text,
            )
            .join(MemoryRecordRow, MemoryRecordRow.memory_id == MemoryVersionRow.memory_id)
            .where(
                MemoryRecordRow.status == MemoryStatus.ACTIVE.value,
                MemoryVersionRow.status == MemoryStatus.ACTIVE.value,
                MemoryVersionRow.version_number == MemoryRecordRow.current_version_number,
            )
        )
    ).all()
    events = (
        await session.execute(
            select(
                SessionEventRow.tenant_id,
                SessionEventRow.user_id,
                SessionEventRow.event_id,
                SessionEventRow.search_text,
            )
        )
    ).all()
    targets: list[_Target] = []
    for tenant_id, user_id, owner_id, body in memories:
        text_value = str(body or "").strip()
        if not text_value:
            continue
        targets.append(
            _Target(
                tenant_id=str(tenant_id),
                user_id=str(user_id) if user_id else None,
                owner_kind=OwnerKind.MEMORY_VERSION.value,
                owner_id=str(owner_id),
                text=text_value,
            )
        )
    for tenant_id, user_id, owner_id, body in events:
        text_value = str(body or "").strip()
        if not text_value:
            continue
        targets.append(
            _Target(
                tenant_id=str(tenant_id),
                user_id=str(user_id) if user_id else None,
                owner_kind=OwnerKind.EVENT.value,
                owner_id=str(owner_id),
                text=text_value,
            )
        )
    return targets


async def _replace_sqlite(
    session: AsyncSession,
    targets: Sequence[_Target],
    vectors: Sequence[Sequence[float]],
    dimension: int,
) -> None:
    await session.execute(delete(MemoryEmbeddingRow))
    created = datetime.now(UTC)
    for target, vector in zip(targets, vectors, strict=True):
        session.add(
            MemoryEmbeddingRow(
                embedding_id=str(uuid4()),
                tenant_id=target.tenant_id,
                owner_kind=target.owner_kind,
                owner_id=target.owner_id,
                user_id=target.user_id,
                dimension=dimension,
                embedding_json=list(vector),
                created_at=created,
            )
        )


async def _replace_postgres(
    session: AsyncSession,
    targets: Sequence[_Target],
    vectors: Sequence[Sequence[float]],
    dimension: int,
) -> None:
    await session.execute(text("DROP INDEX IF EXISTS ix_embeddings_cosine"))
    await session.execute(text("DELETE FROM memory_embeddings"))
    await session.execute(
        text(f"ALTER TABLE memory_embeddings ALTER COLUMN embedding TYPE vector({int(dimension)})")
    )
    created = datetime.now(UTC)
    for target, vector in zip(targets, vectors, strict=True):
        literal = "[" + ",".join(str(value) for value in vector) + "]"
        await session.execute(
            text(
                "INSERT INTO memory_embeddings "
                "(embedding_id, tenant_id, owner_kind, owner_id, user_id, dimension, "
                "embedding_json, created_at, embedding) "
                "VALUES (:id, :tenant, :kind, :owner, :user, :dim, "
                "CAST(:payload AS json), :created, CAST(:vector AS vector))"
            ),
            {
                "id": str(uuid4()),
                "tenant": target.tenant_id,
                "kind": target.owner_kind,
                "owner": target.owner_id,
                "user": target.user_id,
                "dim": dimension,
                "payload": json.dumps(list(vector)),
                "created": created,
                "vector": literal,
            },
        )
    await session.execute(
        text(
            "CREATE INDEX ix_embeddings_cosine ON memory_embeddings "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    )
