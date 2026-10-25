"""CLI smoke tests."""

from pathlib import Path

from typer.testing import CliRunner

from memcave.cli.app import app

runner = CliRunner()


def test_init_and_help(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["init", "--backend", "sqlite"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".memcave" / "config.toml").exists()
    listed = runner.invoke(
        app,
        [
            "session",
            "list",
            "--tenant",
            "00000000-0000-4000-8000-000000000001",
            "--backend",
            "memory",
        ],
    )
    assert listed.exit_code == 0, listed.output


def test_migrate_and_benchmark(tmp_path: Path) -> None:
    database = tmp_path / "migrated.db"
    migrated = runner.invoke(
        app,
        ["migrate", "--dsn", f"sqlite+aiosqlite:///{database}"],
    )
    assert migrated.exit_code == 0, migrated.output
    assert database.exists()
    report = tmp_path / "report.json"
    benchmark = runner.invoke(
        app,
        ["benchmark", "run", "--backend", "memory", "--output", str(report)],
    )
    assert benchmark.exit_code == 0, benchmark.output
    rendered = runner.invoke(
        app,
        ["benchmark", "report", "--input", str(report), "--output", str(tmp_path / "report.md")],
    )
    assert rendered.exit_code == 0, rendered.output
    assert "Strategy metrics" in (tmp_path / "report.md").read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "user",
            "erase",
            "--tenant",
            "00000000-0000-4000-8000-000000000001",
            "--user",
            "00000000-0000-4000-8000-0000000000a1",
            "--backend",
            "memory",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert "--yes" in result.output
