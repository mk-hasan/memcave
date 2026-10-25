"""Embedding provider selection and offline rebuilds."""

import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.conftest import TENANT, USER, start
from typer.testing import CliRunner

from memcave.adapters.embeddings.factory import build_embedding_provider
from memcave.adapters.embeddings.fake import FakeEmbeddingProvider
from memcave.adapters.embeddings.rebuild import apply_rebuild, plan_rebuild
from memcave.bootstrap import Settings, build_manager
from memcave.cli.app import app
from memcave.db.tables import EngineMetadataRow, MemoryEmbeddingRow
from memcave.domain.api import AddMemoryRequest, AppendEventRequest, DeleteMemoryRequest
from memcave.domain.enums import EventType, MemoryCategory
from memcave.domain.errors import ProviderError

runner = CliRunner()


class RecordingEmbedding(FakeEmbeddingProvider):
    def __init__(self, dimension: int) -> None:
        super().__init__(dimension)
        self.texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return await super().embed(texts)


def test_factory_defaults_to_fake_and_rejects_missing_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    provider = build_embedding_provider(settings.embedding_provider, settings.embedding_dimension)
    assert isinstance(provider, FakeEmbeddingProvider)
    assert provider.dimension == 64
    with pytest.raises(ProviderError, match="2000"):
        build_embedding_provider("fake", 2001)
    with pytest.raises(ProviderError, match="384"):
        build_embedding_provider("local", 64)
    with pytest.raises(ProviderError, match="1536"):
        build_embedding_provider("openai", 64)

    real_import = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError(name)
        if name == "openai" or name.startswith("openai."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr("builtins.__import__", blocked)
    with pytest.raises(ProviderError, match=r"memcave\[embeddings\]"):
        build_embedding_provider("local", 384)
    with pytest.raises(ProviderError, match=r"memcave\[openai\]"):
        build_embedding_provider("openai", 1536)


async def _snapshot(database: Path) -> tuple[int, list[tuple[str, int, int]]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            metadata = await session.get(EngineMetadataRow, "embedding_dimension")
            assert metadata is not None
            rows = (await session.scalars(select(MemoryEmbeddingRow))).all()
        parsed: list[tuple[str, int, int]] = []
        for row in rows:
            payload = row.embedding_json
            vector = payload if isinstance(payload, list) else []
            parsed.append((row.owner_id, row.dimension, len(vector)))
        return int(metadata.value), parsed
    finally:
        await engine.dispose()


async def test_startup_refuses_a_stored_dimension_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "mismatch.db"
    dsn = f"sqlite+aiosqlite:///{database}"
    manager = await build_manager(Settings(storage_backend="sqlite", storage_dsn=dsn))
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="kept",
            content="Keep this preference",
        )
    )
    before = await _snapshot(database)
    with pytest.raises(ProviderError, match="does not match"):
        await build_manager(
            Settings(storage_backend="sqlite", storage_dsn=dsn, embedding_dimension=32)
        )
    assert await _snapshot(database) == before
    assert before[0] == 64
    assert before[1]
    assert all(dimension == 64 and length == 64 for _owner, dimension, length in before[1])


async def test_sqlite_rebuild_resumes_and_skips_deleted_memory(tmp_path: Path) -> None:
    database = tmp_path / "rebuild.db"
    dsn = f"sqlite+aiosqlite:///{database}"
    first = FakeEmbeddingProvider(64)
    manager = await build_manager(
        Settings(
            storage_backend="sqlite",
            storage_dsn=dsn,
            embedding_provider="fake",
            embedding_dimension=first.dimension,
        )
    )
    assert isinstance(manager.deps.embeddings, FakeEmbeddingProvider)
    assert manager.deps.embeddings.dimension == first.dimension
    assert manager.deps.embeddings is not first
    session_id = await start(manager)
    await manager.append_event(
        AppendEventRequest(
            tenant_id=TENANT,
            user_id=USER,
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "searchable episodic note"},
        )
    )
    await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="kept",
            content="Keep this preference",
        )
    )
    removed = await manager.add_memory(
        AddMemoryRequest(
            tenant_id=TENANT,
            user_id=USER,
            category=MemoryCategory.PREFERENCE,
            title="gone",
            content="Suppressed secret fact",
        )
    )
    await manager.delete_memory(
        DeleteMemoryRequest(tenant_id=TENANT, user_id=USER, memory_id=removed.memory.memory_id)
    )
    engine = create_async_engine(dsn)
    replacement = RecordingEmbedding(32)
    assert isinstance(manager.deps.embeddings, FakeEmbeddingProvider)
    assert replacement is not manager.deps.embeddings

    def interrupt() -> None:
        raise RuntimeError("interrupted")

    try:
        plan = await plan_rebuild(engine, 32)
        assert plan.stored_dimension == 64
        assert plan.target_dimension == 32
        assert plan.active_vectors == 2
        with pytest.raises(RuntimeError, match="interrupted"):
            await apply_rebuild(engine, replacement, 32, before_metadata=interrupt)
        stored, rows = await _snapshot(database)
        assert stored == 64
        assert len(rows) == 3
        assert all(dimension == 64 and length == 64 for _owner, dimension, length in rows)
        await apply_rebuild(engine, replacement, 32)
        stored, rows = await _snapshot(database)
        assert stored == 32
        assert len(rows) == 2
        assert len({owner for owner, _dimension, _length in rows}) == 2
        assert all(dimension == 32 and length == 32 for _owner, dimension, length in rows)
        joined = "\n".join(replacement.texts)
        assert "Keep this preference" in joined
        assert "searchable episodic note" in joined
        assert "Suppressed secret fact" not in joined
        await apply_rebuild(engine, replacement, 32)
        again, again_rows = await _snapshot(database)
        assert again == 32
        assert len(again_rows) == 2
    finally:
        await engine.dispose()


