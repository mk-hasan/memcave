"""OpenAI-compatible chat. The API key is passed to the client and never stored."""

import time

from memcave.domain.errors import ProviderError
from memcave.domain.models import ModelRequest, ModelResponse
from memcave.dreamflow.schemas import ExtractionBatch

_INVALID_JSON = "model returned invalid JSON"


class OpenAICompatibleModelProvider:
    """Chat completions against any server that speaks the OpenAI API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        client: object | None = None,
    ) -> None:
        if not base_url.strip():
            raise ProviderError("MEMCAVE_BASE_URL is not set")
        if not api_key.strip():
            raise ProviderError("MEMCAVE_API_KEY is not set")
        if not model.strip():
            raise ProviderError("MEMCAVE_MODEL is not set")
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self._client = client if client is not None else _async_client(base_url, api_key, timeout)

    def __repr__(self) -> str:
        return f"OpenAICompatibleModelProvider(base_url={self.base_url!r}, model={self.model!r})"

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        response = await self._complete_once(request)
        if request.task != "extract" or _valid_extraction(response.content):
            return response
        response = await self._complete_once(request)
        if _valid_extraction(response.content):
            return response
        raise ProviderError(_INVALID_JSON)

    async def _complete_once(self, request: ModelRequest) -> ModelResponse:
        kwargs: dict[str, object] = {
            "model": self.model,
            "temperature": request.temperature,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if request.seed is not None:
            kwargs["seed"] = request.seed
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**kwargs)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ProviderError("the model provider request failed") from exc
        content = response.choices[0].message.content or ""
        usage = response.usage
        return ModelResponse(
            content=content,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=(time.perf_counter() - started) * 1000,
            model_name=self.model,
        )


def _valid_extraction(content: str) -> bool:
    try:
        ExtractionBatch.model_validate_json(content)
    except ValueError:
        return False
    return True


def _async_client(base_url: str, api_key: str, timeout: float) -> object:
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise ProviderError("install memcave[openai] to use the OpenAI provider") from exc
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
