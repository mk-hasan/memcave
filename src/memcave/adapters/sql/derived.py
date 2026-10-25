"""Derived-memory, vector, and audit SQL stores."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select

from memcave.adapters.sql.gateway import (
    SqlGateway,
    as_json,
    as_uuid,
    aware,
    index_text,
    must_aware,
)
from memcave.db.tables import (
    AuditEventRow,
    BenchmarkResultRow,
    DreamflowItemRow,
    DreamflowRunRow,
    MemoryCandidateRow,
    MemoryConflictRow,
    MemoryEmbeddingRow,
    MemoryEvidenceRow,
    MemoryRecordRow,
    MemoryVersionRow,
)
from memcave.domain.enums import (
    AuditAction,
    ItemStatus,
    MemoryCategory,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    OwnerKind,
    RunStatus,
)
from memcave.domain.errors import PolicyRejectedError
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
    TenantScope,
    VectorMatch,
)
from memcave.services.text import cosine_similarity, resolve_subject_key


def _window(version: MemoryVersion, as_of: datetime) -> bool:
    if version.valid_from > as_of:
        return False
    return version.valid_until is None or version.valid_until > as_of


class SqlDerivedStore:
    def __init__(self, gateway: SqlGateway) -> None:
        self.gateway = gateway

    async def insert_memory(
        self,
        record: MemoryRecord,
        version: MemoryVersion,
        evidence: list[MemoryEvidence] | tuple[MemoryEvidence, ...],
    ) -> Memory:
        async with self.gateway.factory() as session:
            await self._reject_duplicate(session, record)
            session.add(_record_row(record))
            session.add(_version_row(version))
            for item in evidence:
                session.add(_evidence_row(item))
            await index_text(
                session,
                "memory_version",
                str(version.version_id),
                str(version.tenant_id),
                f"{version.title}\n{version.content}",
            )
            await session.commit()
        return _view(record, version, list(evidence))

    async def _reject_duplicate(self, session: object, record: MemoryRecord) -> None:
        if record.status not in {MemoryStatus.ACTIVE, MemoryStatus.CANDIDATE}:
            return
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(session, AsyncSession):
            return
        rows = (
            await session.scalars(
                select(MemoryRecordRow).where(
                    MemoryRecordRow.tenant_id == str(record.tenant_id),
                    MemoryRecordRow.category == record.category.value,
                    MemoryRecordRow.content_hash == record.content_hash,
                    MemoryRecordRow.status.in_(
                        [MemoryStatus.ACTIVE.value, MemoryStatus.CANDIDATE.value]
                    ),
                )
            )
        ).all()
        for row in rows:
            if row.memory_id == str(record.memory_id):
                continue
            if row.scope == record.scope.value and (row.user_id or None) == (
                str(record.user_id) if record.user_id else None
            ):
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
        async with self.gateway.factory() as session:
            if previous_version_id is not None and previous_status is not None:
                previous = await session.get(MemoryVersionRow, str(previous_version_id))
                if previous is not None:
                    previous.status = previous_status.value
                    previous.valid_until = previous_valid_until
            row = await session.get(MemoryRecordRow, str(record.memory_id))
            if row is None:
                session.add(_record_row(record))
            else:
                _apply_record(row, record)
            session.add(_version_row(version))
            for item in evidence:
                session.add(_evidence_row(item))
            await index_text(
                session,
                "memory_version",
                str(version.version_id),
                str(version.tenant_id),
                f"{version.title}\n{version.content}",
            )
            await session.commit()
        return _view(record, version, list(evidence))

    async def add_evidence(self, evidence: MemoryEvidence) -> None:
        async with self.gateway.factory() as session:
            session.add(_evidence_row(evidence))
            await session.commit()

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
        record = await self._record(memory_id)
        if record is None or not _visible(scope, record, include_shared, scope.user_id):
            return None
        versions = await self._versions(memory_id)
        version = _select(record, versions, version_number, as_of)
        if version is None:
            return None
        if version.status is MemoryStatus.DELETED and not include_deleted:
            return None
        evidence = await self._evidence(version.version_id)
        return _view(record, version, evidence)

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
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryRecordRow).where(MemoryRecordRow.tenant_id == str(scope.tenant_id))
                )
            ).all()
        allowed = {MemoryStatus.ACTIVE}
        if include_candidates:
            allowed.add(MemoryStatus.CANDIDATE)
        found: list[Memory] = []
        for row in rows:
            record = _record_model(row)
            if record.status is MemoryStatus.DELETED:
                continue
            if not _visible(scope, record, include_shared, user_id):
                continue
            if categories is not None and record.category not in categories:
                continue
            for version in await self._versions(record.memory_id):
                if version.status not in allowed or version.confidence < min_confidence:
                    continue
                if not _window(version, as_of):
                    continue
                found.append(_view(record, version, await self._evidence(version.version_id)))
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
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryRecordRow).where(
                        MemoryRecordRow.tenant_id == str(scope.tenant_id),
                        MemoryRecordRow.category == category.value,
                    )
                )
            ).all()
        for row in rows:
            record = _record_model(row)
            if record.user_id != user_id:
                continue
            if record.status not in statuses or record.content_hash != content_hash:
                continue
            versions = await self._versions(record.memory_id)
            version = _select(record, versions, None, None)
            if version is None:
                continue
            return _view(record, version, await self._evidence(version.version_id))
        return None

    async def clear_hash(self, scope: TenantScope, memory_id: UUID) -> None:
        async with self.gateway.factory() as session:
            row = await session.get(MemoryRecordRow, str(memory_id))
            if row is None or row.tenant_id != str(scope.tenant_id):
                return
            row.content_hash = ""
            versions = (
                await session.scalars(
                    select(MemoryVersionRow).where(MemoryVersionRow.memory_id == str(memory_id))
                )
            ).all()
            for version in versions:
                if version.status == MemoryStatus.DELETED.value:
                    version.content_hash = ""
            await session.commit()

    async def list_versions(self, scope: TenantScope, memory_id: UUID) -> list[MemoryVersion]:
        record = await self._record(memory_id)
        if record is None or record.tenant_id != scope.tenant_id:
            return []
        if scope.user_id is not None and record.user_id not in {None, scope.user_id}:
            return []
        return await self._versions(memory_id)

    async def list_memory_ids(self, scope: TenantScope, user_id: UUID) -> list[UUID]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryRecordRow.memory_id).where(
                        MemoryRecordRow.tenant_id == str(scope.tenant_id),
                        MemoryRecordRow.user_id == str(user_id),
                    )
                )
            ).all()
        return [UUID(row) for row in rows]

    async def list_user_memories(self, scope: TenantScope, user_id: UUID) -> list[Memory]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryRecordRow).where(
                        MemoryRecordRow.tenant_id == str(scope.tenant_id),
                        MemoryRecordRow.user_id == str(user_id),
                    )
                )
            ).all()
        found: list[Memory] = []
        for row in rows:
            record = _record_model(row)
            if record.status is MemoryStatus.DELETED:
                continue
            versions = await self._versions(record.memory_id)
            version = _select(record, versions, None, None)
            if version is not None:
                found.append(_view(record, version, await self._evidence(version.version_id)))
        return found

    async def save_conflict(self, conflict: MemoryConflict) -> None:
        async with self.gateway.factory() as session:
            session.add(
                MemoryConflictRow(
                    conflict_id=str(conflict.conflict_id),
                    tenant_id=str(conflict.tenant_id),
                    memory_id=str(conflict.memory_id),
                    candidate_id=str(conflict.candidate_id) if conflict.candidate_id else None,
                    conflict_type=conflict.conflict_type.value,
                    resolution=conflict.resolution.value,
                    resolution_strategy=conflict.resolution_strategy.value,
                    notes=conflict.notes,
                    created_at=conflict.created_at,
                )
            )
            await session.commit()

    async def save_run(self, run: DreamflowRun) -> None:
        async with self.gateway.factory() as session:
            row = await session.get(DreamflowRunRow, str(run.run_id))
            payload = dict(
                tenant_id=str(run.tenant_id),
                user_id=str(run.user_id) if run.user_id else None,
                status=run.status.value,
                pipeline_version=run.pipeline_version,
                idempotency_key=run.idempotency_key,
                started_at=run.started_at,
                finished_at=run.finished_at,
                report_json=run.report,
                error=run.error,
            )
            if row is None:
                session.add(DreamflowRunRow(run_id=str(run.run_id), **payload))
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
            await session.commit()

    async def get_run_by_key(self, scope: TenantScope, key: str) -> DreamflowRun | None:
        async with self.gateway.factory() as session:
            row = await session.scalar(
                select(DreamflowRunRow).where(
                    DreamflowRunRow.tenant_id == str(scope.tenant_id),
                    DreamflowRunRow.idempotency_key == key,
                )
            )
        return None if row is None else _run(row)

    async def save_item(self, item: DreamflowItem) -> None:
        async with self.gateway.factory() as session:
            existing = await session.get(DreamflowItemRow, str(item.item_id))
            if existing is not None:
                await session.delete(existing)
                await session.flush()
            session.add(
                DreamflowItemRow(
                    item_id=str(item.item_id),
                    run_id=str(item.run_id),
                    tenant_id=str(item.tenant_id),
                    session_id=str(item.session_id),
                    stage=item.stage,
                    status=item.status.value,
                    content_hash=item.content_hash,
                    memory_id=str(item.memory_id) if item.memory_id else None,
                    error=item.error,
                    payload_json=item.payload,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            )
            await session.commit()

    async def list_items(self, scope: TenantScope, run_id: UUID) -> list[DreamflowItem]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(DreamflowItemRow).where(
                        DreamflowItemRow.tenant_id == str(scope.tenant_id),
                        DreamflowItemRow.run_id == str(run_id),
                    )
                )
            ).all()
        return [_item(row) for row in rows]

    async def save_candidate(self, candidate: MemoryCandidate) -> None:
        async with self.gateway.factory() as session:
            existing = await session.get(MemoryCandidateRow, str(candidate.candidate_id))
            payload = candidate.model_dump(mode="json")
            if existing is None:
                session.add(
                    MemoryCandidateRow(
                        candidate_id=str(candidate.candidate_id),
                        dreamflow_run_id=str(candidate.dreamflow_run_id),
                        tenant_id=str(candidate.tenant_id),
                        user_id=str(candidate.user_id),
                        payload_json=payload,
                    )
                )
            else:
                existing.payload_json = payload
            await session.commit()

    async def list_candidates(self, scope: TenantScope, run_id: UUID) -> list[MemoryCandidate]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryCandidateRow).where(
                        MemoryCandidateRow.tenant_id == str(scope.tenant_id),
                        MemoryCandidateRow.dreamflow_run_id == str(run_id),
                    )
                )
            ).all()
        return [MemoryCandidate.model_validate(as_json(row.payload_json)) for row in rows]

    async def erase_user(self, scope: TenantScope) -> EraseCounts:
        if scope.user_id is None:
            return EraseCounts()
        async with self.gateway.factory() as session:
            records = (
                await session.scalars(
                    select(MemoryRecordRow).where(
                        MemoryRecordRow.tenant_id == str(scope.tenant_id),
                        MemoryRecordRow.user_id == str(scope.user_id),
                    )
                )
            ).all()
            memory_ids = [row.memory_id for row in records]
            versions = (
                await session.scalars(
                    select(MemoryVersionRow).where(
                        MemoryVersionRow.memory_id.in_(memory_ids or [""])
                    )
                )
            ).all()
            evidence = (
                await session.scalars(
                    select(MemoryEvidenceRow).where(
                        MemoryEvidenceRow.memory_id.in_(memory_ids or [""])
                    )
                )
            ).all()
            conflicts = (
                await session.scalars(
                    select(MemoryConflictRow).where(
                        MemoryConflictRow.memory_id.in_(memory_ids or [""])
                    )
                )
            ).all()
            items = (
                await session.scalars(
                    select(DreamflowItemRow).where(
                        DreamflowItemRow.tenant_id == str(scope.tenant_id)
                    )
                )
            ).all()
            removed_items = 0
            for item in items:
                payload = as_json(item.payload_json) or {}
                if payload.get("user_id") == str(scope.user_id):
                    await session.delete(item)
                    removed_items += 1
            candidates = (
                await session.scalars(
                    select(MemoryCandidateRow).where(
                        MemoryCandidateRow.tenant_id == str(scope.tenant_id),
                        MemoryCandidateRow.user_id == str(scope.user_id),
                    )
                )
            ).all()
            for row in candidates:
                await session.delete(row)
            results = (
                await session.scalars(
                    select(BenchmarkResultRow).where(
                        BenchmarkResultRow.user_id == str(scope.user_id)
                    )
                )
            ).all()
            for row in results:
                await session.delete(row)
            for row in evidence:
                await session.delete(row)
            for row in conflicts:
                await session.delete(row)
            for row in versions:
                await session.delete(row)
            for row in records:
                await session.delete(row)
            await session.commit()
            return EraseCounts(
                memories=len(records),
                versions=len(versions),
                evidence=len(evidence),
                conflicts=len(conflicts),
                dreamflow_items=removed_items,
                benchmark_results=len(results),
            )

    async def existing_paths(self, scope: TenantScope, user_id: UUID | None) -> set[str]:
        async with self.gateway.factory() as session:
            stmt = select(MemoryRecordRow.logical_path).where(
                MemoryRecordRow.tenant_id == str(scope.tenant_id)
            )
            if user_id is None:
                stmt = stmt.where(MemoryRecordRow.user_id.is_(None))
            else:
                stmt = stmt.where(MemoryRecordRow.user_id == str(user_id))
            rows = (await session.scalars(stmt)).all()
        return set(rows)

    async def _record(self, memory_id: UUID) -> MemoryRecord | None:
        async with self.gateway.factory() as session:
            row = await session.get(MemoryRecordRow, str(memory_id))
        return None if row is None else _record_model(row)

    async def _versions(self, memory_id: UUID) -> list[MemoryVersion]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryVersionRow).where(MemoryVersionRow.memory_id == str(memory_id))
                )
            ).all()
        return [_version_model(row) for row in rows]

    async def _evidence(self, version_id: UUID) -> list[MemoryEvidence]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(MemoryEvidenceRow).where(MemoryEvidenceRow.version_id == str(version_id))
                )
            ).all()
        return [_evidence_model(row) for row in rows]


class SqlVectorStore:
    def __init__(self, gateway: SqlGateway) -> None:
        self.gateway = gateway

    async def upsert(self, record: EmbeddingRecord) -> None:
        from sqlalchemy import text

        async with self.gateway.factory() as session:
            await session.execute(
                delete(MemoryEmbeddingRow).where(
                    MemoryEmbeddingRow.owner_kind == record.owner_kind.value,
                    MemoryEmbeddingRow.owner_id == str(record.owner_id),
                )
            )
            if self.gateway.engine.dialect.name == "postgresql":
                vector = "[" + ",".join(str(value) for value in record.vector) + "]"
                await session.execute(
                    text(
                        "INSERT INTO memory_embeddings "
                        "(embedding_id, tenant_id, owner_kind, owner_id, user_id, dimension, "
                        "embedding_json, created_at, embedding) "
                        "VALUES (:id, :tenant, :kind, :owner, :user, :dim, "
                        "CAST(:payload AS json), :created, CAST(:vector AS vector))"
                    ),
                    {
                        "id": str(record.embedding_id),
                        "tenant": str(record.tenant_id),
                        "kind": record.owner_kind.value,
                        "owner": str(record.owner_id),
                        "user": str(record.user_id) if record.user_id else None,
                        "dim": record.dimension,
                        "payload": json_dumps(record.vector),
                        "created": record.created_at,
                        "vector": vector,
                    },
                )
            else:
                session.add(
                    MemoryEmbeddingRow(
                        embedding_id=str(record.embedding_id),
                        tenant_id=str(record.tenant_id),
                        owner_kind=record.owner_kind.value,
                        owner_id=str(record.owner_id),
                        user_id=str(record.user_id) if record.user_id else None,
                        dimension=record.dimension,
                        embedding_json=record.vector,
                        created_at=record.created_at,
                    )
                )
            await session.commit()

    async def search(
        self,
        scope: TenantScope,
        vector: list[float] | tuple[float, ...],
        *,
        limit: int,
        owner_kind: OwnerKind | None,
    ) -> list[VectorMatch]:
        if self.gateway.engine.dialect.name == "postgresql":
            matches = await self._pg_search(scope, list(vector), limit, owner_kind)
            if matches:
                return matches
        async with self.gateway.factory() as session:
            stmt = select(MemoryEmbeddingRow).where(
                MemoryEmbeddingRow.tenant_id == str(scope.tenant_id)
            )
            if owner_kind is not None:
                stmt = stmt.where(MemoryEmbeddingRow.owner_kind == owner_kind.value)
            rows = (await session.scalars(stmt)).all()
        matches = [
            VectorMatch(
                owner_kind=OwnerKind(row.owner_kind),
                owner_id=UUID(row.owner_id),
                cosine=cosine_similarity(list(vector), list(as_json(row.embedding_json))),
            )
            for row in rows
        ]
        matches.sort(key=lambda item: item.cosine, reverse=True)
        return matches[:limit]

    async def _pg_search(
        self,
        scope: TenantScope,
        vector: list[float],
        limit: int,
        owner_kind: OwnerKind | None,
    ) -> list[VectorMatch]:
        from sqlalchemy import text

        kind_sql = ""
        params: dict[str, object] = {
            "tenant": str(scope.tenant_id),
            "vector": "[" + ",".join(str(value) for value in vector) + "]",
            "limit": limit,
        }
        if owner_kind is not None:
            kind_sql = "AND owner_kind = :kind"
            params["kind"] = owner_kind.value
        try:
            async with self.gateway.factory() as session:
                rows = await session.execute(
                    text(
                        "SELECT owner_kind, owner_id, "
                        "1 - (embedding <=> CAST(:vector AS vector)) AS cosine "
                        "FROM memory_embeddings WHERE tenant_id = :tenant "
                        "AND embedding IS NOT NULL "
                        f"{kind_sql} ORDER BY embedding <=> CAST(:vector AS vector) "
                        "LIMIT :limit"
                    ),
                    params,
                )
                return [
                    VectorMatch(
                        owner_kind=OwnerKind(row[0]),
                        owner_id=UUID(str(row[1])),
                        cosine=float(row[2]),
                    )
                    for row in rows
                ]
        except Exception:
            return []

    async def delete_owners(
        self, scope: TenantScope, owner_ids: list[UUID] | tuple[UUID, ...]
    ) -> int:
        if not owner_ids:
            return 0
        async with self.gateway.factory() as session:
            result = await session.execute(
                delete(MemoryEmbeddingRow).where(
                    MemoryEmbeddingRow.tenant_id == str(scope.tenant_id),
                    MemoryEmbeddingRow.owner_id.in_([str(item) for item in owner_ids]),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)


class SqlEventSink:
    def __init__(self, gateway: SqlGateway) -> None:
        self.gateway = gateway

    async def emit(self, event: AuditEvent) -> None:
        async with self.gateway.factory() as session:
            session.add(
                AuditEventRow(
                    audit_id=str(event.audit_id),
                    tenant_id=str(event.tenant_id),
                    user_id=str(event.user_id) if event.user_id else None,
                    actor=event.actor,
                    action=event.action.value,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    occurred_at=event.occurred_at,
                    metadata_json=event.metadata,
                )
            )
            await session.commit()

    async def list_audits(self, scope: TenantScope) -> list[AuditEvent]:
        async with self.gateway.factory() as session:
            rows = (
                await session.scalars(
                    select(AuditEventRow).where(AuditEventRow.tenant_id == str(scope.tenant_id))
                )
            ).all()
        return [
            AuditEvent(
                audit_id=UUID(row.audit_id),
                tenant_id=UUID(row.tenant_id),
                user_id=as_uuid(row.user_id),
                actor=row.actor,
                action=AuditAction(row.action),
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                occurred_at=must_aware(row.occurred_at),
                metadata=dict(as_json(row.metadata_json) or {}),
            )
            for row in rows
        ]


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value)


def _visible(
    scope: TenantScope, record: MemoryRecord, include_shared: bool, user_id: UUID | None
) -> bool:
    if record.tenant_id != scope.tenant_id:
        return False
    if record.scope is MemoryScope.TENANT:
        return include_shared
    return user_id is None or record.user_id == user_id


def _select(
    record: MemoryRecord,
    versions: list[MemoryVersion],
    version_number: int | None,
    as_of: datetime | None,
) -> MemoryVersion | None:
    if version_number is not None:
        return next((item for item in versions if item.version_number == version_number), None)
    if as_of is None:
        return next(
            (item for item in versions if item.version_number == record.current_version_number),
            None,
        )
    matching = [
        item for item in versions if item.status is MemoryStatus.ACTIVE and _window(item, as_of)
    ]
    if not matching:
        return None
    return max(matching, key=lambda item: (item.valid_from, item.version_number))


def _view(record: MemoryRecord, version: MemoryVersion, evidence: list[MemoryEvidence]) -> Memory:
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


def _record_row(model: MemoryRecord) -> MemoryRecordRow:
    return MemoryRecordRow(
        memory_id=str(model.memory_id),
        tenant_id=str(model.tenant_id),
        scope=model.scope.value,
        user_id=str(model.user_id) if model.user_id else None,
        category=model.category.value,
        logical_path=model.logical_path,
        status=model.status.value,
        current_version_number=model.current_version_number,
        content_hash=model.content_hash,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
        metadata_json=model.metadata,
    )


def _apply_record(row: MemoryRecordRow, model: MemoryRecord) -> None:
    row.scope = model.scope.value
    row.user_id = str(model.user_id) if model.user_id else None
    row.category = model.category.value
    row.logical_path = model.logical_path
    row.status = model.status.value
    row.current_version_number = model.current_version_number
    row.content_hash = model.content_hash
    row.updated_at = model.updated_at
    row.deleted_at = model.deleted_at
    row.metadata_json = model.metadata


def _record_model(row: MemoryRecordRow) -> MemoryRecord:
    return MemoryRecord(
        memory_id=UUID(row.memory_id),
        tenant_id=UUID(row.tenant_id),
        scope=MemoryScope(row.scope),
        user_id=as_uuid(row.user_id),
        category=MemoryCategory(row.category),
        logical_path=row.logical_path,
        status=MemoryStatus(row.status),
        current_version_number=row.current_version_number,
        content_hash=row.content_hash,
        created_at=must_aware(row.created_at),
        updated_at=must_aware(row.updated_at),
        deleted_at=aware(row.deleted_at),
        metadata=dict(as_json(row.metadata_json) or {}),
    )


def _version_row(model: MemoryVersion) -> MemoryVersionRow:
    return MemoryVersionRow(
        version_id=str(model.version_id),
        memory_id=str(model.memory_id),
        tenant_id=str(model.tenant_id),
        version_number=model.version_number,
        predecessor_version_id=str(model.predecessor_version_id)
        if model.predecessor_version_id
        else None,
        title=model.title,
        content=model.content,
        structured_content_json=model.structured_content,
        status=model.status.value,
        confidence=model.confidence,
        importance=model.importance,
        valid_from=model.valid_from,
        valid_until=model.valid_until,
        created_at=model.created_at,
        source=model.source.value,
        embedding_id=str(model.embedding_id) if model.embedding_id else None,
        content_hash=model.content_hash,
        metadata_json=model.metadata,
        search_text=f"{model.title}\n{model.content}",
    )


def _version_model(row: MemoryVersionRow) -> MemoryVersion:
    structured = as_json(row.structured_content_json)
    return MemoryVersion(
        version_id=UUID(row.version_id),
        memory_id=UUID(row.memory_id),
        tenant_id=UUID(row.tenant_id),
        version_number=row.version_number,
        predecessor_version_id=as_uuid(row.predecessor_version_id),
        title=row.title,
        content=row.content,
        structured_content=dict(structured) if isinstance(structured, dict) else None,
        status=MemoryStatus(row.status),
        confidence=row.confidence,
        importance=row.importance,
        valid_from=must_aware(row.valid_from),
        valid_until=aware(row.valid_until),
        created_at=must_aware(row.created_at),
        source=MemorySource(row.source),
        embedding_id=as_uuid(row.embedding_id),
        content_hash=row.content_hash,
        metadata=dict(as_json(row.metadata_json) or {}),
    )


def _evidence_row(model: MemoryEvidence) -> MemoryEvidenceRow:
    return MemoryEvidenceRow(
        evidence_id=str(model.evidence_id),
        memory_id=str(model.memory_id),
        version_id=str(model.version_id),
        tenant_id=str(model.tenant_id),
        session_id=str(model.session_id) if model.session_id else None,
        event_id=str(model.event_id) if model.event_id else None,
        quote=model.quote,
        weight=model.weight,
        created_at=model.created_at,
    )


def _evidence_model(row: MemoryEvidenceRow) -> MemoryEvidence:
    return MemoryEvidence(
        evidence_id=UUID(row.evidence_id),
        memory_id=UUID(row.memory_id),
        version_id=UUID(row.version_id),
        tenant_id=UUID(row.tenant_id),
        session_id=as_uuid(row.session_id),
        event_id=as_uuid(row.event_id),
        quote=row.quote,
        weight=row.weight,
        created_at=must_aware(row.created_at),
    )


def _run(row: DreamflowRunRow) -> DreamflowRun:
    return DreamflowRun(
        run_id=UUID(row.run_id),
        tenant_id=UUID(row.tenant_id),
        user_id=as_uuid(row.user_id),
        status=RunStatus(row.status),
        pipeline_version=row.pipeline_version,
        idempotency_key=row.idempotency_key,
        started_at=must_aware(row.started_at),
        finished_at=aware(row.finished_at),
        report=dict(as_json(row.report_json) or {}),
        error=row.error,
    )


def _item(row: DreamflowItemRow) -> DreamflowItem:
    return DreamflowItem(
        item_id=UUID(row.item_id),
        run_id=UUID(row.run_id),
        tenant_id=UUID(row.tenant_id),
        session_id=UUID(row.session_id),
        stage=row.stage,
        status=ItemStatus(row.status),
        content_hash=row.content_hash,
        memory_id=as_uuid(row.memory_id),
        error=row.error,
        payload=dict(as_json(row.payload_json) or {}),
        created_at=must_aware(row.created_at),
        updated_at=must_aware(row.updated_at),
    )