def test_rebuild_dry_run_prints_dimensions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    database = tmp_path / "cli.db"
    dsn = f"sqlite+aiosqlite:///{database}"
    import asyncio

    asyncio.run(build_manager(Settings(storage_backend="sqlite", storage_dsn=dsn)))
    listed = runner.invoke(
        app,
        ["embeddings", "rebuild", "--dimension", "32", "--backend", "sqlite", "--dsn", dsn],
    )
    assert listed.exit_code == 0, listed.output
    assert "stored dimension 64" in listed.output
    assert "target dimension 32" in listed.output
    assert "active vectors 0" in listed.output
    assert "rebuilt" not in listed.output


@pytest.mark.embeddings
@pytest.mark.skipif(
    os.environ.get("MEMCAVE_LOCAL_EMBEDDINGS") != "1",
    reason="set MEMCAVE_LOCAL_EMBEDDINGS=1 to download the local embedding model",
)
async def test_local_model_returns_384_dimensions() -> None:
    provider = build_embedding_provider("local", 384)
    vectors = await provider.embed(["concise answers"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384


@pytest.mark.postgres
async def test_postgres_rebuild_replaces_the_vector_column() -> None:
    dsn = os.environ.get("MEMCAVE_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MEMCAVE_POSTGRES_DSN is not set")
    from sqlalchemy import text

    from memcave.adapters.sql.gateway import prepare_database

    engine = create_async_engine(dsn)
    changed = False
    try:
        await prepare_database(engine, 64)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            metadata = await session.get(EngineMetadataRow, "embedding_dimension")
        if metadata is None or int(metadata.value) != 64:
            pytest.skip("PostgreSQL database is not at embedding dimension 64")
        manager = await build_manager(Settings(storage_backend="postgres", storage_dsn=dsn))
        await manager.add_memory(
            AddMemoryRequest(
                tenant_id=TENANT,
                user_id=USER,
                category=MemoryCategory.PREFERENCE,
                title="kept",
                content="Keep this preference",
            )
        )
        first = FakeEmbeddingProvider(32)
        await apply_rebuild(engine, first, 32)
        changed = True
        async with engine.connect() as connection:
            dims = (
                await connection.execute(
                    text("SELECT vector_dims(embedding) FROM memory_embeddings")
                )
            ).all()
            index = (
                await connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE indexname = 'ix_embeddings_cosine'"
                    )
                )
            ).all()
        assert dims
        assert all(int(row[0]) == 32 for row in dims)
        assert index
        count = len(dims)
        second = FakeEmbeddingProvider(32)
        await apply_rebuild(engine, second, 32)
        async with engine.connect() as connection:
            again = (
                await connection.execute(text("SELECT count(*) FROM memory_embeddings"))
            ).scalar_one()
        assert int(again) == count
        stored, _rows = await _postgres_dimension(engine)
        assert stored == 32
    finally:
        if changed:
            await apply_rebuild(engine, FakeEmbeddingProvider(64), 64)
        await engine.dispose()


async def _postgres_dimension(engine: object) -> tuple[int, list[object]]:
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(engine, AsyncEngine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        metadata = await session.get(EngineMetadataRow, "embedding_dimension")
        assert metadata is not None
        return int(metadata.value), []
