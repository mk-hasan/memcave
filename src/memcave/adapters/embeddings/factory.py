"""Select an embedding provider without importing optional SDKs until needed."""

from memcave.adapters.embeddings.fake import FakeEmbeddingProvider
from memcave.domain.errors import ProviderError
from memcave.ports.protocols import EmbeddingProvider

MAX_EMBEDDING_DIMENSION = 2000
LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMENSION = 384
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSION = 1536


def build_embedding_provider(
    name: str,
    dimension: int,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: float = 60.0,
) -> EmbeddingProvider:
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
    elif name == "openai_compatible":
        from memcave.adapters.embeddings.openai import OpenAICompatibleEmbeddingProvider

        provider = OpenAICompatibleEmbeddingProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimension=dimension,
            timeout=timeout,
        )
    else:
        raise ProviderError(f"unknown embedding provider {name}")
    if provider.dimension != dimension:
        raise ProviderError(
            f"provider {name} dimension {provider.dimension} does not match settings {dimension}"
        )
    return provider
