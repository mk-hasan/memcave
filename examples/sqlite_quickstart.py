"""SQLite quickstart. Creates a local database and does not need Docker."""

import asyncio
from pathlib import Path
from uuid import UUID

from memcave.bootstrap import Settings, build_manager
from memcave.domain.api import (
    AppendEventRequest,
    ExportMarkdownRequest,
    SearchMemoryRequest,
    StartSessionRequest,
)
from memcave.domain.enums import EventType

TENANT = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-0000000000a1")


async def main() -> None:
    root = Path(".memcave")
    root.mkdir(exist_ok=True)
    database = root / "quickstart.db"
    manager = await build_manager(
        Settings(
            storage_backend="sqlite",
            storage_dsn=f"sqlite+aiosqlite:///{database}",
        )
    )
    started = await manager.start_session(
        StartSessionRequest(tenant_id=TENANT, user_id=USER, title="sqlite quickstart")
    )
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=started.session.session_id,
            event_type=EventType.EXPLICIT_MEMORY_INSTRUCTION,
            payload={
                "instruction_type": "remember",
                "raw_text": "remember that the fiscal year starts in February",
            },
        )
    )
    found = await manager.search_memory(
        SearchMemoryRequest(
            tenant_id=TENANT, user_id=USER, query_text="when does the fiscal year start"
        )
    )
    exported = await manager.export_markdown(
        ExportMarkdownRequest(tenant_id=TENANT, user_id=USER, output_dir=str(root / "export"))
    )
    print(found.hits[0].content)
    print(exported.output_dir)


if __name__ == "__main__":
    asyncio.run(main())
