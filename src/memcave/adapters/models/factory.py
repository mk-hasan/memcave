"""Select a chat provider without importing the OpenAI SDK until needed."""

from memcave.adapters.models.fake import FakeModelProvider
from memcave.domain.errors import ProviderError
from memcave.ports.protocols import ModelProvider


def build_model_provider(
    name: str,
    *,
    model: str = "",
    fallback_model: str = "fake-memcave",
    base_url: str = "",
    api_key: str = "",
    timeout: float = 60.0,
) -> ModelProvider:
    """Return the configured chat provider. `fake` stays the default for tests."""
    if name == "fake":
        return FakeModelProvider(model_name=model or fallback_model)
    if name == "openai_compatible":
        from memcave.adapters.models.openai import OpenAICompatibleModelProvider

        return OpenAICompatibleModelProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    raise ProviderError(f"unknown model provider {name}")
