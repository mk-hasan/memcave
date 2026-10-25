"""Optional OpenAI embeddings. The SDK and API key are never written to logs or the database."""

import os
from collections.abc import Sequence

from memcave.adapters.embeddings.factory import OPENAI_EMBEDDING_DIMENSION, OPENAI_EMBEDDING_MODEL
from memcave.domain.errors import ProviderError


class OpenAIEmbeddingProvider:
    """text-embedding-3-small. Dimension is fixed at 1536."""

    dimension = OPENAI_EMBEDDING_DIMENSION

    def __init__(self, model: str = OPENAI_EMBEDDING_MODEL) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError(
                "install memcave[openai] to use the OpenAI embedding provider"
            ) from exc
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key.strip():
            raise ProviderError("OPENAI_API_KEY is not set")
        del key
        self.model = model
        self._client = AsyncOpenAI()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model=self.model, input=list(texts))
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]
