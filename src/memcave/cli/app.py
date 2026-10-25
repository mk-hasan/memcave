"""Typer command line for local memcave stores."""

import asyncio
import json
import tomllib
from pathlib import Path
from uuid import UUID

import typer

from memcave.bootstrap import Settings, build_manager, configured_embeddings
from memcave.domain.api import (
    EraseUserDataRequest,
    ExportMarkdownRequest,
    FetchMemoryRequest,
    GetMemoryIndexRequest,
    ListEventsRequest,
    ListSessionsRequest,
    RunDreamflowRequest,
    SearchMemoryRequest,
)
from memcave.services.memory_manager import MemoryManager

app = typer.Typer(no_args_is_help=True, help="Persistent memory for AI agents.")
embeddings_app = typer.Typer(no_args_is_help=True, help="Rebuild stored embedding vectors.")
session_app = typer.Typer(no_args_is_help=True, help="Inspect sessions.")
memory_app = typer.Typer(no_args_is_help=True, help="Search and export memory.")
dream_app = typer.Typer(no_args_is_help=True, help="Run dreamflow.")
benchmark_app = typer.Typer(no_args_is_help=True, help="Run and render benchmarks.")
user_app = typer.Typer(no_args_is_help=True, help="Erase a user's stored data.")
app.add_typer(session_app, name="session")
app.add_typer(embeddings_app, name="embeddings")
review_app = typer.Typer(no_args_is_help=True, help="Accept or reject candidate memories.")
memory_app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")
app.add_typer(dream_app, name="dreamflow")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(user_app, name="user")


def _settings(backend: str | None, dsn: str | None) -> Settings:
    data: dict[str, object] = {}
    path = Path(".memcave/config.toml")
    if path.exists():
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    current = Settings(**data)  # type: ignore[arg-type]
    updates: dict[str, object] = {}
    if backend is not None:
        updates["storage_backend"] = backend
    if dsn is not None:
        updates["storage_dsn"] = dsn
    if updates:
        return current.model_copy(update=updates)
    return current


def _manager(backend: str | None, dsn: str | None) -> MemoryManager:
    return asyncio.run(build_manager(_settings(backend, dsn)))


BackendOpt = typer.Option(None, "--backend", help="memory, sqlite, or postgres")
DsnOpt = typer.Option(None, "--dsn", help="SQLAlchemy database URL")


@app.command()
def init(
    backend: str = typer.Option("sqlite", "--backend"),
    dsn: str = typer.Option("sqlite+aiosqlite:///./.memcave/memcave.db", "--dsn"),
) -> None:
    """Create local config and prepare a SQLite database."""
    root = Path(".memcave")
    root.mkdir(exist_ok=True)
    config = root / "config.toml"
    config.write_text(
        f'storage_backend = "{backend}"\nstorage_dsn = "{dsn}"\n',
        encoding="utf-8",
    )
    if backend == "sqlite":
        asyncio.run(build_manager(Settings(storage_backend="sqlite", storage_dsn=dsn)))
    typer.echo(f"wrote {config}")


