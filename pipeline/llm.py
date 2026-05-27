"""Configurable LLM provider abstraction.

All pipeline components use a provider object instead of calling SDK clients
directly. This makes it trivial to switch between Gemini, Anthropic, OpenAI,
and Ollama without touching business logic.

Usage:
    from pipeline.llm import create_provider
    provider = create_provider(preferences)
    text = provider.complete(prompt, max_tokens=1024)        # quality model
    text = provider.complete_fast(prompt, max_tokens=300)    # cheap/fast model

Provider    Quality model               Fast model
─────────────────────────────────────────────────────────────────────────
gemini      gemini-3.1-flash-lite       gemini-3.1-flash-lite   (FREE tier)
anthropic   claude-sonnet-4-6       claude-haiku-4-5-20251001
openai      gpt-4o                  gpt-4o-mini
ollama      gemma3:12b (default)    gemma3:4b (default)     (LOCAL — no key)

Ollama config keys (in preferences.yaml):
    ollama_base_url:      http://localhost:11434/v1   (default)
    ollama_quality_model: gemma3:12b                  (default)
    ollama_fast_model:    gemma3:4b                   (default)

API keys are read from:
    1. preferences["api_keys"][provider_name]
    2. Environment variable: GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
    (Ollama does not require an API key.)
"""
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Optional imports — each provider only requires its own SDK
try:
    from google import genai as _genai
    from google.genai import types as _genai_types
except ImportError:
    _genai = None
    _genai_types = None

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

try:
    import openai as _openai
except ImportError:
    _openai = None


