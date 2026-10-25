"""Local FastEmbed provider. The library is imported only when this provider is built."""

import asyncio
from collections.abc import Sequence

from memcave.adapters.embeddings.factory import LOCAL_DIMENSION, LOCAL_MODEL
from memcave.domain.errors import ProviderError


class FastEmbedProvider:
    """BAAI/bge-small-en-v1.5. Dimension is fixed at 384."""

    dimension = LOCAL_DIMENSION

    def __init__(self) -> None:
        try:
            import fastembed  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "install memcave[embeddings] to use the local embedding provider"
            ) from exc
        self._model: object | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload = list(texts)
        return await asyncio.to_thread(self._embed_sync, payload)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        from fastembed import TextEmbedding

        if self._model is None:
            self._model = TextEmbedding(model_name=LOCAL_MODEL)
        model = self._model
        embedded = model.embed(texts)  # type: ignore[attr-defined]
        return [vector.tolist() for vector in embedded]
