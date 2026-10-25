# Changelog

## 0.1.0.dev0

First implementation of the 0.1 library:

- in-memory and SQLite stores, with an optional PostgreSQL and pgvector adapter
- explicit memories, dreamflow, hybrid retrieval, and Markdown export
- LangGraph tools, a five-strategy benchmark, and an optional Streamlit report view
- tests for the five release invariants: immediate instruction patterns, soft-delete versions, suppression hashes, cosine subject keys, and dreamflow retries
- a scripted-reader limitation in the benchmark report header
- local-only documentation for the Compose password, and the WSL Streamlit bind address
- an agent turn loop that logs the session, injects fenced recall, and runs dreamflow after the session ends
- `memcave memory review` commands that accept a candidate with a new version or reject it with hash suppression

The license appendix is still the Apache placeholder `Copyright [yyyy] [name of copyright owner]`. This version stays `0.1.0.dev0` until GitHub Actions passes. That workflow was not run for this lock.
