"""Live check against the caller's OpenAI-compatible server."""

import os

import pytest
from pydantic import SecretStr
from tests.conftest import TENANT, USER

from memcave.bootstrap import Settings, in_memory_manager
from memcave.domain.api import (
    AppendEventRequest,
    EndSessionRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
    StartSessionRequest,
)
from memcave.domain.enums import EventType

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("MEMCAVE_LIVE") != "1",
    reason="set MEMCAVE_LIVE=1 to call a configured model",
)
async def test_live_model_returns_the_fenced_preference() -> None:
    settings = Settings(storage_backend="memory")
    missing: list[str] = []
    if settings.model_provider != "openai_compatible":
        missing.append("MEMCAVE_MODEL_PROVIDER=openai_compatible")
    if settings.embedding_provider != "openai_compatible":
        missing.append("MEMCAVE_EMBEDDING_PROVIDER=openai_compatible")
    if not settings.base_url.strip():
        missing.append("MEMCAVE_BASE_URL")
    if not settings.model.strip():
        missing.append("MEMCAVE_MODEL")
    if not settings.embedding_model.strip():
        missing.append("MEMCAVE_EMBEDDING_MODEL")
    secret = settings.api_key
    key = secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)
    if not key.strip():
        missing.append("MEMCAVE_API_KEY")
    if missing:
        pytest.fail("MEMCAVE_LIVE=1 needs " + ", ".join(missing))

    manager = in_memory_manager(settings)
    started = await manager.start_session(
        StartSessionRequest(tenant_id=TENANT, user_id=USER, title="live")
    )
    session_id = started.session.session_id
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "remember that I prefer concise answers"},
        )
    )
    await manager.end_session(
        EndSessionRequest(tenant_id=TENANT, user_id=USER, session_id=session_id)
    )
    await manager.run_dreamflow(RunDreamflowRequest(tenant_id=TENANT, user_id=USER, use_model=True))
    found = await manager.search_memory(
        SearchMemoryRequest(tenant_id=TENANT, user_id=USER, query_text="concise answers")
    )
    assert "<<<MEMCAVE_MEMORY>>>" in found.context_block
    assert "concise" in found.context_block.casefold()
