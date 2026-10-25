"""Optional LangGraph tools and hooks over MemoryManager."""

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from uuid import UUID

from memcave.domain.api import (
    AddMemoryRequest,
    AppendEventRequest,
    DeleteMemoryRequest,
    EndSessionRequest,
    FetchMemoryRequest,
    GetMemoryIndexRequest,
    SearchMemoryRequest,
    StartSessionRequest,
    UpdateMemoryRequest,
)
from memcave.domain.enums import EventType, MemoryCategory
from memcave.services.memory_manager import MemoryManager

Tool = Callable[..., Awaitable[str]]


def memory_tools(manager: MemoryManager, tenant_id: UUID, user_id: UUID) -> dict[str, Tool]:
    """Fixed tool registry. Memory text cannot add or rename tools."""

    async def search(query: str) -> str:
        result = await manager.search_memory(
            SearchMemoryRequest(tenant_id=tenant_id, user_id=user_id, query_text=query)
        )
        return result.context_block

    async def fetch(memory_id: str) -> str:
        result = await manager.fetch_memory(
            FetchMemoryRequest(tenant_id=tenant_id, user_id=user_id, memory_id=UUID(memory_id))
        )
        return result.memory.content

    async def add(category: str, title: str, content: str) -> str:
        result = await manager.add_memory(
            AddMemoryRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                category=MemoryCategory(category),
                title=title,
                content=content,
            )
        )
        return str(result.memory.memory_id)

    async def update(memory_id: str, expected_version: int, content: str) -> str:
        result = await manager.update_memory(
            UpdateMemoryRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=UUID(memory_id),
                expected_version=expected_version,
                content=content,
            )
        )
        return str(result.memory.version_number)

    async def delete(memory_id: str) -> str:
        result = await manager.delete_memory(
            DeleteMemoryRequest(tenant_id=tenant_id, user_id=user_id, memory_id=UUID(memory_id))
        )
        return result.memory.status.value

    return {
        "search_memory": search,
        "fetch_memory": fetch,
        "add_memory": add,
        "update_memory": update,
        "delete_memory": delete,
    }


async def on_session_start(manager: MemoryManager, request: StartSessionRequest) -> str:
    """Start a session and return the compact index unless the user opted out."""
    await manager.start_session(request)
    if request.memory_opt_out:
        return ""
    index = await manager.get_memory_index(
        GetMemoryIndexRequest(tenant_id=request.tenant_id, user_id=request.user_id)
    )
    return index.markdown


async def on_event(
    manager: MemoryManager,
    tenant_id: UUID,
    user_id: UUID,
    session_id: UUID,
    event_type: EventType,
    payload: dict[str, Any],
) -> None:
    await manager.append_event(
        AppendEventRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
    )


async def on_session_end(
    manager: MemoryManager, tenant_id: UUID, user_id: UUID, session_id: UUID
) -> None:
    await manager.end_session(
        EndSessionRequest(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
    )


def build_recall_graph(
    manager: MemoryManager,
    tenant_id: UUID,
    user_id: UUID,
    model: object,
) -> object:
    """Compile a two-node graph. The model is any object with complete_structured."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("install memcave[langgraph] to build the example graph") from exc

    from memcave.domain.models import ModelRequest

    class GraphState(TypedDict, total=False):
        question: str
        context: str
        answer: str
        opt_out: str

    async def recall(state: GraphState) -> GraphState:
        question = state.get("question", "")
        if state.get("opt_out") == "yes":
            return {"question": question, "context": ""}
        result = await manager.search_memory(
            SearchMemoryRequest(tenant_id=tenant_id, user_id=user_id, query_text=question)
        )
        return {"question": question, "context": result.context_block}

    async def answer(state: GraphState) -> GraphState:
        response = await model.complete_structured(  # type: ignore[attr-defined]
            ModelRequest(
                task="answer",
                system_prompt="Use only the supplied context.",
                user_prompt=(
                    f"Tools: list_tables, describe_table, run_query\n"
                    f"Question: {state.get('question', '')}\n{state.get('context', '')}"
                ),
                temperature=0.0,
                seed=0,
            )
        )
        return {"answer": response.content}

    graph: Any = StateGraph(GraphState)
    graph.add_node("recall", recall)
    graph.add_node("answer", answer)
    graph.set_entry_point("recall")
    graph.add_edge("recall", "answer")
    graph.add_edge("answer", END)
    return graph.compile()
