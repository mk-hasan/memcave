"""Core packages must stay free of infrastructure imports."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "memcave"
PACKAGES = ("domain", "ports", "services", "dreamflow", "evaluation")
FORBIDDEN = {
    "langgraph",
    "streamlit",
    "fastapi",
    "asyncpg",
    "pgvector",
    "openai",
    "sqlalchemy",
    "memcave.adapters",
    "memcave.db",
}


def test_core_import_boundary() -> None:
    offenders: list[str] = []
    for package in PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if any(
                        name == forbidden or name.startswith(forbidden + ".")
                        for forbidden in FORBIDDEN
                    ):
                        offenders.append(f"{path.name}: {name}")
    assert offenders == []
