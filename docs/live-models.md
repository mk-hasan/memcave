# Live models

The fake chat and embedding providers are the default. Tests and CI use them, and they do not call a network or need an API key. A real model is optional. One OpenAI-compatible adapter serves both a local server and a public API. Install it with:

```bash
pip install -e ".[openai]"
```

Set the provider names and the server in the environment. Do not put `MEMCAVE_API_KEY` in `.memcave/config.toml`. The process reads the key and does not write it to logs or the database.

## Local

The chat model and the embedding model are the ones you already run. A small local model may fail JSON extraction. Dreamflow records that failure in the run report and does not write a memory from the invalid response.

```bash
export MEMCAVE_MODEL_PROVIDER=openai_compatible
export MEMCAVE_EMBEDDING_PROVIDER=openai_compatible
export MEMCAVE_BASE_URL=http://localhost:11434/v1
export MEMCAVE_API_KEY=ollama
export MEMCAVE_MODEL=your-chat-model
export MEMCAVE_EMBEDDING_MODEL=your-embedding-model
export MEMCAVE_EMBEDDING_DIMENSION=768
```

Replace the model names and the dimension with the values your server reports.

## Public

```bash
export MEMCAVE_MODEL_PROVIDER=openai_compatible
export MEMCAVE_EMBEDDING_PROVIDER=openai_compatible
export MEMCAVE_BASE_URL=https://api.openai.com/v1
export MEMCAVE_API_KEY="$OPENAI_API_KEY"
export MEMCAVE_MODEL=gpt-4o-mini
export MEMCAVE_EMBEDDING_MODEL=text-embedding-3-small
export MEMCAVE_EMBEDDING_DIMENSION=1536
```

## Rebuild

Startup raises `ProviderError` when the provider dimension and `engine_metadata` differ, and it writes no vectors. Changing dimension, including the first switch off the fake 64-dimension store, requires a rebuild before search.

```bash
memcave embeddings rebuild --dimension 1536
memcave embeddings rebuild --dimension 1536 --yes
```

Without `--yes`, the command prints the stored dimension, the target dimension, and the number of active vectors, then writes nothing. With `--yes`, it re-embeds active searchable rows and only then updates `engine_metadata`. A dimension above 2000 is refused. A failed rebuild does not leave mixed dimensions searchable.

## Live test

The default `pytest` run does not select this test and does not contact a network. To call the server you configured above:

```bash
MEMCAVE_LIVE=1 pytest -m live
```

The test stores “remember that I prefer concise answers”, runs dreamflow, searches once, and checks that the fenced hit is returned. It does not grade the model’s prose.
