# Implementation checklist

Phase 0 and Phase 1 are the current baseline. Production code starts only after this checklist and the design documents are approved.

Design documents:

- [architecture.md](architecture.md)
- [memory-model.md](memory-model.md)
- [evaluation.md](evaluation.md)
- [threat-model.md](threat-model.md)
- [adr/0001-architecture.md](adr/0001-architecture.md)

## Phase 0 findings

Inspected `/home/hasan/Documents/projects/personal/memcave` on 2026-10-24.

- Branch `main` tracks `origin/main` at `aca0825` (`Initial commit`).
- Remote: `https://github.com/mk-hasan/memcave.git`.
- Tracked files: Apache-2.0 `LICENSE`, and `README.md` containing `# memcave`.
- The license appendix still contains the placeholder `Copyright [yyyy] [name of copyright owner]`.
- No source, tests, packaging, or CI exist, so there are no code conventions to preserve.
- Working tree was clean before the design documents were added.

Constraints carried into the design:

- The install name, import package, CLI, and repository name are `memcave`. The offline consolidation pipeline is `dreamflow`.
- The license stays Apache-2.0.
- The core must not import LangGraph, Streamlit, PostgreSQL drivers, pgvector, or OpenAI.
- Version 0.1 stops at the acceptance list in the project brief. FastAPI, CrewAI, AutoGen, hosted vector databases, and extra commercial model providers are extension points.

## Assumptions to confirm with approval

Approval of this checklist accepts the following choices. They are the smallest options that meet the 0.1 acceptance criteria.

1. The package name is `memcave`, matching the repository. The consolidation pipeline is `dreamflow`.
2. There is no HTTP API in 0.1.
3. `delete_memory` is a soft delete with hash suppression. `erase_user_data` is the hard delete.
4. Tenant isolation is enforced in application queries. PostgreSQL row-level security waits.
5. The built-in embedding dimension is 64. A mismatch with stored metadata fails at startup.
6. Token estimates use `ceil(character_count / 4)`.
7. Unambiguous explicit instructions write immediately. Other messages wait for dreamflow.
8. CI benchmarks use the rule-based extractor and the scripted reader. They do not rank strategies by an LLM judge.
9. The copyright line is filled in during release preparation after the owner confirms the exact holder text. The initial commit author is M.k Hasan.

## Phase 2 — Core vertical slice

Goal: an in-memory `MemoryManager` that records episodes, writes explicit memories, searches them, and renders an index, with deterministic tests and a tiny CLI.

- [x] Add `pyproject.toml` with package `memcave`, Python `>=3.12`, base dependencies, and the extra groups. Version `0.1.0.dev0`.
- [x] Add domain enums, errors, entities, and request/response models from the memory model.
- [x] Add ports: stores, providers, clock, event sink, policies, reranker, and tracer.
- [x] Add in-memory episodic, derived, vector, and audit adapters.
- [x] Add the deterministic fake embedding and fake model providers.
- [x] Add `MemoryManager` operations through `export_markdown` and `erase_user_data`.
- [x] Add the untrusted-context renderer and secret redaction policy.
- [x] Add explicit-instruction parsing for the four unambiguous patterns.
- [x] Add retrieval scoring with score components, hard filters, and token budget.
- [x] Add an import-boundary test for core modules.
- [x] Add unit tests for version conflicts, soft delete, suppression, tenant filters, redaction, and fence escaping.
- [x] Add a Typer CLI that can target `--backend memory` for `session list`, `memory search`, `memory show`, and `memory export`.
- [x] Add `examples/quickstart_memory.py`, runnable without network access.

Exit check:

- [x] `ruff format --check` and `ruff check` pass.
- [x] `mypy` strict passes on the core packages that exist.
- [x] `pytest` passes without network access.
- [x] Report the commands and their results. Do not claim a command that was not run.

## Phase 3 — Persistence

Goal: SQLite by default, PostgreSQL and pgvector behind the extra, with one contract suite.

