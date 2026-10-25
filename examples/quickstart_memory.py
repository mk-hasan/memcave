"""In-memory quickstart. No database and no network."""

import asyncio
from uuid import UUID

from memcave.bootstrap import in_memory_manager
from memcave.domain.api import (
    AppendEventRequest,
    EndSessionRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
    StartSessionRequest,
)
from memcave.domain.enums import EventType


async def main() -> None:
    manager = in_memory_manager()
    tenant = UUID("00000000-0000-4000-8000-000000000001")
    user = UUID("00000000-0000-4000-8000-0000000000a1")
    started = await manager.start_session(
        StartSessionRequest(tenant_id=tenant, user_id=user, title="quickstart")
    )
    await manager.append_event(
        AppendEventRequest(
            tenant_id=tenant,
            user_id=user,
            session_id=started.session.session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={
                "instruction_type": "remember",
                "raw_text": "remember that I prefer concise answers",
            },
        )
    )
    await manager.end_session(
        EndSessionRequest(
            tenant_id=tenant, user_id=user, session_id=started.session.session_id
        )
    )
    await manager.run_dreamflow(RunDreamflowRequest(tenant_id=tenant, user_id=user))
    found = await manager.search_memory(
        SearchMemoryRequest(tenant_id=tenant, user_id=user, query_text="concise answers")
    )
    print(found.hits[0].content)
    print("fenced", "<<<MEMCAVE_MEMORY>>>" in found.context_block)


if __name__ == "__main__":
    asyncio.run(main())
