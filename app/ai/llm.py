"""LLM provider abstraction layer using LangChain.

This module provides a provider-agnostic interface for LLM operations.
Switching between providers (Groq, OpenAI, Anthropic, etc.) requires only
a config change -- no code changes in the agent or tools.

Supported Providers:
    - groq: Groq cloud (llama-3.3-70b-versatile) -- fast, free tier available.
    - openai: OpenAI (gpt-4o, gpt-4o-mini) -- most capable, paid.
    - anthropic: Anthropic (claude-sonnet-4-20250514) -- strong reasoning, paid.

Abbreviations Used in This Module
----------------------------------
- LLM: Large Language Model -- the AI model that generates responses.
- API: Application Programming Interface -- a set of endpoints for interaction.
"""

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

logger = logging.getLogger("app.ai.llm")


class ProviderType(str, Enum):
    """Supported LLM providers."""

    GROQ = "groq"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement the generate() method which takes a list
    of messages and returns a response with a `.content` attribute.
    """

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> Any:
        """Generate a response from the LLM."""
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        """Stream a response from the LLM, yielding chunks."""
        ...


class GroqProvider(LLMProvider):
    """Groq cloud provider -- fast inference, free tier available."""

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b"):
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> Any:
        from langchain_groq import ChatGroq

        llm = ChatGroq(
            groq_api_key=self.api_key,
            model_name=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return llm.invoke(messages)

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        from langchain_groq import ChatGroq

        llm = ChatGroq(
            groq_api_key=self.api_key,
            model_name=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        for chunk in llm.stream(messages):
            if chunk.content:
                yield chunk.content


class OpenAIProvider(LLMProvider):
    """OpenAI provider -- most capable models, paid."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> Any:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=self.api_key,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return llm.invoke(messages)

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            api_key=self.api_key,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        for chunk in llm.stream(messages):
            if chunk.content:
                yield chunk.content


class AnthropicProvider(LLMProvider):
    """Anthropic provider -- strong reasoning, paid."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> Any:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            api_key=self.api_key,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return llm.invoke(messages)

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            api_key=self.api_key,
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        for chunk in llm.stream(messages):
            if chunk.content:
                yield chunk.content


_PROVIDER_MAP: dict[ProviderType, type[LLMProvider]] = {
    ProviderType.GROQ: GroqProvider,
    ProviderType.OPENAI: OpenAIProvider,
    ProviderType.ANTHROPIC: AnthropicProvider,
}


def create_provider() -> LLMProvider:
    """Create an LLM provider based on environment configuration.

    Environment Variables:
        LLM_PROVIDER: The provider to use (groq, openai, anthropic). Default: groq.
        LLM_API_KEY: The API key for the selected provider.
        LLM_MODEL: Optional model override for the selected provider.

    Returns:
        A configured LLMProvider instance.

    Raises:
        ValueError: If the provider is not supported or API key is missing.
    """
    from app.core.config import get_settings

    s = get_settings()
    provider_name = getattr(s, "llm_provider", "groq") or "groq"

    try:
        provider_type = ProviderType(provider_name)
    except ValueError:
        raise ValueError(
            f"Unsupported LLM provider: '{provider_name}'. "
            f"Supported: {[p.value for p in ProviderType]}"
        )

    api_key = _get_api_key(provider_type, s)
    if not api_key:
        raise ValueError(
            f"API key not configured for provider '{provider_name}'. "
            f"Set the appropriate environment variable."
        )

    model = getattr(s, "llm_model", None)

    provider_class = _PROVIDER_MAP[provider_type]
    if model:
        return provider_class(api_key=api_key, model=model)
    return provider_class(api_key=api_key)


def _get_api_key(provider_type: ProviderType, settings: Any) -> str:
    """Get the API key for a provider with fallback logic."""
    if provider_type == ProviderType.GROQ:
        return (
            getattr(settings, "ai_groq_api_key", None)
            or getattr(settings, "llm_api_key", None)
            or ""
        )
    elif provider_type == ProviderType.OPENAI:
        return (
            getattr(settings, "openai_api_key", None)
            or getattr(settings, "llm_api_key", None)
            or ""
        )
    elif provider_type == ProviderType.ANTHROPIC:
        return (
            getattr(settings, "anthropic_api_key", None)
            or getattr(settings, "llm_api_key", None)
            or ""
        )
    return ""
