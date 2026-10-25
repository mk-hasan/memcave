"""LangGraph example. Requires `pip install memcave[langgraph]`."""

import asyncio
from uuid import UUID

from memcave.adapters.models.fake import FakeModelProvider
from memcave.bootstrap import in_memory_manager
from memcave.domain.api import AddMemoryRequest
from memcave.domain.enums import MemoryCategory
from memcave.integrations.langgraph.tools import build_recall_graph, memory_tools

TENANT = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-0000000000a1")


async def main() -> None:
    manager = in_memory_manager()
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="concise",
            content="Prefer concise answers.",
        )
    )
    tools = memory_tools(manager, TENANT, USER)
    print("tools", ", ".join(tools))
    graph = build_recall_graph(manager, TENANT, USER, FakeModelProvider())
    result = await graph.ainvoke({"question": "What is 2 + 2?", "opt_out": "no"})  # type: ignore[attr-defined]
    print(result["answer"])


if __name__ == "__main__":
    asyncio.run(main())
