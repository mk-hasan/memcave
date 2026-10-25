"""Two-session agent loop on the in-memory store and the scripted model."""

import asyncio
from uuid import UUID

from memcave.adapters.models.fake import FakeModelProvider
from memcave.bootstrap import in_memory_manager
from memcave.domain.enums import EventType
from memcave.domain.models import ModelRequest
from memcave.integrations.langgraph.session import AgentTurnLoop

TENANT = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-0000000000a1")


async def main() -> None:
    manager = in_memory_manager()
    model = FakeModelProvider()
    first = AgentTurnLoop(manager, TENANT, USER)
    await first.start()
    opening = await first.ask("remember that I prefer concise answers")
    await first.record(EventType.TOOL_CALL, {"name": "search_memory"})
    await first.record(EventType.TOOL_RESULT, {"content": "logged tool output"})
    reply = await model.complete_structured(
        ModelRequest(
            task="answer",
            system_prompt="Use only the supplied context.",
            user_prompt=(
                "Question: remember that I prefer concise answers\n"
                f"{opening.context_block}"
            ),
            temperature=0.0,
            seed=0,
        )
    )
    await first.record(EventType.FINAL_ANSWER, {"content": reply.content})
    assert first.search_calls == 1
    await first.close(dream=True)

    second = AgentTurnLoop(manager, TENANT, USER)
    await second.start()
    recalled = await second.ask("how should you answer?")
    await second.record(EventType.TOOL_RESULT, {"content": "logged tool output"})
    assert second.search_calls == 1
    print(recalled.context_block)
    print("fenced", "<<<MEMCAVE_MEMORY>>>" in recalled.context_block)
    print("retrieved", "concise" in recalled.context_block.casefold())
    print("searches", first.search_calls, second.search_calls)


if __name__ == "__main__":
    asyncio.run(main())