@embeddings_app.command("rebuild")
def embeddings_rebuild(
    dimension: int = typer.Option(..., "--dimension", help="Target vector dimension"),
    yes: bool = typer.Option(False, "--yes", help="Replace stored vectors"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Re-embed active memories and episodic rows with the configured provider."""
    settings = _settings(backend, dsn)
    if settings.storage_backend not in {"sqlite", "postgres"}:
        raise typer.BadParameter("embeddings rebuild needs a sqlite or postgres database")
    _ensure_sqlite_directory(settings.storage_dsn)
    plan = asyncio.run(_rebuild(settings, dimension, apply=yes))
    typer.echo(f"stored dimension {plan.stored_dimension}")
    typer.echo(f"target dimension {plan.target_dimension}")
    typer.echo(f"active vectors {plan.active_vectors}")
    if yes:
        typer.echo("rebuilt")


async def _rebuild(settings: Settings, dimension: int, *, apply: bool) -> object:
    from sqlalchemy.ext.asyncio import create_async_engine

    from memcave.adapters.embeddings.rebuild import apply_rebuild, plan_rebuild

    if settings.storage_backend == "postgres":
        import asyncpg  # noqa: F401

    engine = create_async_engine(settings.storage_dsn)
    try:
        if not apply:
            return await plan_rebuild(engine, dimension)
        provider = configured_embeddings(settings, dimension)
        return await apply_rebuild(engine, provider, dimension)
    finally:
        await engine.dispose()


@app.command()
def migrate(dsn: str = DsnOpt) -> None:
    """Upgrade the database to the current Alembic revision."""
    from alembic import command
    from alembic.config import Config

    settings = _settings(None, dsn)
    _ensure_sqlite_directory(settings.storage_dsn)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.storage_dsn)
    command.upgrade(config, "head")
    typer.echo("migrated")


@session_app.command("list")
def session_list(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID | None = typer.Option(None, "--user"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """List sessions for a tenant."""
    manager = _manager(backend, dsn)
    result = asyncio.run(
        manager.list_sessions(ListSessionsRequest(tenant_id=tenant_id, user_id=user_id))
    )
    for item in result.sessions:
        typer.echo(f"{item.session_id} {item.status.value} {item.title or ''}")


@memory_app.command("search")
def memory_search(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    query: str = typer.Option(..., "--query"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Run hybrid search and print hits."""
    manager = _manager(backend, dsn)
    result = asyncio.run(
        manager.search_memory(
            SearchMemoryRequest(tenant_id=tenant_id, user_id=user_id, query_text=query)
        )
    )
    for hit in result.hits:
        typer.echo(f"{hit.score:.3f} {hit.kind.value} {hit.title}")


@memory_app.command("show")
def memory_show(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    memory_id: UUID = typer.Option(..., "--memory"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Print one memory and its version metadata."""
    manager = _manager(backend, dsn)
    result = asyncio.run(
        manager.fetch_memory(
            FetchMemoryRequest(tenant_id=tenant_id, user_id=user_id, memory_id=memory_id)
        )
    )
    memory = result.memory
    typer.echo(f"{memory.memory_id} v{memory.version_number} {memory.status.value}")
    typer.echo(memory.content)


@review_app.command("list")
def review_list(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """List candidate memories for one user."""
    manager = _manager(backend, dsn)
    candidates = asyncio.run(manager.list_review_candidates(tenant_id, user_id))
    if not candidates:
        typer.echo("no candidates")
        return
    for memory in candidates:
        typer.echo(
            f"{memory.memory_id} v{memory.version_number} {memory.category.value} {memory.title}"
        )


@review_app.command("accept")
def review_accept(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    memory_id: UUID = typer.Option(..., "--memory"),
    yes: bool = typer.Option(False, "--yes", help="Write a new active version"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Promote a candidate by appending an active version."""
    manager = _manager(backend, dsn)
    current = asyncio.run(manager.load_review_memory(tenant_id, user_id, memory_id))
    if not yes:
        typer.echo(
            f"would accept {current.memory_id} v{current.version_number} "
            f"{current.status.value} -> active"
        )
        typer.echo("pass --yes to update")
        return
    updated = asyncio.run(manager.accept_review_candidate(tenant_id, user_id, memory_id))
    typer.echo(f"accepted {updated.memory_id} v{updated.version_number} {updated.status.value}")


@review_app.command("reject")
def review_reject(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    memory_id: UUID = typer.Option(..., "--memory"),
    yes: bool = typer.Option(False, "--yes", help="Soft-delete the candidate"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Soft-delete a candidate and keep its suppression hash."""
    manager = _manager(backend, dsn)
    current = asyncio.run(manager.load_review_memory(tenant_id, user_id, memory_id))
    if not yes:
        typer.echo(f"would reject {current.memory_id} and suppress its hash")
        typer.echo("pass --yes to delete")
        return
    deleted = asyncio.run(manager.reject_review_candidate(tenant_id, user_id, memory_id))
    typer.echo(f"rejected {deleted.memory_id} {deleted.status.value}")


@memory_app.command("export")
def memory_export(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    output_dir: Path = typer.Option(Path("memory-export"), "--output"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Write the Markdown memory tree."""
    manager = _manager(backend, dsn)
    result = asyncio.run(
        manager.export_markdown(
            ExportMarkdownRequest(tenant_id=tenant_id, user_id=user_id, output_dir=str(output_dir))
        )
    )
    typer.echo(result.output_dir)


@dream_app.command("run")
def dreamflow_run(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID | None = typer.Option(None, "--user"),
    use_model: bool = typer.Option(False, "--use-model"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Run dreamflow for a tenant and optional user."""
    manager = _manager(backend, dsn)
    result = asyncio.run(
        manager.run_dreamflow(
            RunDreamflowRequest(tenant_id=tenant_id, user_id=user_id, use_model=use_model)
        )
    )
    typer.echo(result.run.status.value)
    typer.echo(json.dumps(result.run.report, default=str))


@benchmark_app.command("run")
def benchmark_run(
    output: Path = typer.Option(Path("benchmark-report.json"), "--output"),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Execute the packaged strategies on the fixture catalog."""
    from memcave.adapters.models.fake import FakeModelProvider
    from memcave.evaluation.fixtures import load_catalog
    from memcave.evaluation.runner import run_benchmark
    from memcave.evaluation.strategies import strategies

    settings = _settings(backend, dsn)

    async def factory() -> MemoryManager:
        return await build_manager(settings)

    report = asyncio.run(
        run_benchmark(load_catalog(), strategies(), factory, FakeModelProvider(), seed=0)
    )
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text(str(report["markdown"]), encoding="utf-8")
    typer.echo(str(output))


@benchmark_app.command("report")
def benchmark_report(
    source: Path = typer.Option(..., "--input"),
    output: Path = typer.Option(Path("benchmark-report.md"), "--output"),
) -> None:
    """Render Markdown from a saved benchmark JSON file."""
    from memcave.evaluation.reports import render_markdown

    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.BadParameter("benchmark JSON must be an object")
    text = render_markdown(payload)
    output.write_text(text, encoding="utf-8")
    typer.echo(str(output))


@user_app.command("erase")
def user_erase(
    tenant_id: UUID = typer.Option(..., "--tenant"),
    user_id: UUID = typer.Option(..., "--user"),
    yes: bool = typer.Option(
        False, "--yes", help="Delete. Without this flag the command is a dry run."
    ),
    backend: str | None = BackendOpt,
    dsn: str | None = DsnOpt,
) -> None:
    """Erase one user in one tenant. Defaults to a dry run."""
    manager = _manager(backend, dsn)

    async def _dry() -> tuple[int, int]:
        events = await manager.list_events(
            ListEventsRequest(tenant_id=tenant_id, user_id=user_id, limit=200)
        )
        index = await manager.get_memory_index(
            GetMemoryIndexRequest(tenant_id=tenant_id, user_id=user_id)
        )
        return len(events.events), len(index.entries)

    event_count, memory_count = asyncio.run(_dry())
    typer.echo(f"dry-run events={event_count} memories={memory_count}")
    if not yes:
        typer.echo("pass --yes to delete")
        return
    result = asyncio.run(
        manager.erase_user_data(EraseUserDataRequest(tenant_id=tenant_id, user_id=user_id))
    )
    typer.echo(result.counts.model_dump_json())


def _ensure_sqlite_directory(dsn: str) -> None:
    """Create the parent directory for a file-backed SQLite DSN."""
    from sqlalchemy.engine.url import make_url

    url = make_url(dsn)
    if not url.drivername.startswith("sqlite"):
        return
    database = url.database
    if database is None or database == ":memory:":
        return
    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
