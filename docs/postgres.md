# PostgreSQL and pgvector

SQLite is the default. PostgreSQL is optional and needs the `postgres` extra. The Compose username and password `memcave` are for this local database only. Do not reuse them anywhere else.

```bash
docker compose up -d postgres
pip install -e ".[postgres]"
export MEMCAVE_STORAGE_BACKEND=postgres
export MEMCAVE_STORAGE_DSN=postgresql+asyncpg://memcave:memcave@localhost:5432/memcave
memcave migrate --dsn "$MEMCAVE_STORAGE_DSN"
```

`docker-compose.yml` uses `pgvector/pgvector:pg16`. The application creates the `vector` extension, a `vector(N)` column, and an HNSW cosine index when it prepares the database. `N` comes from `MEMCAVE_EMBEDDING_DIM` and defaults to 64. The value is stored in `engine_metadata`. Startup raises `ProviderError` if a later process uses a different dimension.

Tenant isolation in 0.1 is enforced by application queries. Row-level security is not enabled. Do not point this build at a database shared with untrusted roles unless you add that control yourself.

Contract tests:

```bash
MEMCAVE_POSTGRES_DSN=postgresql+asyncpg://memcave:memcave@localhost:5432/memcave \
  pytest -m postgres
```

The default `pytest` run skips that marker.
