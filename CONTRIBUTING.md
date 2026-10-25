# Contributing

Install the development extra and run the same checks CI runs:

```bash
pip install -e ".[dev]"
ruff format --check src tests
ruff check src tests
mypy
pytest
```

`pytest` skips PostgreSQL tests. With Docker, the username and password below are local-only:

```bash
docker compose up -d postgres
MEMCAVE_POSTGRES_DSN=postgresql+asyncpg://memcave:memcave@localhost:5432/memcave \
  pytest -m postgres
```

Pre-commit runs Ruff and mypy: `pre-commit install`.

Core packages (`domain`, `ports`, `services`, `dreamflow`, `evaluation`) must not import LangGraph, Streamlit, SQLAlchemy, asyncpg, pgvector, or OpenAI. Add adapters behind `memcave.bootstrap`.

Do not commit secrets or real personal data. Benchmark fixtures stay fictional. The scripted reader is not evidence of model quality; do not add a test that declares a winning strategy.
