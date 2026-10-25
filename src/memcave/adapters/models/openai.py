"""Optional OpenAI model provider."""

import math

from memcave.domain.errors import ProviderError
from memcave.domain.models import ModelRequest, ModelResponse


class OpenAIModelProvider:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError("install memcave[openai] to use the OpenAI provider") from exc
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model=self.model,
            temperature=request.temperature,
            seed=request.seed,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        usage = response.usage
        return ModelResponse(
            content=content,
            input_tokens=usage.prompt_tokens if usage else math.ceil(len(request.user_prompt) / 4),
            output_tokens=usage.completion_tokens if usage else math.ceil(len(content) / 4),
            latency_ms=0.0,
            model_name=self.model,
        )