- [x] Add SQLAlchemy 2 tables and packaged Alembic migrations, including partial unique hashes and the indexes in the memory model.
- [x] Add the SQLite adapter, FTS5 lexical search, and JSON cosine search.
- [x] Add the PostgreSQL adapter and pgvector column behind lazy imports.
- [x] Record and check `embedding_dimension` in `engine_metadata`.
- [x] Translate driver errors into domain errors.
- [x] Parameterize contract tests across in-memory, SQLite, and PostgreSQL.
- [x] Mark PostgreSQL tests with `postgres` and skip them by default.
- [x] Add `docker-compose.yml` using `pgvector/pgvector:pg16`.
- [x] Add `examples/sqlite_quickstart.py` and the PostgreSQL setup notes needed to run it.
- [x] Implement `memcave init` and `memcave migrate`.

Exit check:

- [x] Default pytest run passes on in-memory and SQLite and does not require Docker.
- [x] PostgreSQL contract tests are run when Docker is available, and the exact result is reported.
- [x] If Docker is unavailable, say so and leave the PostgreSQL box open.

## Phase 4 — Dreamflow

Goal: idempotent, evidence-backed consolidation and a generated index.

- [x] Implement the ten stages as separate, testable types.
- [x] Implement rule-based extraction for explicit instructions and structured outcomes.
- [x] Implement model-assisted extraction and conflict advice through `ModelProvider`, validated by Pydantic.
- [x] On rule/model disagreement or low confidence, persist `needs_review`.
- [x] Merge exact and high-cosine duplicates without a new version.
- [x] Version contradictions and temporal changes according to the invariants.
- [x] Suppress hashes of forgotten memories during dreamflow, while allowing a later explicit remember.
- [x] Render `memory.md` from active rows during index regeneration and export.
- [x] Persist run and item state so a retry resumes and a second success creates no duplicate memories.
- [x] Add `memcave dreamflow run`.

Exit check:

- [x] Idempotency, conflict, versioning, and path-traversal tests pass.
- [x] A fixture run report shows rejected and review candidates rather than silent overwrites.

## Phase 5 — LangGraph

Goal: optional tools and hooks on top of `MemoryManager`.

- [x] Add search, fetch, add, update, and delete tools.
- [x] Add session start, event capture, and session end hooks.
- [x] Inject the compact index on session start unless the user or session opted out.
- [x] Add an example agent that runs on the fake model.
- [x] Test the integration with the `langgraph` extra installed.
- [x] Skip those tests with a clear reason when the extra is absent.

Exit check:

- [x] The example does not import adapter sessions into the graph.
- [x] An adversarial memory payload does not become a registered tool call.

## Phase 6 — Evaluation

Goal: the five strategies, the 25 fixtures, metrics, and both report formats.

- [x] Add the `MemoryStrategy` protocol and the five strategies.
- [x] Add the case schema and the fixture catalog from the evaluation design.
- [x] Add deterministic metrics, separated build and answer costs, and the pure-Python bootstrap.
- [x] Add the runner, JSON report, and Markdown report.
- [x] Add `memcave benchmark run` and `memcave benchmark report`.
- [x] Assert the CI conditions in the evaluation design, including zero leakage and zero memcave adversarial compliance.
- [x] Leave “hybrid has the best score” unasserted.

Exit check:

- [x] The default suite runs the catalog with the fake provider and no network.
- [x] A sample JSON and Markdown report are produced by the test run.

## Phase 7 — Dashboard and release

Goal: installable package, optional dashboard, CI, and honest docs.

