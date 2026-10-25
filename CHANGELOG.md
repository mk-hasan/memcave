# Changelog

## 0.1.0.dev0

First implementation of the 0.1 library:

- in-memory and SQLite stores, with an optional PostgreSQL and pgvector adapter
- explicit memories, dreamflow, hybrid retrieval, and Markdown export
- LangGraph tools, a five-strategy benchmark, and an optional Streamlit report view
- tests for the five release invariants: immediate instruction patterns, soft-delete versions, suppression hashes, cosine subject keys, and dreamflow retries
- a scripted-reader limitation in the benchmark report header
- local-only documentation for the Compose password, and the WSL Streamlit bind address
- an agent turn loop that logs the session, searches once per user question, and runs dreamflow only when close is asked to dream
- retrieval that hides expired and not-yet-valid memories, then boosts an explicit correction or a newer derived memory with the same subject
- stable subject keys that gate cosine merge and conflict detection; an empty key does not cosine-merge
- workflow steps appended to the fenced context only when that workflow's subject is in the question
- an evidence event id on each index line, without the episode body
- `memcave memory review` commands that accept a candidate with a new version or reject it with hash suppression
- an embedding provider factory and `memcave embeddings rebuild`, which replaces stored vectors before updating `engine_metadata`
- one OpenAI-compatible adapter for local and public chat and embeddings, with the fake providers still the default

The license appendix is still the Apache placeholder `Copyright [yyyy] [name of copyright owner]`. This version stays `0.1.0.dev0` until GitHub Actions passes. That workflow was not run for this lock.
