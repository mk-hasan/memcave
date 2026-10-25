"""OpenAI-compatible embeddings. The API key is passed to the client and never stored."""

from collections.abc import Sequence

from memcave.adapters.embeddings.factory import MAX_EMBEDDING_DIMENSION
from memcave.domain.errors import ProviderError


class OpenAICompatibleEmbeddingProvider:
    """Embeddings against any server that speaks the OpenAI embeddings API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        timeout: float = 60.0,
        client: object | None = None,
    ) -> None:
        if dimension > MAX_EMBEDDING_DIMENSION:
            raise ProviderError(
                f"embedding dimension {dimension} is above {MAX_EMBEDDING_DIMENSION}, "
                "which a standard pgvector HNSW index cannot hold"
            )
        if dimension < 1:
            raise ProviderError("embedding dimension must be positive")
        if not base_url.strip():
            raise ProviderError("MEMCAVE_BASE_URL is not set")
        if not api_key.strip():
            raise ProviderError("MEMCAVE_API_KEY is not set")
        if not model.strip():
            raise ProviderError("MEMCAVE_EMBEDDING_MODEL is not set")
        self.base_url = base_url
        self.model = model
        self.dimension = dimension
        self.timeout = timeout
        self._client = client if client is not None else _async_client(base_url, api_key, timeout)

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleEmbeddingProvider("
            f"base_url={self.base_url!r}, model={self.model!r}, dimension={self.dimension})"
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload = list(texts)
        if not payload:
            return []
        try:
            response = await self._client.embeddings.create(  # type: ignore[attr-defined]
                model=self.model, input=payload
            )
        except Exception as exc:
            raise ProviderError("the embedding provider request failed") from exc
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if len(vectors) != len(payload) or any(len(vector) != self.dimension for vector in vectors):
            raise ProviderError(
                f"embedding length does not match the declared dimension {self.dimension}"
            )
        return vectors


def _async_client(base_url: str, api_key: str, timeout: float) -> object:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise ProviderError("install memcave[openai] to use the OpenAI provider") from exc
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