- [x] Add the Streamlit dashboard over benchmark JSON, behind the `dashboard` extra.
- [x] Replace the placeholder README with the product summary, SQLite quickstart, limitations, and privacy notes.
- [x] Add PostgreSQL setup, LangGraph example, lifecycle, dreamflow, evaluation, threat-model summary, and API stubs.
- [x] Add `CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, `CHANGELOG`, issue templates, and a pull-request template.
- [x] Add pre-commit and GitHub Actions for Ruff, mypy, pytest, SQLite migration, package build, PostgreSQL, and LangGraph.
- [ ] Confirm the Apache-2.0 copyright line with the owner and update the license appendix. The appendix is still `Copyright [yyyy] [name of copyright owner]` on purpose.
- [ ] Set version `0.1.0` only when the acceptance list below is true.

## Phase 8 — Release lock

Goal: the local 0.1.0.dev0 claim is reproducible, and the five write invariants fail when broken.

- [x] Only `remember that …`, `forget …`, `use X instead of Y`, and `as of <date> …` write on append. Other messages wait for dreamflow.
- [x] Soft delete appends a deleted version and a suppression hash. The previous version row is unchanged.
- [x] The hash is tenant, user, category, and casefolded content. An explicit remember clears it. `erase_user_data` removes events, versions, evidence, embeddings, and hashes.
- [x] Cosine merge requires the same tenant, user, category, and subject key. Similarity alone does not merge.
- [x] A dreamflow retry resumes, and a second success creates no memories.
- [x] The PostgreSQL job runs `pytest -m postgres` against the shared contract suite. The default suite skips that marker.
- [x] The benchmark report header says the reader is scripted. Tests do not claim that hybrid has the best score.
- [x] The Compose password is documented as local-only. The WSL Streamlit bind address is documented. The closed IDE browser is not a product change.
- [ ] Version stays `0.1.0.dev0` until `.github/workflows/ci.yml` passes on the published commit. This lock does not push.

Exit check:

- [x] `pip install -e ".[dev]"` works in a clean virtual environment.
- [x] The SQLite quickstart runs.
- [ ] CI command results are recorded from an actual run of the current `main` commits. The earlier run was on a rewritten commit and is not this lock.

## Acceptance map

| 0.1 acceptance item | Phase |
| --- | --- |
| Local install | 2 packaging, 7 install check |
| SQLite quickstart without extra infrastructure | 3 and 7 |
| Record sessions and events | 2 and 3 |
| Add and version explicit memories | 2 and 4 |
| Dreamflow turns sessions into evidence-backed candidates | 4 |
| Duplicates and conflicts do not silently overwrite | 4 |
| Hybrid retrieval over both memory kinds | 2 behavior, 3 persistence |
| Generate `memory.md` | 4 |
| LangGraph tools | 5 |
| Benchmark at least four strategies | 6 implements five |
| JSON and Markdown reports | 6 |
| Tenant, deletion, and prompt-injection tests | 2, 3, 4, and 6 |
| Core tests without paid services | every phase |
| CI | 7 |
| Limitations and privacy risks documented | 1 design, 7 user docs |

## Recorded results

Local commands on Python 3.14.4, after `uv venv` and `uv pip install -e ".[dev]"` (the system interpreter has no `ensurepip`):

- `ruff format --check src tests` and `ruff check src tests`: passed.
- `mypy`: passed, 27 source files.
- `pytest --cov`: 33 passed, 4 deselected (`postgres`), branch coverage 88.84% (gate 85%). The run includes the 25 benchmark cases. The report header says the reader is scripted and this is not a production-model result. The tests do not assert that hybrid has the best score.
- `python examples/quickstart_memory.py`: printed the concise-answer memory and `fenced True`.
- `python examples/sqlite_quickstart.py`: printed the February fiscal-year memory and wrote `.memcave/export`. That directory is gitignored.
- `python -m build`: built `memcave-0.1.0.dev0.tar.gz` and the wheel.
- `MEMCAVE_POSTGRES_DSN=postgresql+asyncpg://memcave:memcave@localhost:5432/memcave pytest -m postgres`: 4 passed, 33 deselected, against the running Compose database. Those four are the shared contract tests. The Compose password is local-only.
- `pytest tests/integration`: 3 passed with the langgraph extra installed.
- Streamlit `AppTest` uploaded a benchmark JSON and rendered the metrics table. A closed browser at `127.0.0.1:8765` is a WSL reachability issue, not a product failure. Bind with `streamlit run src/memcave/dashboard/app.py --server.address 0.0.0.0 --server.port 8765` and open the WSL IP from Windows.
- `.github/workflows/ci.yml` was not run for this lock. Nothing was pushed. The package version stays `0.1.0.dev0`.

## Remaining risks

- Application-level isolation still depends on every new query including the tenant predicate. PostgreSQL row-level security is not enabled.
- The scripted benchmark reader measures whether a fact reached the prompt. It is not a claim about a production model.
- Secret regexes miss novel credential formats.
- The 64-dimension embedder will not match a later model until a migration and re-embed path exist.
- The license appendix keeps the Apache placeholder `Copyright [yyyy] [name of copyright owner]` until the holder confirms the notice.
