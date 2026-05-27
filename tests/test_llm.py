"""Unit tests for the LLM provider abstraction."""
import pytest
from unittest.mock import patch, MagicMock
from pipeline.llm import (
    GeminiProvider,
    AnthropicProvider,
    OpenAIProvider,
    create_provider,
)


# ── GeminiProvider ────────────────────────────────────────────────────────────
# Patch _generate directly — Gemini's `models` property is read-only and
# can't be patched via patch.object at the instance level.

class TestGeminiProvider:
    @pytest.fixture
    def provider(self):
        return GeminiProvider(api_key="test-gemini-key")

    def test_complete_returns_string(self, provider):
        with patch.object(provider, "_generate", return_value="Response text"):
            result = provider.complete("Test prompt", max_tokens=100)
        assert result == "Response text"

    def test_complete_fast_returns_string(self, provider):
        with patch.object(provider, "_generate", return_value="Fast response"):
            result = provider.complete_fast("Test prompt", max_tokens=100)
        assert result == "Fast response"

    def test_complete_uses_quality_model(self, provider):
        with patch.object(provider, "_generate", return_value="ok") as mock_gen:
            provider.complete("prompt", max_tokens=100)
        assert mock_gen.call_args[0][0] == provider._quality_model

    def test_complete_fast_uses_fast_model(self, provider):
        with patch.object(provider, "_generate", return_value="ok") as mock_gen:
            provider.complete_fast("prompt", max_tokens=100)
        assert mock_gen.call_args[0][0] == provider._fast_model

    def test_provider_name(self, provider):
        assert provider.name == "gemini"


# ── AnthropicProvider ─────────────────────────────────────────────────────────

class TestAnthropicProvider:
    @pytest.fixture
    def provider(self):
        return AnthropicProvider(api_key="test-anthropic-key")

    def test_complete_returns_string(self, provider):
        with patch.object(provider._client.messages, "create") as mock_create:
            mock_create.return_value.content = [MagicMock(text="Claude response")]
            result = provider.complete("prompt", max_tokens=100)
        assert result == "Claude response"

    def test_complete_fast_returns_string(self, provider):
        with patch.object(provider._client.messages, "create") as mock_create:
            mock_create.return_value.content = [MagicMock(text="Fast claude")]
            result = provider.complete_fast("prompt", max_tokens=100)
        assert result == "Fast claude"

    def test_complete_uses_quality_model(self, provider):
        with patch.object(provider._client.messages, "create") as mock_create:
            mock_create.return_value.content = [MagicMock(text="ok")]
            provider.complete("prompt", max_tokens=100)
        assert mock_create.call_args[1]["model"] == provider.QUALITY_MODEL

    def test_complete_fast_uses_fast_model(self, provider):
        with patch.object(provider._client.messages, "create") as mock_create:
            mock_create.return_value.content = [MagicMock(text="ok")]
            provider.complete_fast("prompt", max_tokens=100)
        assert mock_create.call_args[1]["model"] == provider.FAST_MODEL

    def test_provider_name(self, provider):
        assert provider.name == "anthropic"


# ── OpenAIProvider ────────────────────────────────────────────────────────────

openai_available = pytest.mark.skipif(
    __import__("importlib").util.find_spec("openai") is None,
    reason="openai package not installed"
)

class TestOpenAIProvider:
    @pytest.fixture
    def provider(self):
        return OpenAIProvider(api_key="test-openai-key")

    @openai_available
    def test_complete_returns_string(self, provider):
        with patch.object(provider._client.chat.completions, "create") as mock_create:
            mock_create.return_value.choices = [
                MagicMock(message=MagicMock(content="OpenAI response"))
            ]
            result = provider.complete("prompt", max_tokens=100)
        assert result == "OpenAI response"

    @openai_available
    def test_complete_fast_uses_fast_model(self, provider):
        with patch.object(provider._client.chat.completions, "create") as mock_create:
            mock_create.return_value.choices = [
                MagicMock(message=MagicMock(content="ok"))
            ]
            provider.complete_fast("prompt", max_tokens=100)
        assert mock_create.call_args[1]["model"] == provider.FAST_MODEL

    @openai_available
    def test_provider_name(self, provider):
        assert provider.name == "openai"


# ── create_provider factory ───────────────────────────────────────────────────

class TestCreateProvider:
    def test_creates_gemini_by_default(self):
        config = {"llm_provider": "gemini", "api_keys": {"gemini": "key"}}
        provider = create_provider(config)
        assert provider.name == "gemini"

    def test_creates_anthropic(self):
        config = {"llm_provider": "anthropic", "api_keys": {"anthropic": "key"}}
        provider = create_provider(config)
        assert provider.name == "anthropic"

    @openai_available
    def test_creates_openai(self):
        config = {"llm_provider": "openai", "api_keys": {"openai": "key"}}
        provider = create_provider(config)
        assert provider.name == "openai"

    def test_falls_back_to_env_var_for_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        config = {"llm_provider": "gemini", "api_keys": {}}
        provider = create_provider(config)
        assert provider.name == "gemini"

    def test_raises_on_missing_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        config = {"llm_provider": "gemini", "api_keys": {}}
        with pytest.raises(ValueError, match="API key"):
            create_provider(config)

    def test_raises_on_unknown_provider(self):
        config = {"llm_provider": "unknown_llm", "api_keys": {"unknown_llm": "key"}}
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider(config)

    def test_gemini_is_default_when_provider_not_specified(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        config = {"api_keys": {}}
        provider = create_provider(config)
        assert provider.name == "gemini"
