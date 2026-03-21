"""
Permafrost AI Providers — Multi-model abstraction layer.

Supports: Claude, OpenAI, Gemini, Ollama, OpenRouter.
Each provider implements the same interface so brain.py doesn't care which one is used.

Usage:
    provider = create_provider("claude", api_key="sk-...", model="claude-sonnet-4-20250514")
    response = provider.chat([{"role": "user", "content": "hello"}])
"""

import json
import logging
import subprocess
from abc import ABC, abstractmethod

log = logging.getLogger("permafrost.providers")

# ── Provider registry ─────────────────────────────────────────

_PROVIDERS: dict[str, type] = {}


def register_provider(name: str):
    """Decorator to register a provider class."""
    def decorator(cls):
        _PROVIDERS[name] = cls
        return cls
    return decorator


def create_provider(name: str, **kwargs) -> "BaseProvider":
    """Factory: create a provider by name."""
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Available: {list(_PROVIDERS.keys())}")
    return _PROVIDERS[name](**kwargs)


def list_providers() -> list[dict]:
    """List all registered providers with metadata."""
    return [
        {"name": name, "label": cls.LABEL, "needs_api_key": cls.NEEDS_API_KEY,
         "default_model": cls.DEFAULT_MODEL, "model_help": cls.MODEL_HELP}
        for name, cls in _PROVIDERS.items()
    ]


# ── Base class ─────────────────────────────────────────────────

class BaseProvider(ABC):
    """Abstract base class for AI providers."""

    LABEL: str = "Unknown"
    NEEDS_API_KEY: bool = True
    DEFAULT_MODEL: str = ""
    MODEL_HELP: str = ""

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 120,
                 max_retries: int = 2, **kwargs):
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra = kwargs

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send messages and get a response.

        Args:
            messages: List of {"role": "user"|"assistant"|"system", "content": "..."}

        Returns:
            Response text string.
        """
        ...

    def simple(self, prompt: str, **kwargs) -> str:
        """Convenience: single prompt in, response out."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def stream(self, messages: list[dict], **kwargs):
        """Stream response tokens. Yields text chunks.

        Default implementation falls back to non-streaming chat().
        Override in subclasses for true streaming support.
        """
        yield self.chat(messages, **kwargs)

    def _retry(self, func, *args, **kwargs) -> str:
        """Execute with retry logic."""
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_err = e
                if attempt < self.max_retries:
                    log.warning(f"[{self.LABEL}] attempt {attempt}/{self.max_retries} failed: {e}")
                    import time
                    time.sleep(min(2 ** attempt, 10))
                else:
                    log.error(f"[{self.LABEL}] all {self.max_retries} attempts failed: {e}")
        raise RuntimeError(f"{self.LABEL}: {last_err} (after {self.max_retries} retries)")

    def validate(self) -> tuple[bool, str]:
        """Validate configuration. Returns (ok, error_message)."""
        if self.NEEDS_API_KEY and not self.api_key:
            return False, f"{self.LABEL} requires an API key"
        if not self.model:
            return False, f"{self.LABEL} requires a model ID"
        return True, ""


# ── Claude ─────────────────────────────────────────────────────

@register_provider("claude")
class ClaudeProvider(BaseProvider):
    LABEL = "Claude (Anthropic)"
    NEEDS_API_KEY = True
    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    MODEL_HELP = "e.g. claude-sonnet-4-20250514, claude-opus-4-20250514"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        try:
            import anthropic
        except ImportError:
            return self._chat_via_cli(messages)

        client = anthropic.Anthropic(api_key=self.api_key)
        # Separate system message
        system = ""
        chat_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_msgs.append(m)

        params = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": chat_msgs,
        }
        if system:
            params["system"] = system

        response = client.messages.create(**params)
        return response.content[0].text

    def _chat_via_cli(self, messages: list[dict]) -> str:
        """Fallback: use claude CLI for persistent session."""
        prompt = messages[-1]["content"] if messages else ""
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "stream-json"],
            capture_output=True, text=True, timeout=self.timeout,
            encoding="utf-8", errors="replace"
        )
        for line in result.stdout.strip().split("\n"):
            try:
                evt = json.loads(line)
                if evt.get("type") == "result":
                    return evt.get("result", "")
            except json.JSONDecodeError:
                continue
        return result.stdout.strip() or "[no response]"


# ── OpenAI ─────────────────────────────────────────────────────

@register_provider("openai")
class OpenAIProvider(BaseProvider):
    LABEL = "GPT (OpenAI)"
    NEEDS_API_KEY = True
    DEFAULT_MODEL = "gpt-4o"
    MODEL_HELP = "e.g. gpt-4o, gpt-4o-mini, o3-mini"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            timeout=self.timeout,
        )
        return response.choices[0].message.content


# ── Gemini ─────────────────────────────────────────────────────

@register_provider("gemini")
class GeminiProvider(BaseProvider):
    LABEL = "Gemini (Google)"
    NEEDS_API_KEY = True
    DEFAULT_MODEL = "gemini-2.0-flash"
    MODEL_HELP = "e.g. gemini-2.0-flash, gemini-2.5-pro"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)

        # Extract system prompt for system_instruction
        system_text = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [m["content"]]})

        model_kwargs = {}
        if system_text.strip():
            model_kwargs["system_instruction"] = system_text.strip()

        model = genai.GenerativeModel(self.model, **model_kwargs)
        response = model.generate_content(contents)
        return response.text


# ── Ollama ─────────────────────────────────────────────────────

@register_provider("ollama")
class OllamaProvider(BaseProvider):
    LABEL = "Ollama (Local)"
    NEEDS_API_KEY = False
    DEFAULT_MODEL = "llama3"
    MODEL_HELP = "e.g. llama3, mistral, codestral, gemma2"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.endpoint = self.api_key or "http://localhost:11434"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        r = requests.post(
            f"{self.endpoint}/api/chat",
            json={"model": self.model, "messages": messages, "stream": False},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "[no response]")

    def validate(self) -> tuple[bool, str]:
        if not self.model:
            return False, "Ollama requires a model name"
        return True, ""


# ── OpenRouter ─────────────────────────────────────────────────

@register_provider("openrouter")
class OpenRouterProvider(BaseProvider):
    LABEL = "OpenRouter"
    NEEDS_API_KEY = True
    DEFAULT_MODEL = "anthropic/claude-sonnet-4"
    MODEL_HELP = "e.g. anthropic/claude-sonnet-4, openai/gpt-4o, google/gemini-2.0-flash"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://github.com/permafrost-framework",
            },
            json={"model": self.model, "messages": messages},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
