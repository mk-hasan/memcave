"""Select an embedding provider without importing optional SDKs until needed."""

from memcave.adapters.embeddings.fake import FakeEmbeddingProvider
from memcave.domain.errors import ProviderError
from memcave.ports.protocols import EmbeddingProvider

MAX_EMBEDDING_DIMENSION = 2000
LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMENSION = 384
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSION = 1536


def build_embedding_provider(name: str, dimension: int) -> EmbeddingProvider:
    """Return the configured provider. The declared dimension must match settings."""
    if dimension > MAX_EMBEDDING_DIMENSION:
        raise ProviderError(
            f"embedding dimension {dimension} is above {MAX_EMBEDDING_DIMENSION}, "
            "which a standard pgvector HNSW index cannot hold"
        )
    if dimension < 1:
        raise ProviderError("embedding dimension must be positive")
    if name == "fake":
        provider: EmbeddingProvider = FakeEmbeddingProvider(dimension)
    elif name == "local":
        if dimension != LOCAL_DIMENSION:
            raise ProviderError(
                f"provider local declares dimension {LOCAL_DIMENSION}, not {dimension}"
            )
        from memcave.adapters.embeddings.local import FastEmbedProvider

        provider = FastEmbedProvider()
    elif name == "openai":
        if dimension != OPENAI_EMBEDDING_DIMENSION:
            raise ProviderError(
                f"provider openai declares dimension {OPENAI_EMBEDDING_DIMENSION}, not {dimension}"
            )
        from memcave.adapters.embeddings.openai import OpenAIEmbeddingProvider

        provider = OpenAIEmbeddingProvider()
    else:
        raise ProviderError(f"unknown embedding provider {name}")
    if provider.dimension != dimension:
        raise ProviderError(
            f"provider {name} dimension {provider.dimension} does not match settings {dimension}"
        )
    return provider
