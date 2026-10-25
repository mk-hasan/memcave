"""Offline tests for the OpenAI-compatible chat and embedding adapter."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from tests.conftest import TENANT, USER, start

from memcave.adapters.embeddings.openai import OpenAICompatibleEmbeddingProvider
from memcave.adapters.memory.stores import InMemoryVectorStore
from memcave.adapters.models.factory import build_model_provider
from memcave.adapters.models.openai import OpenAICompatibleModelProvider
from memcave.bootstrap import Settings, build_manager, in_memory_manager, provider_link
from memcave.domain.api import AddMemoryRequest
from memcave.domain.enums import MemoryCategory
from memcave.domain.errors import ProviderError
from memcave.domain.models import ModelRequest, TenantScope
from memcave.services.memory_manager import MemoryManager


class _ChatClient:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict[str, object]] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        message = SimpleNamespace(content=content)
        usage = SimpleNamespace(prompt_tokens=2, completion_tokens=2)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _Embeddings:
    def __init__(self, length: int) -> None:
        self.length = length
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        vector = [0.0] * self.length
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=vector)])


class _EmbedClient:
    def __init__(self, length: int) -> None:
        self.embeddings = _Embeddings(length)


def test_repr_omits_the_api_key() -> None:
    settings = Settings(api_key=SecretStr("super-secret-value"))
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in repr(provider_link(settings))


def test_memory_root_uses_the_model_factory() -> None:
    settings = Settings(
        storage_backend="memory",
        model_provider="openai_compatible",
        base_url="http://localhost:11434/v1",
        model="chat",
    )
    with pytest.raises(ProviderError, match="MEMCAVE_API_KEY"):
        in_memory_manager(settings)


def test_fake_model_stays_the_default() -> None:
    provider = build_model_provider("fake", fallback_model="fake-memcave")
    assert provider.model_name == "fake-memcave"  # type: ignore[attr-defined]
    with pytest.raises(ProviderError, match="unknown model provider"):
        build_model_provider("openai")


def test_missing_openai_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai" or name.startswith("openai."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ProviderError, match=r"memcave\[openai\]"):
        OpenAICompatibleModelProvider(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            model="chat",
            timeout=1,
        )
    with pytest.raises(ProviderError, match=r"memcave\[openai\]"):
        OpenAICompatibleEmbeddingProvider(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            model="embed",
            dimension=32,
            timeout=1,
        )


async def test_sqlite_root_uses_the_embedding_factory(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    database = tmp_path / "adapter.db"
    real_import = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai" or name.startswith("openai."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr("builtins.__import__", blocked)
    settings = Settings(
        storage_backend="sqlite",
        storage_dsn=f"sqlite+aiosqlite:///{database}",
        embedding_provider="openai_compatible",
        embedding_dimension=32,
        embedding_model="embed",
        base_url="http://localhost:11434/v1",
        api_key=SecretStr("ollama"),
    )
    with pytest.raises(ProviderError, match=r"memcave\[openai\]"):
        await build_manager(settings)
    assert not database.exists()


async def test_chat_retries_invalid_extraction_once() -> None:
    client = _ChatClient(["not json", '{"candidates": []}'])
    provider = OpenAICompatibleModelProvider(
        base_url="http://localhost:11434/v1",
        api_key="super-secret-value",
        model="chat",
        timeout=5,
        client=client,
    )
    response = await provider.complete_structured(
        ModelRequest(
            task="extract",
            system_prompt="schema",
            user_prompt="remember that I prefer concise answers",
            seed=0,
        )
    )
    assert response.content == '{"candidates": []}'
    assert len(client.calls) == 2
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert client.calls[0]["seed"] == 0
    assert "super-secret-value" not in repr(provider)
    assert "api_key" not in vars(provider)

    failing = _ChatClient(["not json", "still not json"])
    bad = OpenAICompatibleModelProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="chat",
        timeout=5,
        client=failing,
    )
    with pytest.raises(ProviderError, match="model returned invalid JSON"):
        await bad.complete_structured(
            ModelRequest(task="extract", system_prompt="schema", user_prompt="hello")
        )
    assert len(failing.calls) == 2


async def test_wrong_embedding_length_writes_nothing(memory_manager: MemoryManager) -> None:
    await start(memory_manager)
    client = _EmbedClient(length=2)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://localhost:11434/v1",
        api_key="super-secret-value",
        model="embed",
        dimension=4,
        timeout=1,
        client=client,
    )
    assert "super-secret-value" not in repr(provider)
    memory_manager.deps.embeddings = provider
    with pytest.raises(ProviderError, match="declared dimension"):
        await memory_manager.add_memory(
            AddMemoryRequest(
                tenant_id=TENANT,
                user_id=USER,
                category=MemoryCategory.PREFERENCE,
                title="concise",
                content="Prefer concise answers.",
            )
        )
    scope = TenantScope(tenant_id=TENANT, user_id=USER)
    assert await memory_manager.deps.derived.list_user_memories(scope, USER) == []
    store = memory_manager.deps.vectors
    assert isinstance(store, InMemoryVectorStore)
    assert store.data.embeddings == {}
    assert client.embeddings.calls
    assert client.embeddings.calls[0]["model"] == "embed"
