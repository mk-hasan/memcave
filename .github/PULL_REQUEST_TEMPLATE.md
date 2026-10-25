## Summary

-

## Test plan

- [ ] `ruff format --check` and `ruff check`
- [ ] `mypy`
- [ ] `pytest` (SQLite and in-memory, no Docker)
- [ ] PostgreSQL tests, if the change touches SQL
- [ ] No new core import of LangGraph, Streamlit, SQLAlchemy, or OpenAI
