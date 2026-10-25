"""SQL stores for SQLite and PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memcave.db.tables import (
    Base,
    EngineMetadataRow,
    SessionEventRow,
    SessionRow,
    TenantRow,
    UserRow,
)
from memcave.domain.enums import (
    DreamflowState,
    EventType,
    ProcessingState,
    SessionStatus,
)
from memcave.domain.errors import (
    NotFoundError,
    ProviderError,
    TenantIsolationError,
)
from memcave.domain.models import (
    Session,
    SessionEvent,
    Tenant,
    TenantScope,
    User,
)
from memcave.services.text import decode_cursor, encode_cursor, tokenize


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def must_aware(value: datetime | None) -> datetime:
    current = aware(value)
    if current is None:
        raise NotFoundError("missing timestamp")
    return current


def as_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def as_json(value: Any) -> Any:
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded
    return value


class SqlGateway:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.factory = async_sessionmaker(engine, expire_on_commit=False)

    async def lexical_owner_ids(self, tenant_id: str, query: str) -> list[str]:
        tokens = tokenize(query)
        if not tokens:
            return []
        probe = tokens[0]
        async with self.factory() as session:
            dialect = self.engine.dialect.name
            try:
                if dialect == "sqlite":
                    rows = await session.execute(
                        text(
                            "SELECT owner_id FROM text_search "
                            "WHERE tenant_id = :tenant AND text_search MATCH :probe"
                        ),
                        {"tenant": tenant_id, "probe": probe},
                    )
                    return [str(row[0]) for row in rows]
                rows = await session.execute(
                    text(
                        "SELECT event_id FROM session_events WHERE tenant_id = :tenant "
                        "AND to_tsvector('english', search_text) "
                        "@@ plainto_tsquery('english', :probe) "
                        "UNION SELECT version_id FROM memory_versions "
                        "WHERE tenant_id = :tenant "
                        "AND to_tsvector('english', search_text) "
                        "@@ plainto_tsquery('english', :probe)"
                    ),
                    {"tenant": tenant_id, "probe": probe},
                )
                return [str(row[0]) for row in rows]
            except Exception:
                return []


async def prepare_database(engine: AsyncEngine, dimension: int = 64) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        dialect = engine.dialect.name
        if dialect == "sqlite":
            await conn.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5("
                "owner_kind UNINDEXED, owner_id UNINDEXED, tenant_id UNINDEXED, body)"
            )
        elif dialect == "postgresql":
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.exec_driver_sql(
                "ALTER TABLE memory_embeddings ADD COLUMN IF NOT EXISTS "
                f"embedding vector({int(dimension)})"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_embeddings_cosine ON memory_embeddings "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_events_tsv ON session_events "
                "USING gin (to_tsvector('english', search_text))"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_versions_tsv ON memory_versions "
                "USING gin (to_tsvector('english', search_text))"
            )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        current = await session.get(EngineMetadataRow, "embedding_dimension")
        if current is None:
            session.add(EngineMetadataRow(key="embedding_dimension", value=str(dimension)))
            await session.commit()
        elif int(current.value) != dimension:
            raise ProviderError(
                f"stored embedding dimension {current.value} does not match provider {dimension}"
            )


async def index_text(
    session: AsyncSession, kind: str, owner_id: str, tenant_id: str, body: str
) -> None:
    if session.bind is None or session.bind.dialect.name != "sqlite":
        return
    await session.execute(
        text("DELETE FROM text_search WHERE owner_id = :owner"),
        {"owner": owner_id},
    )
    await session.execute(
        text(
            "INSERT INTO text_search (owner_kind, owner_id, tenant_id, body) "
            "VALUES (:kind, :owner, :tenant, :body)"
        ),
        {"kind": kind, "owner": owner_id, "tenant": tenant_id, "body": body},
    )


def _page[T](
    rows: list[T], limit: int, cursor: str | None, key_fn: Any
) -> tuple[list[T], str | None]:
    ordered = sorted(rows, key=key_fn)
    if cursor is not None:
        moment, identifier = decode_cursor(cursor)
        ordered = [item for item in ordered if key_fn(item) > (moment, identifier)]
    page = ordered[:limit]
    if len(ordered) <= limit or not page:
        return page, None
    last = key_fn(page[-1])
    return page, encode_cursor(last[0], last[1])


class SqlEpisodicStore:
    def __init__(self, gateway: SqlGateway) -> None:
        self.gateway = gateway

    async def upsert_tenant(self, tenant: Tenant) -> Tenant:
        async with self.gateway.factory() as session:
            row = await session.get(TenantRow, str(tenant.tenant_id))
            if row is None:
                session.add(
                    TenantRow(
                        tenant_id=str(tenant.tenant_id),
                        name=tenant.name,
                        created_at=tenant.created_at,
                        metadata_json=tenant.metadata,
                    )
                )
            else:
                row.name = tenant.name
                row.metadata_json = tenant.metadata
            await session.commit()
        return tenant

    async def upsert_user(self, user: User) -> User:
        async with self.gateway.factory() as session:
            row = await session.get(UserRow, str(user.user_id))
            payload = dict(
                tenant_id=str(user.tenant_id),
                external_id=user.external_id,
                display_name=user.display_name,
                memory_opt_out=int(user.memory_opt_out),
                created_at=user.created_at,
                metadata_json=user.metadata,
            )
            if row is None:
                session.add(UserRow(user_id=str(user.user_id), **payload))
            else:
                if row.tenant_id != str(user.tenant_id):
                    raise TenantIsolationError("user belongs to another tenant")
                for key, value in payload.items():
                    setattr(row, key, value)
            await session.commit()
        return user

    async def get_user(self, scope: TenantScope, user_id: UUID) -> User | None:
        async with self.gateway.factory() as session:
            row = await session.get(UserRow, str(user_id))
        if row is None or row.tenant_id != str(scope.tenant_id):
            return None
        return _user(row)

    async def create_session(self, session_model: Session) -> Session:
        async with self.gateway.factory() as session:
            session.add(_session_row(session_model))
            await session.commit()
        return session_model

    async def get_session(self, scope: TenantScope, session_id: UUID) -> Session | None:
        async with self.gateway.factory() as session:
            row = await session.get(SessionRow, str(session_id))
        if row is None or row.tenant_id != str(scope.tenant_id):
            return None
        return _session(row)

    async def list_sessions(
        self, scope: TenantScope, *, limit: int, cursor: str | None
    ) -> tuple[list[Session], str | None]:
        async with self.gateway.factory() as session:
            stmt = select(SessionRow).where(SessionRow.tenant_id == str(scope.tenant_id))
            if scope.user_id is not None:
                stmt = stmt.where(SessionRow.user_id == str(scope.user_id))
            rows = (await session.scalars(stmt)).all()
        return _page(
            [_session(row) for row in rows],
            limit,
            cursor,
            lambda item: (item.started_at, item.session_id),
        )

    async def save_session(self, session_model: Session) -> Session:
        async with self.gateway.factory() as session:
            row = await session.get(SessionRow, str(session_model.session_id))
            if row is None or row.tenant_id != str(session_model.tenant_id):
                raise NotFoundError("session was not found")
            for key, value in _session_row(session_model).__dict__.items():
                if key.startswith("_"):
                    continue
                setattr(row, key, value)
            await session.commit()
        return session_model

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        async with self.gateway.factory() as session:
            parent = await session.get(SessionRow, str(event.session_id))
            if (
                parent is None
                or parent.tenant_id != str(event.tenant_id)
                or parent.user_id != str(event.user_id)
            ):
                raise TenantIsolationError("event scope does not match the session")
            current = await session.scalar(
                select(SessionEventRow.sequence)
                .where(SessionEventRow.session_id == str(event.session_id))
                .order_by(SessionEventRow.sequence.desc())
            )
            sequence = 1 + int(current or 0)
            stored = event.model_copy(update={"sequence": sequence})
            row = _event_row(stored)
            session.add(row)
            await index_text(session, "event", row.event_id, row.tenant_id, row.search_text)
            await session.commit()
        return stored

    async def delete_event(self, scope: TenantScope, event_id: UUID) -> None:
        async with self.gateway.factory() as session:
            row = await session.get(SessionEventRow, str(event_id))
            if row is None:
                return
            if row.tenant_id != str(scope.tenant_id):
                raise TenantIsolationError("event belongs to another tenant")
            await session.delete(row)
            await session.commit()

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
        async with self.gateway.factory() as session:
            stmt = select(SessionEventRow).where(SessionEventRow.tenant_id == str(scope.tenant_id))
            if user_id is not None:
                stmt = stmt.where(SessionEventRow.user_id == str(user_id))
            if session_id is not None:
                stmt = stmt.where(SessionEventRow.session_id == str(session_id))
            if event_type is not None:
                stmt = stmt.where(SessionEventRow.event_type == event_type.value)
            if processing_state is not None:
                stmt = stmt.where(SessionEventRow.processing_state == processing_state.value)
            rows = (await session.scalars(stmt)).all()
        events = [_event(row) for row in rows]
        if start is not None:
            events = [event for event in events if event.occurred_at >= start]
        if end is not None:
            events = [event for event in events if event.occurred_at < end]
        return _page(events, limit, cursor, lambda item: (item.occurred_at, item.event_id))

    async def set_processing_state(
        self,
        scope: TenantScope,
        event_ids: list[UUID] | tuple[UUID, ...],
        state: ProcessingState,
    ) -> None:
        async with self.gateway.factory() as session:
            for event_id in event_ids:
                row = await session.get(SessionEventRow, str(event_id))
                if row is None or row.tenant_id != str(scope.tenant_id):
                    continue
                row.processing_state = state.value
            await session.commit()

    async def eligible_sessions(
        self, scope: TenantScope, *, now: datetime, max_age: timedelta | None
    ) -> list[Session]:
        sessions, _cursor = await self.list_sessions(scope, limit=10_000, cursor=None)
        users = {}
        found: list[Session] = []
        for session_model in sessions:
            if session_model.status is not SessionStatus.ENDED:
                continue
            if session_model.dreamflow_state not in {DreamflowState.PENDING, DreamflowState.FAILED}:
                continue
            if session_model.metadata.get("memory_opt_out") is True:
                continue
            user = users.get(session_model.user_id)
            if user is None:
                user = await self.get_user(scope, session_model.user_id)
                users[session_model.user_id] = user
            if user is not None and user.memory_opt_out:
                continue
            anchor = session_model.ended_at or session_model.started_at
            if max_age is not None and now - anchor > max_age:
                continue
            found.append(session_model)
        return found

    async def erase_user(self, scope: TenantScope) -> tuple[int, int]:
        if scope.user_id is None:
            return 0, 0
        async with self.gateway.factory() as session:
            sessions = (
                await session.scalars(
                    select(SessionRow).where(
                        SessionRow.tenant_id == str(scope.tenant_id),
                        SessionRow.user_id == str(scope.user_id),
                    )
                )
            ).all()
            events = (
                await session.scalars(
                    select(SessionEventRow).where(
                        SessionEventRow.tenant_id == str(scope.tenant_id),
                        SessionEventRow.user_id == str(scope.user_id),
                    )
                )
            ).all()
            for row in events:
                await session.delete(row)
            for row in sessions:
                await session.delete(row)
            await session.commit()
            return len(sessions), len(events)


def _user(row: UserRow) -> User:
    return User(
        user_id=UUID(row.user_id),
        tenant_id=UUID(row.tenant_id),
        external_id=row.external_id,
        display_name=row.display_name,
        memory_opt_out=bool(row.memory_opt_out),
        created_at=must_aware(row.created_at),
        metadata=dict(as_json(row.metadata_json) or {}),
    )


def _session_row(model: Session) -> SessionRow:
    return SessionRow(
        session_id=str(model.session_id),
        tenant_id=str(model.tenant_id),
        user_id=str(model.user_id),
        title=model.title,
        status=model.status.value,
        started_at=model.started_at,
        ended_at=model.ended_at,
        dreamflow_state=model.dreamflow_state.value,
        dreamflow_run_id=str(model.dreamflow_run_id) if model.dreamflow_run_id else None,
        metadata_json=model.metadata,
    )


def _session(row: SessionRow) -> Session:
    return Session(
        session_id=UUID(row.session_id),
        tenant_id=UUID(row.tenant_id),
        user_id=UUID(row.user_id),
        title=row.title,
        status=SessionStatus(row.status),
        started_at=must_aware(row.started_at),
        ended_at=aware(row.ended_at),
        dreamflow_state=DreamflowState(row.dreamflow_state),
        dreamflow_run_id=as_uuid(row.dreamflow_run_id),
        metadata=dict(as_json(row.metadata_json) or {}),
    )


def _event_row(model: SessionEvent) -> SessionEventRow:
    from memcave.services.text import event_search_text

    return SessionEventRow(
        event_id=str(model.event_id),
        tenant_id=str(model.tenant_id),
        user_id=str(model.user_id),
        session_id=str(model.session_id),
        sequence=model.sequence,
        event_type=model.event_type.value,
        occurred_at=model.occurred_at,
        payload_json=model.payload,
        processing_state=model.processing_state.value,
        metadata_json=model.metadata,
        search_text=event_search_text(model.payload),
    )


def _event(row: SessionEventRow) -> SessionEvent:
    return SessionEvent(
        event_id=UUID(row.event_id),
        tenant_id=UUID(row.tenant_id),
        user_id=UUID(row.user_id),
        session_id=UUID(row.session_id),
        sequence=row.sequence,
        event_type=EventType(row.event_type),
        occurred_at=must_aware(row.occurred_at),
        payload=dict(as_json(row.payload_json) or {}),
        processing_state=ProcessingState(row.processing_state),
        metadata=dict(as_json(row.metadata_json) or {}),
    )
