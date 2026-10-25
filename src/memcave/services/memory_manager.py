"""Public facade over stores, retrieval, and dreamflow."""

from datetime import datetime
from uuid import uuid4

from memcave.domain.api import (
    AddMemoryRequest,
    AddMemoryResponse,
    AppendEventRequest,
    AppendEventResponse,
    DeleteMemoryRequest,
    DeleteMemoryResponse,
    EndSessionRequest,
    EndSessionResponse,
    EraseUserDataRequest,
    EraseUserDataResponse,
    ExportMarkdownRequest,
    ExportMarkdownResponse,
    FetchMemoryRequest,
    FetchMemoryResponse,
    GetMemoryIndexRequest,
    GetMemoryIndexResponse,
    IndexEntry,
    ListEventsRequest,
    ListEventsResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    RunDreamflowRequest,
    RunDreamflowResponse,
    SearchMemoryRequest,
    SearchMemoryResponse,
    StartSessionRequest,
    StartSessionResponse,
    UpdateMemoryRequest,
    UpdateMemoryResponse,
)
from memcave.domain.enums import AuditAction, DreamflowState, SessionStatus
from memcave.domain.errors import NotFoundError, ValidationError
from memcave.domain.models import AuditEvent, RetrievalConfig, Session, Tenant, TenantScope, User
from memcave.dreamflow.pipeline import DreamflowPipeline
from memcave.ports.protocols import (
    Clock,
    DerivedMemoryStore,
    EmbeddingProvider,
    EpisodicStore,
    EventSink,
    MemoryWritePolicy,
    ModelProvider,
    RedactionPolicy,
    Reranker,
    RetentionPolicy,
    Tracer,
    VectorStore,
)
from memcave.services.operations import (
    Deps,
    add_memory,
    append_event,
    delete_memory,
    erase_user,
    export_markdown,
    fetch_memory,
    index_markdown,
    memories_for_index,
    search_memory,
    update_memory,
)
from memcave.services.render import one_line