class LLMProvider(ABC):
    """Base provider interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        """Call the quality model. Use for matching, cover letters, resumes."""

    @abstractmethod
    def complete_fast(self, prompt: str, max_tokens: int = 300) -> str:
        """Call the cheap/fast model. Use for redaction, headers, story selection."""

    def complete_json(self, prompt: str, max_tokens: int = 1024) -> str:
        """Call the quality model with JSON output enforcement where supported.

        Cloud providers (Gemini, Anthropic, OpenAI) rely on prompt instructions alone.
        OllamaProvider overrides this to add format=json at the API level.
        """
        return self.complete(prompt, max_tokens=max_tokens)


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    DEFAULT_QUALITY_MODEL = "models/gemini-3.1-flash-lite"
    DEFAULT_FAST_MODEL = "models/gemini-3.1-flash-lite"

    _MAX_RETRIES = 3

    def __init__(self, api_key: str, quality_model: str = DEFAULT_QUALITY_MODEL,
                 fast_model: str = DEFAULT_FAST_MODEL):
        if _genai is None:
            raise ImportError("google-genai is required: pip install google-genai")
        self._client = _genai.Client(api_key=api_key)
        self._quality_model = quality_model
        self._fast_model = fast_model

    @property
    def name(self) -> str:
        return "gemini"

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        return self._generate(self._quality_model, prompt, max_tokens)

    def complete_fast(self, prompt: str, max_tokens: int = 300) -> str:
        return self._generate(self._fast_model, prompt, max_tokens)

    def _generate(self, model: str, prompt: str, max_tokens: int) -> str:
        import re as _re
        import time
        config = _genai_types.GenerateContentConfig(max_output_tokens=max_tokens)
        for attempt in range(self._MAX_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                # For thinking models, extract only the text parts (skip thought_signature)
                parts = response.candidates[0].content.parts
                text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
                return "".join(text_parts).strip()
            except Exception as exc:
                err = str(exc)
                if "429" in err and attempt < self._MAX_RETRIES - 1:
                    # Use Gemini's suggested retry delay if present, else back off
                    m = _re.search(r"retry[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*s", err, _re.IGNORECASE)
                    wait = float(m.group(1)) + 2 if m else self._RATE_LIMIT_DELAY * (2 ** attempt)
                    logger.warning("Rate limited — waiting %.0fs (attempt %d/%d)", wait, attempt + 1, self._MAX_RETRIES)
                    time.sleep(wait)
                else:
                    raise


# ── Anthropic ─────────────────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    QUALITY_MODEL = "claude-sonnet-4-6"
    FAST_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str):
        if _anthropic is None:
            raise ImportError("anthropic is required: pip install anthropic")
        self._client = _anthropic.Anthropic(api_key=api_key)

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        return self._generate(self.QUALITY_MODEL, prompt, max_tokens)

    def complete_fast(self, prompt: str, max_tokens: int = 300) -> str:
        return self._generate(self.FAST_MODEL, prompt, max_tokens)

    def _generate(self, model: str, prompt: str, max_tokens: int) -> str:
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    QUALITY_MODEL = "gpt-4o"
    FAST_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str):
        if _openai is None:
            raise ImportError("openai is required: pip install openai")
        self._client = _openai.OpenAI(api_key=api_key)

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        return self._generate(self.QUALITY_MODEL, prompt, max_tokens)

    def complete_fast(self, prompt: str, max_tokens: int = 300) -> str:
        return self._generate(self.FAST_MODEL, prompt, max_tokens)

    def _generate(self, model: str, prompt: str, max_tokens: int) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()


# ── Ollama ────────────────────────────────────────────────────────────────────

class OllamaProvider(LLMProvider):
    """Local Ollama server via OpenAI-compatible API. No API key required."""

    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    DEFAULT_QUALITY_MODEL = "gemma3:12b"
    DEFAULT_FAST_MODEL = "gemma3:4b"
    DEFAULT_NUM_CTX = 8192  # Ollama defaults to 2048 which is too small for our prompts

    def __init__(self, base_url: str, quality_model: str, fast_model: str, num_ctx: int = DEFAULT_NUM_CTX):
        if _openai is None:
            raise ImportError("openai is required: pip install openai")
        # api_key is required by the openai SDK but ignored by Ollama
        self._client = _openai.OpenAI(base_url=base_url, api_key="ollama")
        self._quality_model = quality_model
        self._fast_model = fast_model
        self._num_ctx = num_ctx

    @property
    def name(self) -> str:
        return "ollama"

    def complete(self, prompt: str, max_tokens: int = 1024) -> str:
        return self._generate(self._quality_model, prompt, max_tokens)

    def complete_fast(self, prompt: str, max_tokens: int = 300) -> str:
        return self._generate(self._fast_model, prompt, max_tokens)

    # JSON Schema that the scoring prompt must conform to
    _MATCH_SCHEMA = {
        "type": "json_schema",
        "json_schema": {
            "name": "match_result",
            "schema": {
                "type": "object",
                "properties": {
                    "score":         {"type": "integer", "minimum": 1, "maximum": 10},
                    "summary":       {"type": "string"},
                    "strengths":     {"type": "array", "items": {"type": "string"}},
                    "gaps":          {"type": "array", "items": {"type": "string"}},
                    "location_note": {"type": "string"},
                },
                "required": ["score", "summary", "strengths", "gaps"],
            },
        },
    }

    def complete_json(self, prompt: str, max_tokens: int = 1024) -> str:
        return self._generate(self._quality_model, prompt, max_tokens, json_mode=True)

    def _generate(self, model: str, prompt: str, max_tokens: int, json_mode: bool = False) -> str:
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"options": {"num_ctx": self._num_ctx}},
        )
        if json_mode:
            kwargs["response_format"] = self._MATCH_SCHEMA
        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            usage = response.usage
            content = choice.message.content or ""
            logger.warning(
                "TRUNCATION: response hit max_tokens=%d limit.\n"
                "  Root cause: prompt used %d tokens, model generated %d tokens "
                "(total %d, limit %d).\n"
                "  Last 200 chars of truncated output: ...%s\n"
                "  Fix: increase max_tokens, reduce prompt length, or shorten "
                "output instructions.",
                max_tokens,
                usage.prompt_tokens if usage else -1,
                usage.completion_tokens if usage else -1,
                usage.total_tokens if usage else -1,
                max_tokens,
                content[-200:].replace("\n", " "),
            )
        return choice.message.content.strip()


# ── Factory ───────────────────────────────────────────────────────────────────

_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_KEYED_PROVIDERS = {
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def create_provider(config: dict) -> LLMProvider:
    """Create an LLM provider from preferences config.

    Input:  config dict — expects keys: llm_provider (str), api_keys (dict),
            and optionally ollama_base_url / ollama_quality_model / ollama_fast_model.
    Output: LLMProvider instance

    Key resolution order (for keyed providers):
        1. config["api_keys"][provider_name]
        2. Environment variable (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)

    Raises ValueError if provider is unknown or no API key can be found (for keyed providers).
    """
    provider_name = config.get("llm_provider", "gemini").lower()
    all_providers = list(_KEYED_PROVIDERS) + ["ollama"]

    if provider_name not in all_providers:
        raise ValueError(
            f"Unknown provider: '{provider_name}'. "
            f"Choose from: {', '.join(all_providers)}"
        )

    if provider_name == "ollama":
        base_url = config.get("ollama_base_url", OllamaProvider.DEFAULT_BASE_URL)
        quality_model = config.get("ollama_quality_model", OllamaProvider.DEFAULT_QUALITY_MODEL)
        fast_model = config.get("ollama_fast_model", OllamaProvider.DEFAULT_FAST_MODEL)
        num_ctx = config.get("ollama_num_ctx", OllamaProvider.DEFAULT_NUM_CTX)
        logger.info("Using LLM provider: ollama (%s / %s) at %s, num_ctx=%d", quality_model, fast_model, base_url, num_ctx)
        return OllamaProvider(base_url=base_url, quality_model=quality_model, fast_model=fast_model, num_ctx=num_ctx)

    # Resolve API key for cloud providers
    api_keys = config.get("api_keys", {}) or {}
    api_key = api_keys.get(provider_name) or os.environ.get(_ENV_VARS[provider_name])

    if not api_key:
        env_var = _ENV_VARS[provider_name]
        raise ValueError(
            f"API key for '{provider_name}' not found. "
            f"Set it in preferences.yaml under api_keys.{provider_name} "
            f"or export {env_var}=your-key"
        )

    if provider_name == "gemini":
        quality_model = config.get("gemini_quality_model", GeminiProvider.DEFAULT_QUALITY_MODEL)
        fast_model = config.get("gemini_fast_model", GeminiProvider.DEFAULT_FAST_MODEL)
        logger.info("Using LLM provider: gemini (%s / %s)", quality_model, fast_model)
        return GeminiProvider(api_key=api_key, quality_model=quality_model, fast_model=fast_model)

    logger.info("Using LLM provider: %s", provider_name)
    return _KEYED_PROVIDERS[provider_name](api_key=api_key)
