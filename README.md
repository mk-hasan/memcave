# memcave

memcave is an open-source, framework-independent memory library for AI agents. It records what happened, turns closed sessions into evidence-backed memories, versions conflicts instead of overwriting them, and retrieves a bounded untrusted context block.

The package, the command-line tool, and this repository are all named memcave. The offline consolidation pipeline is dreamflow.

Version 0.1 is a library and a local command-line tool. It does not ship an HTTP API.

## Install

Install 0.1.0 from the git tag, without cloning the repository first:

```bash
pip install "memcave @ git+https://github.com/mk-hasan/memcave@v0.1.0"
```

SQLite is included. A real model needs the OpenAI extra:

```bash
pip install "memcave[openai] @ git+https://github.com/mk-hasan/memcave@v0.1.0"
```

Python 3.12 or newer is required. To work on a checkout:

```bash
pip install -e ".[dev]"
pip install -e ".[postgres,langgraph,openai,embeddings,dashboard]"
```

## Five-minute SQLite quickstart

```bash
python examples/sqlite_quickstart.py
```

That creates `.memcave/quickstart.db`, stores one explicit preference, searches it, and writes a Markdown export under `.memcave/export`. No Docker and no API key are required.

The same path from the command line:

```bash
memcave init
memcave migrate
```

`memcave init` writes `.memcave/config.toml` and prepares the local SQLite file. `memcave user erase` prints a dry run unless you pass `--yes`.

An in-memory script that never touches disk is `examples/quickstart_memory.py`.

## Use a real model

The default chat and embedding providers are fake. Tests and CI use them, and they do not call a network. One OpenAI-compatible adapter serves a local server and a public API. Keep `MEMCAVE_API_KEY` in the environment. Do not put it in `.memcave/config.toml`.

A small local model may fail JSON extraction. Dreamflow records that failure in the run report and does not write a memory from the invalid response. If you change `MEMCAVE_EMBEDDING_DIMENSION`, run `memcave embeddings rebuild --dimension N` and then the same command with `--yes` before searching. Without `--yes`, the command prints the plan and writes nothing.

Local server:

```bash
export MEMCAVE_MODEL_PROVIDER=openai_compatible
export MEMCAVE_EMBEDDING_PROVIDER=openai_compatible
export MEMCAVE_BASE_URL=http://localhost:11434/v1
export MEMCAVE_API_KEY=ollama
export MEMCAVE_MODEL=your-chat-model
export MEMCAVE_EMBEDDING_MODEL=your-embedding-model
export MEMCAVE_EMBEDDING_DIMENSION=768
```

Public API:

```bash
export MEMCAVE_MODEL_PROVIDER=openai_compatible
export MEMCAVE_EMBEDDING_PROVIDER=openai_compatible
export MEMCAVE_BASE_URL=https://api.openai.com/v1
export MEMCAVE_API_KEY="$OPENAI_API_KEY"
export MEMCAVE_MODEL=gpt-4o-mini
export MEMCAVE_EMBEDDING_MODEL=text-embedding-3-small
export MEMCAVE_EMBEDDING_DIMENSION=1536
```

Replace the local model names and dimension with the values your server reports. The rebuild order and the live test are in [docs/live-models.md](docs/live-models.md).

## What 0.1 includes

- Session and event capture, explicit memories, and versioned updates
- Soft delete with hash suppression, and hard delete through `erase_user_data`
- Hybrid retrieval over episodic events and derived memories
- Dreamflow: rule-based extraction, optional model extraction, duplicate and conflict handling
- `memory.md` and a Markdown export tree
- Optional LangGraph tools that do not import database sessions into the graph
- A 25-case benchmark with five strategies, JSON, and Markdown reports
- An optional Streamlit view of a benchmark JSON file

From WSL, bind Streamlit on all interfaces and open the Windows-side address. `AppTest` covers the metrics table; a browser that cannot reach `127.0.0.1` inside WSL is an environment limit.

```bash
streamlit run src/memcave/dashboard/app.py --server.address 0.0.0.0 --server.port 8765
```

Use the WSL IP from Windows, for example `http://<wsl-ip>:8765`.

## What 0.1 does not include

- CrewAI, AutoGen, or a hosted vector database
- A hosted model. Tests use a fake provider. A real server is optional through one OpenAI-compatible adapter.
- PostgreSQL row-level security
- A claim that memcave scores higher than the baselines

## Privacy and limitations

Memory text is untrusted data. Search wraps it in `<<<MEMCAVE_MEMORY>>>` delimiters and tells the caller not to follow instructions found inside. That fence is a control, not a proof that a model will comply. Secret patterns are redacted before storage, and novel credential formats can still get through. Application queries enforce tenant and user scope; a missed filter would leak data. Deletion of a user removes that user's rows. Tenant-shared memories stay until they are deleted on their own.

The default benchmark reader is a deterministic script. It shows whether a strategy put a fact in the prompt. It does not measure open-ended reasoning. Reports say so, and they do not rank a winner from an LLM judge.

## Embeddings

The fake embedding provider is the default. It is for tests and offline development. It does not retrieve by meaning; it folds tokens into a fixed vector. The default test run uses it and does not download a model or call the network.

A real local or public model uses one OpenAI-compatible adapter. See [docs/live-models.md](docs/live-models.md) for the environment variables, the local and public commands, and the rebuild warning. The API key stays in the environment. Startup refuses to run when the provider dimension and `engine_metadata` disagree, and a dimension above 2000 is rejected because a standard pgvector HNSW index cannot hold it.

See [docs/threat-model.md](docs/threat-model.md), [docs/lifecycle.md](docs/lifecycle.md), and [docs/evaluation.md](docs/evaluation.md).

## Documentation

| Topic | Document |
| --- | --- |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Memory model | [docs/memory-model.md](docs/memory-model.md) |
| Lifecycle and dreamflow | [docs/lifecycle.md](docs/lifecycle.md), [docs/dreamflow.md](docs/dreamflow.md) |
| PostgreSQL | [docs/postgres.md](docs/postgres.md) |
| Embeddings | this README, section Embeddings |
| Live models | [docs/live-models.md](docs/live-models.md) |
| LangGraph | [docs/langgraph.md](docs/langgraph.md) |
| Evaluation | [docs/evaluation.md](docs/evaluation.md) |
| API stubs | [docs/api.md](docs/api.md) |
| Threat model | [docs/threat-model.md](docs/threat-model.md) |

## License

Apache-2.0. The license appendix still uses the Apache placeholder because the copyright holder has not confirmed the notice.