class MemoryManager:
    """Framework-neutral memory lifecycle API."""

    def __init__(
        self,
        episodic_store: EpisodicStore,
        derived_store: DerivedMemoryStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        model_provider: ModelProvider,
        clock: Clock,
        event_sink: EventSink,
        redaction_policy: RedactionPolicy,
        retention_policy: RetentionPolicy,
        write_policy: MemoryWritePolicy,
        tracer: Tracer,
        reranker: Reranker | None = None,
        *,
        retrieval_config: RetrievalConfig | None = None,
        pipeline: DreamflowPipeline | None = None,
    ) -> None:
        from memcave.services.runtime import IdentityReranker

        self.model_provider = model_provider
        self.deps = Deps(
            episodic=episodic_store,
            derived=derived_store,
            vectors=vector_store,
            embeddings=embedding_provider,
            clock=clock,
            sink=event_sink,
            redaction=redaction_policy,
            retention=retention_policy,
            write_policy=write_policy,
            tracer=tracer,
            reranker=reranker or IdentityReranker(),
            config=retrieval_config or RetrievalConfig(),
        )
        self.pipeline = pipeline or DreamflowPipeline(model_provider)

    async def start_session(self, request: StartSessionRequest) -> StartSessionResponse:
        now = self.deps.clock.now()
        await self.deps.episodic.upsert_tenant(
            Tenant(
                tenant_id=request.tenant_id,
                name=request.tenant_name or str(request.tenant_id),
                created_at=now,
            )
        )
        await self.deps.episodic.upsert_user(
            User(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                external_id=request.external_id,
                display_name=request.user_display_name,
                memory_opt_out=request.memory_opt_out,
                created_at=now,
            )
        )
        session = Session(
            session_id=request.session_id or uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            title=request.title,
            started_at=now,
            metadata=dict(request.metadata),
        )
        stored = await self.deps.episodic.create_session(session)
        return StartSessionResponse(session=stored)

    async def append_event(self, request: AppendEventRequest) -> AppendEventResponse:
        event, memory = await append_event(self.deps, request)
        return AppendEventResponse(event=event, memory=memory)

    async def end_session(self, request: EndSessionRequest) -> EndSessionResponse:
        scope = TenantScope(tenant_id=request.tenant_id, user_id=request.user_id)
        session = await self.deps.episodic.get_session(scope, request.session_id)
        if session is None or session.user_id != request.user_id:
            raise NotFoundError("session was not found")
        ended = session.model_copy(
            update={
                "status": SessionStatus.ENDED,
                "ended_at": self.deps.clock.now(),
                "dreamflow_state": DreamflowState.PENDING,
            }
        )
        stored = await self.deps.episodic.save_session(ended)
        await self.deps.sink.emit(
            AuditEvent(
                audit_id=uuid4(),
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                actor=request.actor,
                action=AuditAction.UPDATE,
                resource_type="session",
                resource_id=str(stored.session_id),
                occurred_at=self.deps.clock.now(),
                metadata={"status": "ended"},
            )
        )
        return EndSessionResponse(session=stored)

    async def list_sessions(self, request: ListSessionsRequest) -> ListSessionsResponse:
        self._check_page(request.limit)
        scope = TenantScope(tenant_id=request.tenant_id, user_id=request.user_id)
        sessions, cursor = await self.deps.episodic.list_sessions(
            scope, limit=request.limit, cursor=request.cursor
        )
        return ListSessionsResponse(sessions=sessions, next_cursor=cursor)

    async def list_events(self, request: ListEventsRequest) -> ListEventsResponse:
        self._check_page(request.limit)
        scope = TenantScope(tenant_id=request.tenant_id, user_id=request.user_id)
        events, cursor = await self.deps.episodic.list_events(
            scope,
            session_id=request.session_id,
            event_type=request.event_type,
            processing_state=request.processing_state,
            start=request.start,
            end=request.end,
            limit=request.limit,
            cursor=request.cursor,
            user_id=request.user_id,
        )
        return ListEventsResponse(events=events, next_cursor=cursor)

    async def search_memory(self, request: SearchMemoryRequest) -> SearchMemoryResponse:
        hits, truncated, context, audit_id, reranker_error = await search_memory(self.deps, request)
        return SearchMemoryResponse(
            hits=hits,
            truncated=truncated,
            context_block=context,
            audit_id=audit_id,
            reranker_error=reranker_error,
        )

    async def fetch_memory(self, request: FetchMemoryRequest) -> FetchMemoryResponse:
        return FetchMemoryResponse(memory=await fetch_memory(self.deps, request))

    async def add_memory(self, request: AddMemoryRequest) -> AddMemoryResponse:
        return AddMemoryResponse(memory=await add_memory(self.deps, request))

    async def update_memory(self, request: UpdateMemoryRequest) -> UpdateMemoryResponse:
        return UpdateMemoryResponse(memory=await update_memory(self.deps, request))

    async def delete_memory(self, request: DeleteMemoryRequest) -> DeleteMemoryResponse:
        return DeleteMemoryResponse(memory=await delete_memory(self.deps, request))

    async def get_memory_index(self, request: GetMemoryIndexRequest) -> GetMemoryIndexResponse:
        as_of = request.as_of or self.deps.clock.now()
        memories = await memories_for_index(
            self.deps, request.tenant_id, request.user_id, as_of, request.include_shared
        )
        entries = [
            IndexEntry(
                logical_path=memory.logical_path,
                title=memory.title,
                category=memory.category,
                status=memory.status,
                confidence=memory.confidence,
                summary=one_line(memory.content),
            )
            for memory in memories
        ]
        return GetMemoryIndexResponse(entries=entries, markdown=index_markdown(memories))

    async def run_dreamflow(self, request: RunDreamflowRequest) -> RunDreamflowResponse:
        run = await self.pipeline.run(self.deps, request)
        return RunDreamflowResponse(run=run)

    async def export_markdown(self, request: ExportMarkdownRequest) -> ExportMarkdownResponse:
        directory, files, markdown = await export_markdown(self.deps, request)
        return ExportMarkdownResponse(output_dir=directory, files=files, markdown=markdown)

    async def erase_user_data(self, request: EraseUserDataRequest) -> EraseUserDataResponse:
        counts, audit_id = await erase_user(
            self.deps, request.tenant_id, request.user_id, request.actor
        )
        return EraseUserDataResponse(counts=counts, audit_id=audit_id)

    def _check_page(self, limit: int) -> None:
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200")


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationError("timestamps must be timezone-aware")
    return value
