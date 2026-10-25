"""One session of recall, event logging, and dreamflow over MemoryManager."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from memcave.domain.api import RunDreamflowRequest, SearchMemoryRequest, StartSessionRequest
from memcave.domain.enums import EventType
from memcave.integrations.langgraph.tools import (
    Tool,
    memory_tools,
    on_event,
    on_session_end,
    on_session_start,
)
from memcave.services.memory_manager import MemoryManager


@dataclass(frozen=True)
class TurnContext:
    """Index from session start and the fenced search block for this question."""

    index_markdown: str
    context_block: str


class AgentTurnLoop:
    """Log a session, inject recall, and dream after the session ends."""

    def __init__(
        self,
        manager: MemoryManager,
        tenant_id: UUID,
        user_id: UUID,
        *,
        memory_opt_out: bool = False,
        use_model: bool = False,
    ) -> None:
        self.manager = manager
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.memory_opt_out = memory_opt_out
        self.use_model = use_model
        self.session_id = uuid4()
        self.index_markdown = ""
        self._started = False
        self._closed = False

    @property
    def tools(self) -> dict[str, Tool]:
        """Fixed registry. Retrieved memory cannot add or rename these tools."""
        return memory_tools(self.manager, self.tenant_id, self.user_id)

    async def start(self) -> str:
        metadata: dict[str, Any] = {}
        if self.memory_opt_out:
            metadata["memory_opt_out"] = True
        self.index_markdown = await on_session_start(
            self.manager,
            StartSessionRequest(
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                session_id=self.session_id,
                memory_opt_out=self.memory_opt_out,
                metadata=metadata,
            ),
        )
        self._started = True
        return self.index_markdown

    async def record(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if not self._started:
            raise RuntimeError("start the session before recording an event")
        await on_event(
            self.manager,
            self.tenant_id,
            self.user_id,
            self.session_id,
            event_type,
            payload,
        )

    async def ask(self, question: str) -> TurnContext:
        """Record the user message and inject a delimited search result."""
        await self.record(EventType.USER_MESSAGE, {"content": question})
        if self.memory_opt_out:
            return TurnContext(index_markdown="", context_block="")
        found = await self.manager.search_memory(
            SearchMemoryRequest(
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                query_text=question,
            )
        )
        return TurnContext(
            index_markdown=self.index_markdown,
            context_block=found.context_block,
        )

    async def close(self) -> None:
        """End the session, then run dreamflow unless the user opted out."""
        if self._closed:
            return
        if not self._started:
            raise RuntimeError("start the session before closing it")
        await on_session_end(self.manager, self.tenant_id, self.user_id, self.session_id)
        self._closed = True
        if self.memory_opt_out:
            return
        await self.manager.run_dreamflow(
            RunDreamflowRequest(
                tenant_id=self.tenant_id,
                user_id=self.user_id,
                use_model=self.use_model,
            )
        )
