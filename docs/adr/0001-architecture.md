# ADR 0001: memcave 0.1 architecture

- Status: Proposed
- Date: 2026-10-24
- Deciders: memcave maintainers
- Related: [architecture](../architecture.md), [memory model](../memory-model.md), [evaluation](../evaluation.md), [threat model](../threat-model.md)

## Context

memcave is a new framework-independent library for the agent memory lifecycle: capture, extract, validate, consolidate, version, retrieve, evaluate, and delete. The repository `mk-hasan/memcave` currently contains an Apache-2.0 `LICENSE` and a one-line README. There is no application code, packaging, or schema to preserve.

Version 0.1 has to be installable as `memcave`, usable on SQLite with no extra infrastructure, and able to run on PostgreSQL with pgvector. The core must stay free of framework and vendor imports. Tests that gate development must run without network access or paid APIs.

Several implementation choices are ambiguous. This ADR records the smallest choice that still meets the 0.1 acceptance criteria.

## Decision

1. **One distribution, ports and adapters.** Publish a single installable project named `memcave` using a `src/memcave` layout. Domain services depend on typed ports. Adapters implement those ports. A composition root, `memcave.bootstrap`, is the only core-adjacent module allowed to wire adapters.

2. **Use one project name.** The Git remote, distribution, import package, CLI, and prose name are `memcave`. The offline consolidation pipeline is named `dreamflow`.

3. **Ship SQLite and in-memory adapters in the base install.** Core dependencies are Pydantic v2, SQLAlchemy 2, Alembic, aiosqlite, Typer, and pydantic-settings. PostgreSQL (`asyncpg`, `pgvector`), LangGraph, OpenAI, and Streamlit are optional extras: `postgres`, `langgraph`, `openai`, `dashboard`, `dev`, and `all`.

4. **Do not add a FastAPI service in 0.1.** No acceptance criterion requires HTTP. A later process can wrap `MemoryManager` without changing the domain.

5. **Split identity from versions.** A `MemoryRecord` is the stable identity. A `MemoryVersion` is immutable content. The public read model returns the identity plus the selected version so callers see one memory object. Updates insert a version, relink evidence, mark the previous version superseded when the change replaces it outright, and append an audit event.

6. **Keep episodic content append-only.** Adapters may update only processing columns. Payload changes are not part of the public API. Right-to-erasure is the exception: `erase_user_data` hard-deletes stored personal content. Ordinary `delete_memory` is a soft delete that retains a normalized content hash so dreamflow does not recreate a forgotten memory. A later explicit remember may create it again. Dreamflow may not.

7. **Apply unambiguous explicit instructions immediately.** `add_memory` and an `explicit_memory_instruction` event write derived memory in the same transaction as the triggering write. Ordinary messages wait for dreamflow. Ambiguous explicit instructions are stored immediately as episodic events and as derived records with status `candidate`. Default retrieval skips `candidate`.

8. **Use application-level tenant isolation.** Every store method takes a tenant scope, and every query includes that tenant predicate. Database row-level security is a later hardening step, not a 0.1 requirement.

9. **Use portable embeddings plus an optional pgvector column.** Store a JSON float array on every backend. The fake embedding provider uses 64 dimensions. On PostgreSQL, migration adds a `vector(N)` column where `N` comes from `MEMCAVE_EMBEDDING_DIM` (default 64) and records `N` in internal `engine_metadata`. Startup refuses to run when the provider dimension differs.

10. **Use built-in lexical search.** In-memory adapters use token overlap. SQLite uses FTS5. PostgreSQL uses `tsvector`. No separate search engine is added.

11. **Rank with an interpretable weighted score.** Hard filters remove unauthorized, deleted, expired, and wrong-tenant rows. Remaining episodic and derived candidates are scored separately, min-max normalized within each kind, merged, and optionally reranked. Each hit keeps machine-readable score components. The default reranker is identity.

12. **Render retrieved memory as untrusted data.** `search_memory` returns hits and a delimited context block. Production integrations must pass that block through, not raw memory text. The block tells the agent that memory is data. Preferences and constraints are usable as user preferences or policies only when their source is an explicit user or administrator instruction, and they never override system or developer instructions.

13. **Make dreamflow idempotent per session and content hash.** A run persists stage state. Replaying a run resumes unfinished items. A partial unique index backs active and candidate content hashes, and the writer checks that index inside the write transaction. Conflicts produce a new version or a `needs_review` candidate. They do not overwrite a version in place.

14. **Treat automated conflict handling as advisory when rules and the model disagree.** Rule-based handling is authoritative for the structural cases defined in the memory model. A model may suggest a resolution. Disagreement, invalid structured output, or low confidence leaves the candidate in `needs_review`.

15. **Evaluate strategies with one harness and a deterministic reader.** The same fixture, prompt template, tool catalog, temperature, and `ModelProvider` are used for `no_memory`, `full_context`, `vector_episodic`, `derived_only`, and `memcave_hybrid`. The default provider is scripted and context-bound. CI asserts metric correctness, isolation, erasure behavior, and adversarial compliance of memcave strategies. CI does not treat an LLM judge or a single quality score as proof that one strategy is better.

16. **Instrument through local protocols.** `Clock`, `EventSink`, and a small `Tracer` port are injected. The default tracer is a no-op. An OpenTelemetry SDK adapter can be added later without putting the SDK in core dependencies.

17. **Leave unimplemented products as extension points.** CrewAI, AutoGen, hosted vector databases, and additional commercial model providers are documented extension points. They are not implemented and are not described as compatible.

## Alternatives considered

| Alternative | Why it was not selected for 0.1 |
| --- | --- |
| Separate distributions (`memcave-core`, `memcave-postgres`, `memcave-langgraph`) | More packaging than the acceptance criteria need. Extras provide the same dependency boundary. |
| FastAPI in the base install | No 0.1 caller requires it, and it would widen the dependency and threat surface. |
| pgvector as the only embedding store | SQLite quickstart would no longer be zero-setup. |
| A third-party search or tokenizer dependency | FTS5, `tsvector`, and a character-based token estimate are enough for the first benchmarks. |
| Database row-level security as the primary isolation mechanism | It does not exist on SQLite. Application scopes can be contract-tested on every adapter. |
| Materialized memory-index table | Rendering the index from active rows avoids a second source of truth. |
| LLM-judge pass/fail in CI | The judge is optional, biased, and unnecessary for the deterministic fixture set. |
| UUID v7 identifiers | The standard library does not provide UUID v7 on every version from 3.12 upward. UUID4 plus `timestamptz` is enough. |

## Consequences

- Core modules stay importable on a machine that has only the base extra.
- PostgreSQL and LangGraph tests are real, but they are marked and run when those extras and services are present.
- Soft delete and erasure are different operations, and documentation must keep that difference visible.
- The fake benchmark agent is intentionally simple. It measures whether the right facts and workflows were supplied. It does not measure open-ended reasoning quality.
- Changing the embedding dimension after data exists requires a new migration and a re-embed, which 0.1 does not automate beyond a documented failure at startup.
- Application-level isolation is only as strong as the store methods. The contract suite and the scope type exist to make an unscoped query hard to represent.

## Confirmation needed

This ADR is proposed. Implementation of production code waits for review. The assumptions that most affect later phases are listed in the [implementation checklist](../implementation-checklist.md).
