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
         "default_model": cls.DEFAULT_MODEL, "model_help": cls.MODEL_HELP,
         "known_models": cls.KNOWN_MODELS}
        for name, cls in _PROVIDERS.items()
    ]


# ── Base class ─────────────────────────────────────────────────

class BaseProvider(ABC):
    """Abstract base class for AI providers."""

    LABEL: str = "Unknown"
    NEEDS_API_KEY: bool = True
    DEFAULT_MODEL: str = ""
    MODEL_HELP: str = ""
    # Known models for dropdown (override in subclass)
    KNOWN_MODELS: list[str] = []

    def __init__(self, api_key: str = "", model: str = "", timeout: int = 120,
                 max_retries: int = 2, **kwargs):
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra = kwargs

    def _track_usage(self, prompt_tokens: int, completion_tokens: int):
        """Record token usage after an API call."""
        try:
            from core.token_tracker import track_usage
            track_usage(prompt_tokens, completion_tokens, model=self.model)
        except Exception as e:
            log.debug(f"Token tracking skipped: {e}")

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send messages and get a response.

        Args:
            messages: List of {"role": "user"|"assistant"|"system", "content": "..."}

        Returns:
            Response text string.
        """
        ...

    # Whether this provider supports native function calling
    SUPPORTS_TOOLS: bool = False

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None,
                        **kwargs) -> dict:
        """Send messages with native tool/function calling support.

        Args:
            messages: Conversation messages
            tools: List of tool schemas for function calling

        Returns:
            {
                "text": str,             # AI text response
                "tool_calls": [          # List of tool calls (empty if none)
                    {"name": str, "args": dict},
                    ...
                ]
            }

        Default: falls back to regular chat() (no native tool support).
        Override in subclasses for native function calling.
        """
        response_text = self.chat(messages, **kwargs)
        return {"text": response_text, "tool_calls": []}

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
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    MODEL_HELP = "e.g. claude-sonnet-4-20250514, claude-opus-4-20250514"
    KNOWN_MODELS = ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-5-20251001"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        try:
            import anthropic
        except ImportError:
            return self._chat_via_cli(messages)

        client = anthropic.Anthropic(api_key=self.api_key)
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
        try:
            self._track_usage(response.usage.input_tokens, response.usage.output_tokens)
        except (AttributeError, TypeError):
            pass
        return response.content[0].text

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None,
                        **kwargs) -> dict:
        try:
            import anthropic
        except ImportError:
            return {"text": self.chat(messages, **kwargs), "tool_calls": []}

        client = anthropic.Anthropic(api_key=self.api_key)
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
        if tools:
            params["tools"] = tools

        response = client.messages.create(**params)
        try:
            self._track_usage(response.usage.input_tokens, response.usage.output_tokens)
        except (AttributeError, TypeError):
            pass

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "args": block.input, "id": block.id})
        return {"text": "\n".join(text_parts), "tool_calls": tool_calls, "stop_reason": response.stop_reason}

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
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "gpt-4o"
    MODEL_HELP = "e.g. gpt-4o, gpt-4o-mini, o3-mini"
    KNOWN_MODELS = ["gpt-4o", "gpt-4o-mini", "o3-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"]

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
        try:
            usage = response.usage
            if usage:
                self._track_usage(usage.prompt_tokens, usage.completion_tokens)
        except (AttributeError, TypeError):
            pass
        return response.choices[0].message.content

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None,
                        **kwargs) -> dict:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        params = {"model": self.model, "messages": messages, "timeout": self.timeout}
        if tools:
            params["tools"] = tools
        response = client.chat.completions.create(**params)
        try:
            if response.usage:
                self._track_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        except (AttributeError, TypeError):
            pass
        msg = response.choices[0].message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append({"name": tc.function.name, "args": args, "id": tc.id})
        return {"text": msg.content or "", "tool_calls": tool_calls}


# ── Gemini ─────────────────────────────────────────────────────

@register_provider("gemini")
class GeminiProvider(BaseProvider):
    LABEL = "Gemini (Google)"
    NEEDS_API_KEY = True
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "gemini-2.0-flash"
    MODEL_HELP = "e.g. gemini-2.0-flash, gemini-2.5-pro"
    KNOWN_MODELS = ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-pro"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _prep_gemini(self, messages):
        """Prepare messages for Gemini API."""
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        system_text = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [m["content"]]})
        return genai, system_text, contents

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        genai, system_text, contents = self._prep_gemini(messages)
        model_kwargs = {}
        if system_text.strip():
            model_kwargs["system_instruction"] = system_text.strip()
        model = genai.GenerativeModel(self.model, **model_kwargs)
        response = model.generate_content(contents)
        try:
            meta = response.usage_metadata
            if meta:
                self._track_usage(meta.prompt_token_count or 0, meta.candidates_token_count or 0)
        except (AttributeError, TypeError):
            pass
        return response.text

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None,
                        **kwargs) -> dict:
        genai, system_text, contents = self._prep_gemini(messages)
        model_kwargs = {}
        if system_text.strip():
            model_kwargs["system_instruction"] = system_text.strip()
        if tools:
            model_kwargs["tools"] = tools
        model = genai.GenerativeModel(self.model, **model_kwargs)
        response = model.generate_content(contents)
        try:
            meta = response.usage_metadata
            if meta:
                self._track_usage(meta.prompt_token_count or 0, meta.candidates_token_count or 0)
        except (AttributeError, TypeError):
            pass
        text_parts = []
        tool_calls = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                fc = part.function_call
                tool_calls.append({"name": fc.name, "args": dict(fc.args) if fc.args else {}})
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)
        return {"text": "\n".join(text_parts), "tool_calls": tool_calls}


# ── Ollama ─────────────────────────────────────────────────────

@register_provider("ollama")
class OllamaProvider(BaseProvider):
    LABEL = "Ollama (Local)"
    NEEDS_API_KEY = False
    DEFAULT_MODEL = "llama3"
    MODEL_HELP = "e.g. llama3, mistral, codestral, gemma2"
    KNOWN_MODELS = ["llama3", "llama3.1", "mistral", "codestral", "gemma2", "phi3", "qwen2"]

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
        data = r.json()
        # Ollama may include token counts in some versions; track if available
        try:
            prompt_t = data.get("prompt_eval_count", 0)
            completion_t = data.get("eval_count", 0)
            if prompt_t or completion_t:
                self._track_usage(prompt_t, completion_t)
        except (AttributeError, TypeError):
            pass
        return data.get("message", {}).get("content", "[no response]")

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
        data = r.json()
        # Track token usage (OpenAI-compatible format)
        try:
            usage = data.get("usage", {})
            if usage:
                self._track_usage(
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
        except (AttributeError, TypeError):
            pass
        return data["choices"][0]["message"]["content"]


# ── Claude CLI (use your Claude subscription, no API key) ───

@register_provider("claude-cli")
class ClaudeCLIProvider(BaseProvider):
    """Use your Claude subscription via the 'claude' CLI command.

    No API key needed — uses your existing Claude Code / Claude Max login.
    Requires 'claude' CLI installed and authenticated on this machine.
    """
    LABEL = "Claude CLI (Your Subscription)"
    NEEDS_API_KEY = False
    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    MODEL_HELP = "Uses your Claude subscription. Model: claude-sonnet-4, claude-opus-4"
    KNOWN_MODELS = ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-5-20251001"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        # Build prompt from messages
        parts = []
        for m in messages:
            if m["role"] == "system":
                parts.append(f"[System]\n{m['content']}")
            elif m["role"] == "user":
                parts.append(f"[User]\n{m['content']}")
            elif m["role"] == "assistant":
                parts.append(f"[Assistant]\n{m['content']}")
        prompt = "\n\n".join(parts)

        # Write to temp file to avoid shell escaping issues
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(prompt)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["claude", "-p", f"@{tmp_path}", "--output-format", "stream-json"],
                capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace",
            )

            # Parse stream-json output
            for line in result.stdout.strip().split("\n"):
                try:
                    evt = json.loads(line)
                    if evt.get("type") == "result":
                        return evt.get("result", "")
                except json.JSONDecodeError:
                    continue

            # Fallback: try plain text output
            if result.stdout.strip():
                return result.stdout.strip()

            if result.stderr.strip():
                return f"[error] {result.stderr.strip()[:500]}"

            return "[error] No response from Claude CLI"
        finally:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass

    def validate(self) -> tuple[bool, str]:
        # Check if claude CLI is available and logged in
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False, (
                    "Claude CLI not responding.\n"
                    "Steps to fix:\n"
                    "1. Install: npm install -g @anthropic-ai/claude-code\n"
                    "2. Login: claude login\n"
                    "3. Follow the browser prompt to authenticate"
                )
            # Check if logged in by trying a simple prompt
            test = subprocess.run(
                ["claude", "-p", "hi", "--output-format", "stream-json"],
                capture_output=True, text=True, timeout=15,
            )
            if test.returncode != 0 and "auth" in test.stderr.lower():
                return False, (
                    "Claude CLI installed but not logged in.\n"
                    "Run in terminal: claude login\n"
                    "Then follow the browser prompt to authenticate."
                )
            return True, ""
        except FileNotFoundError:
            return False, (
                "Claude CLI not installed.\n"
                "Steps:\n"
                "1. Install Node.js from nodejs.org\n"
                "2. Run: npm install -g @anthropic-ai/claude-code\n"
                "3. Run: claude login\n"
                "4. Follow the browser prompt"
            )
        except Exception as e:
            return False, f"Claude CLI check failed: {e}"


# ── Custom Endpoint (any OpenAI-compatible local proxy) ──────

@register_provider("custom")
class CustomEndpointProvider(BaseProvider):
    """Connect to ANY OpenAI-compatible endpoint (Claude Max Proxy, LiteLLM, text-gen-webui, etc).

    Set API Key field to the base URL, e.g. http://localhost:3456/v1
    Model field to the model name available on that endpoint.
    """
    LABEL = "Custom Endpoint (Local Proxy)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "default"
    MODEL_HELP = "Any model available on your local proxy"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # api_key field is used as base_url for custom endpoints
        self.base_url = self.api_key or "http://localhost:3456/v1"
        if not self.base_url.startswith("http"):
            self.base_url = f"http://{self.base_url}"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        r = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        try:
            usage = data.get("usage", {})
            if usage:
                self._track_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        except (AttributeError, TypeError):
            pass
        return data["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(
            f"{self.base_url}/chat/completions",
            json=params, timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        if not self.model:
            return False, "Model name required"
        return True, ""


# ── Qwen (free, OAuth device code flow) ──────────────────────

@register_provider("qwen")
class QwenProvider(BaseProvider):
    """Qwen (Tongyi Qianwen) — free AI via OAuth device code flow.

    No API key needed. On first use, displays a URL + code.
    User visits the URL in browser and enters the code.
    Token is saved and auto-refreshes.
    """
    LABEL = "Qwen (Free, OAuth Login)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "qwen-coder-plus-latest"
    MODEL_HELP = "e.g. qwen-coder-plus-latest, qwen-turbo-latest, qwen-max-latest"
    KNOWN_MODELS = ["qwen-coder-plus-latest", "qwen-turbo-latest", "qwen-max-latest", "qwen-plus-latest"]

    OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
    OAUTH_BASE = "https://chat.qwen.ai"
    API_BASE = "https://chat.qwen.ai/api/v1"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token_file = None

    def _get_token_file(self):
        if self._token_file is None:
            import os
            data_dir = os.path.expanduser("~/.permafrost")
            self._token_file = os.path.join(data_dir, "qwen-oauth-token.json")
        return self._token_file

    def _load_token(self) -> dict:
        import os
        tf = self._get_token_file()
        if os.path.exists(tf):
            try:
                return json.loads(open(tf, "r", encoding="utf-8").read())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_token(self, token_data: dict):
        with open(self._get_token_file(), "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)

    def _get_access_token(self) -> str:
        """Get valid access token, refreshing or doing device flow if needed."""
        import time as _time
        token = self._load_token()

        # Check if we have a valid token
        if token.get("access_token"):
            expires_at = token.get("expires_at", 0)
            if _time.time() < expires_at - 60:
                return token["access_token"]
            # Try refresh
            if token.get("refresh_token"):
                try:
                    return self._refresh_token(token["refresh_token"])
                except Exception:
                    pass

        # Need new device code flow
        return self._device_code_flow()

    def _device_code_flow(self) -> str:
        """OAuth device code flow — user visits URL and enters code."""
        import requests, time as _time

        # Request device code
        r = requests.post(f"{self.OAUTH_BASE}/api/v1/oauth2/device-code", json={
            "client_id": self.OAUTH_CLIENT_ID,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()

        device_code = data.get("device_code", "")
        user_code = data.get("user_code", "")
        verification_url = data.get("verification_uri", data.get("verification_url", ""))
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 300)

        log.info(f"Qwen OAuth: Visit {verification_url} and enter code: {user_code}")

        # Also try to notify user through channels
        try:
            from core.scheduler import PFScheduler
            sched = PFScheduler()
            sched.notify_user(
                f"Qwen OAuth Login Required!\n"
                f"Visit: {verification_url}\n"
                f"Enter code: {user_code}\n"
                f"Expires in {expires_in // 60} minutes."
            )
        except Exception:
            pass

        # Poll for token
        deadline = _time.time() + expires_in
        while _time.time() < deadline:
            _time.sleep(interval)
            try:
                r = requests.post(f"{self.OAUTH_BASE}/api/v1/oauth2/token", json={
                    "client_id": self.OAUTH_CLIENT_ID,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                }, timeout=10)
                if r.status_code == 200:
                    token_data = r.json()
                    token_data["expires_at"] = _time.time() + token_data.get("expires_in", 3600)
                    self._save_token(token_data)
                    log.info("Qwen OAuth: Login successful!")
                    return token_data["access_token"]
            except Exception:
                pass

        raise RuntimeError("Qwen OAuth: Login timed out. Please try again.")

    def _refresh_token(self, refresh_token: str) -> str:
        import requests, time as _time
        r = requests.post(f"{self.OAUTH_BASE}/api/v1/oauth2/token", json={
            "client_id": self.OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=10)
        r.raise_for_status()
        token_data = r.json()
        token_data["expires_at"] = _time.time() + token_data.get("expires_in", 3600)
        self._save_token(token_data)
        return token_data["access_token"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        token = self._get_access_token()
        r = requests.post(
            f"{self.API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": self.model, "messages": messages},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        try:
            usage = data.get("usage", {})
            if usage:
                self._track_usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        except (AttributeError, TypeError):
            pass
        return data["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        token = self._get_access_token()
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(
            f"{self.API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json=params, timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        return True, ""  # No API key needed


# ── GitHub Copilot (subscription OAuth) ──────────────────────

@register_provider("copilot")
class CopilotProvider(BaseProvider):
    """GitHub Copilot — uses your Copilot subscription via device code OAuth.

    No API key needed. Authenticates through GitHub device code flow.
    Requires active GitHub Copilot subscription.
    """
    LABEL = "GitHub Copilot (Subscription)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "gpt-4o"
    MODEL_HELP = "e.g. gpt-4o, claude-sonnet-4 (models available depend on your Copilot plan)"

    GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
    DEVICE_CODE_URL = "https://github.com/login/device/code"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
    API_BASE = "https://api.githubcopilot.com"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token_file = None

    def _get_token_file(self):
        if self._token_file is None:
            import os
            self._token_file = os.path.join(os.path.expanduser("~/.permafrost"), "copilot-oauth-token.json")
        return self._token_file

    def _load_token(self) -> dict:
        import os
        tf = self._get_token_file()
        if os.path.exists(tf):
            try:
                return json.loads(open(tf, "r", encoding="utf-8").read())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_token(self, data: dict):
        with open(self._get_token_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_copilot_token(self) -> str:
        """Get Copilot API token (GitHub OAuth -> Copilot token exchange)."""
        import time as _time, requests
        token = self._load_token()

        # Check copilot token validity
        if token.get("copilot_token"):
            if _time.time() < token.get("copilot_expires_at", 0) - 60:
                return token["copilot_token"]

        # Need GitHub OAuth token first
        gh_token = token.get("github_token")
        if not gh_token:
            gh_token = self._github_device_flow()
            token["github_token"] = gh_token
            self._save_token(token)

        # Exchange for Copilot token
        r = requests.get(self.COPILOT_TOKEN_URL, headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/json",
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        token["copilot_token"] = data.get("token", "")
        token["copilot_expires_at"] = data.get("expires_at", _time.time() + 1800)
        self._save_token(token)
        return token["copilot_token"]

    def _github_device_flow(self) -> str:
        """GitHub device code OAuth flow."""
        import requests, time as _time

        r = requests.post(self.DEVICE_CODE_URL, data={
            "client_id": self.GITHUB_CLIENT_ID,
            "scope": "read:user",
        }, headers={"Accept": "application/json"}, timeout=10)
        r.raise_for_status()
        data = r.json()

        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        interval = data.get("interval", 5)

        log.info(f"GitHub Copilot: Visit {verification_uri} and enter code: {user_code}")

        try:
            from core.scheduler import PFScheduler
            PFScheduler().notify_user(
                f"GitHub Copilot Login!\nVisit: {verification_uri}\nCode: {user_code}"
            )
        except Exception:
            pass

        for _ in range(60):
            _time.sleep(interval)
            r = requests.post(self.TOKEN_URL, data={
                "client_id": self.GITHUB_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }, headers={"Accept": "application/json"}, timeout=10)
            data = r.json()
            if "access_token" in data:
                log.info("GitHub OAuth: Login successful!")
                return data["access_token"]
            if data.get("error") == "authorization_pending":
                continue
            if data.get("error") in ("slow_down", ""):
                _time.sleep(5)
                continue
            raise RuntimeError(f"GitHub OAuth error: {data.get('error_description', data.get('error'))}")

        raise RuntimeError("GitHub OAuth: Login timed out.")

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        token = self._get_copilot_token()
        r = requests.post(
            f"{self.API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Editor-Version": "Permafrost/0.8.0",
            },
            json={"model": self.model, "messages": messages},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        token = self._get_copilot_token()
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(
            f"{self.API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Editor-Version": "Permafrost/0.8.0",
            },
            json=params, timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        return True, ""


# ── OpenAI Codex (ChatGPT subscription OAuth) ───────────────

@register_provider("openai-codex")
class OpenAICodexProvider(BaseProvider):
    """Use your ChatGPT subscription via browser OAuth login.

    No API key needed — authenticates through ChatGPT web login.
    Requires active ChatGPT Plus/Pro/Team subscription.
    """
    LABEL = "ChatGPT (Subscription OAuth)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "gpt-4o"
    MODEL_HELP = "e.g. gpt-4o, o3-mini, gpt-4o-mini"

    AUTH_URL = "https://auth.openai.com/authorize"
    TOKEN_URL = "https://auth.openai.com/oauth/token"
    API_BASE = "https://api.openai.com/v1"
    CLIENT_ID = "pdlLIX2Y72MIl2rhLhTE9VV9bN905kBh"
    REDIRECT_URI = "http://localhost:1455/oauth-callback"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token_file = os.path.expanduser("~/.permafrost/openai-codex-token.json")

    def _load_token(self) -> dict:
        if os.path.exists(self._token_file):
            try:
                return json.loads(open(self._token_file, "r", encoding="utf-8").read())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_token(self, data: dict):
        with open(self._token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_access_token(self) -> str:
        import time as _time
        token = self._load_token()
        if token.get("access_token") and _time.time() < token.get("expires_at", 0) - 60:
            return token["access_token"]
        if token.get("refresh_token"):
            try:
                return self._refresh(token["refresh_token"])
            except Exception:
                pass
        return self._browser_oauth()

    def _browser_oauth(self) -> str:
        """Start local HTTP server, open browser for OAuth, catch redirect."""
        import threading, urllib.parse, http.server, time as _time, webbrowser

        auth_code = [None]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                if "code" in params:
                    auth_code[0] = params["code"][0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Login successful! You can close this tab.")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Login failed.")
            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 1455), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        import secrets
        state = secrets.token_urlsafe(32)
        url = (f"{self.AUTH_URL}?client_id={self.CLIENT_ID}"
               f"&redirect_uri={urllib.parse.quote(self.REDIRECT_URI)}"
               f"&response_type=code&scope=openid%20profile%20email"
               f"&state={state}")

        log.info(f"OpenAI OAuth: Opening browser for login...")
        try:
            webbrowser.open(url)
        except Exception:
            pass

        try:
            from core.scheduler import PFScheduler
            PFScheduler().notify_user(f"ChatGPT Login Required!\nVisit: {url}")
        except Exception:
            pass

        thread.join(timeout=120)
        server.server_close()

        if not auth_code[0]:
            raise RuntimeError("ChatGPT OAuth timed out. Visit the URL manually.")

        return self._exchange_code(auth_code[0])

    def _exchange_code(self, code: str) -> str:
        import requests, time as _time
        r = requests.post(self.TOKEN_URL, json={
            "client_id": self.CLIENT_ID,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.REDIRECT_URI,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = _time.time() + data.get("expires_in", 3600)
        self._save_token(data)
        return data["access_token"]

    def _refresh(self, refresh_token: str) -> str:
        import requests, time as _time
        r = requests.post(self.TOKEN_URL, json={
            "client_id": self.CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = _time.time() + data.get("expires_in", 3600)
        self._save_token(data)
        return data["access_token"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        token = self._get_access_token()
        r = requests.post(f"{self.API_BASE}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json={"model": self.model, "messages": messages}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        token = self._get_access_token()
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(f"{self.API_BASE}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json=params, timeout=self.timeout)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        return True, ""


# ── MiniMax Portal (free OAuth) ──────────────────────────────

@register_provider("minimax")
class MiniMaxProvider(BaseProvider):
    """MiniMax AI — free coding assistant via OAuth login.

    No API key needed. Free tier available.
    """
    LABEL = "MiniMax (Free OAuth)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "MiniMax-M1"
    MODEL_HELP = "e.g. MiniMax-M1, abab6.5s-chat"

    API_BASE_GLOBAL = "https://api.minimax.io/v1"
    API_BASE_CN = "https://api.minimaxi.com/v1"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token_file = os.path.expanduser("~/.permafrost/minimax-oauth-token.json")
        # Use api_key field as region selector: "cn" for China, default global
        self.api_base = self.API_BASE_CN if self.api_key == "cn" else self.API_BASE_GLOBAL

    def _load_token(self) -> dict:
        if os.path.exists(self._token_file):
            try:
                return json.loads(open(self._token_file, "r", encoding="utf-8").read())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_token(self, data: dict):
        with open(self._token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_token(self) -> str:
        import time as _time
        token = self._load_token()
        if token.get("access_token") and _time.time() < token.get("expires_at", 0) - 60:
            return token["access_token"]
        # MiniMax uses simple API key from their portal — store after first manual entry
        if token.get("api_key"):
            return token["api_key"]
        # Prompt user to get key from minimax portal
        try:
            from core.scheduler import PFScheduler
            PFScheduler().notify_user(
                "MiniMax Setup: Visit https://www.minimax.io to get your free API key, "
                "then set it in PF Settings (API Key field)."
            )
        except Exception:
            pass
        raise RuntimeError("MiniMax: Set your API key from minimax.io in Settings")

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        token = self._get_token()
        r = requests.post(f"{self.api_base}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json={"model": self.model, "messages": messages}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        token = self._get_token()
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(f"{self.api_base}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json=params, timeout=self.timeout)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        return True, ""


# ── Chutes (free OAuth + PKCE) ───────────────────────────────

@register_provider("chutes")
class ChutesProvider(BaseProvider):
    """Chutes AI — free tier with OAuth PKCE login.

    No API key needed. Supports multiple open-source models.
    """
    LABEL = "Chutes (Free OAuth)"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3-0324"
    MODEL_HELP = "e.g. deepseek-ai/DeepSeek-V3-0324, meta-llama/Llama-4-Scout-17B-16E"

    AUTH_URL = "https://api.chutes.ai/idp/authorize"
    TOKEN_URL = "https://api.chutes.ai/idp/token"
    API_BASE = "https://llm.chutes.ai/v1"
    REDIRECT_URI = "http://127.0.0.1:1456/oauth-callback"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token_file = os.path.expanduser("~/.permafrost/chutes-oauth-token.json")

    def _load_token(self) -> dict:
        if os.path.exists(self._token_file):
            try:
                return json.loads(open(self._token_file, "r", encoding="utf-8").read())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_token(self, data: dict):
        with open(self._token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_access_token(self) -> str:
        import time as _time
        token = self._load_token()
        if token.get("access_token") and _time.time() < token.get("expires_at", 0) - 60:
            return token["access_token"]
        if token.get("refresh_token"):
            try:
                return self._refresh(token["refresh_token"])
            except Exception:
                pass
        return self._pkce_oauth()

    def _pkce_oauth(self) -> str:
        """OAuth with PKCE — browser login + local callback."""
        import threading, urllib.parse, http.server, hashlib, base64, secrets, time as _time, webbrowser

        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)

        auth_code = [None]

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                if "code" in params:
                    auth_code[0] = params["code"][0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Login successful! You can close this tab.")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Login failed.")
            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 1456), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        url = (f"{self.AUTH_URL}?client_id=permafrost"
               f"&redirect_uri={urllib.parse.quote(self.REDIRECT_URI)}"
               f"&response_type=code&state={state}"
               f"&code_challenge={code_challenge}&code_challenge_method=S256")

        log.info(f"Chutes OAuth: Opening browser...")
        try:
            webbrowser.open(url)
        except Exception:
            pass

        try:
            from core.scheduler import PFScheduler
            PFScheduler().notify_user(f"Chutes Login Required!\nVisit: {url}")
        except Exception:
            pass

        thread.join(timeout=120)
        server.server_close()

        if not auth_code[0]:
            raise RuntimeError("Chutes OAuth timed out.")

        return self._exchange_code(auth_code[0], code_verifier)

    def _exchange_code(self, code: str, verifier: str) -> str:
        import requests, time as _time
        r = requests.post(self.TOKEN_URL, json={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.REDIRECT_URI,
            "code_verifier": verifier,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = _time.time() + data.get("expires_in", 3600)
        self._save_token(data)
        return data["access_token"]

    def _refresh(self, refresh_token: str) -> str:
        import requests, time as _time
        r = requests.post(self.TOKEN_URL, json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = _time.time() + data.get("expires_in", 3600)
        self._save_token(data)
        return data["access_token"]

    def chat(self, messages: list[dict], **kwargs) -> str:
        return self._retry(self._do_chat, messages, **kwargs)

    def _do_chat(self, messages: list[dict], **kwargs) -> str:
        import requests
        token = self._get_access_token()
        r = requests.post(f"{self.API_BASE}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json={"model": self.model, "messages": messages}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None, **kwargs) -> dict:
        import requests
        token = self._get_access_token()
        params = {"model": self.model, "messages": messages}
        if tools:
            params["tools"] = tools
        r = requests.post(f"{self.API_BASE}/chat/completions", headers={
            "Authorization": f"Bearer {token}",
        }, json=params, timeout=self.timeout)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    args = {}
                tool_calls.append({"name": tc["function"]["name"], "args": args})
        return {"text": msg.get("content", ""), "tool_calls": tool_calls}

    def validate(self) -> tuple[bool, str]:
        return True, ""


# ── Echo (Free Testing) ──────────────────────────────────────

@register_provider("echo")
class EchoProvider(BaseProvider):
    """Free testing provider — no API calls, no cost.

    Echoes back a smart response based on the user message,
    demonstrating tool use and memory features without needing any API key.
    """
    LABEL = "Echo (Free Testing)"
    NEEDS_API_KEY = False
    DEFAULT_MODEL = "echo-v1"
    MODEL_HELP = "Free testing mode — no API key needed, no cost"

    def chat(self, messages: list[dict], **kwargs) -> str:
        import re as _re

        # Get the last user message
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        # Check if this is a tool result round — parse and display results
        if "[TOOL_RESULT" in user_msg:
            results = _re.findall(r'\[TOOL_RESULT tool=(\w+)\]\n(.*?)\n\[/TOOL_RESULT\]', user_msg, _re.DOTALL)
            if results:
                parts = []
                for tool_name, output in results:
                    parts.append(f"**{tool_name}** result:\n{output.strip()[:300]}")
                return "Here's what I found:\n\n" + "\n\n".join(parts)
            return "Tool executed successfully."

        # Strip source tag before keyword matching (prevents false matches like "unknown" -> "now")
        clean_msg = _re.sub(r'\[Source:.*?\]\s*', '', user_msg)
        lower = clean_msg.lower()

        # Use clean message (no source tag) for tool args too
        safe_msg = clean_msg.replace('"', '\\"')[:100]

        # Demonstrate tool use when appropriate keywords detected
        if any(kw in lower for kw in ["remember", "save", "note", "record"]):
            return (
                f"I'll save that to memory.\n\n"
                f"[TOOL_CALL]\n"
                f'{{"name": "memory_note", "args": {{"key": "user_note", "value": "{safe_msg}", "type": "context"}}}}\n'
                f"[/TOOL_CALL]"
            )

        if any(kw in lower for kw in ["search", "find", "recall", "what did"]):
            return (
                f"Let me search my memory.\n\n"
                f"[TOOL_CALL]\n"
                f'{{"name": "memory_search", "args": {{"query": "{safe_msg}"}}}}\n'
                f"[/TOOL_CALL]"
            )

        if any(kw in lower for kw in ["file", "read", "list", "dir"]):
            return (
                f"Let me check the files.\n\n"
                f"[TOOL_CALL]\n"
                f'{{"name": "list_files", "args": {{"path": "."}}}}\n'
                f"[/TOOL_CALL]"
            )

        if any(kw in lower for kw in ["time", "date"]):
            return (
                f"[TOOL_CALL]\n"
                f'{{"name": "python_exec", "args": {{"code": "from datetime import datetime; print(datetime.now())"}}}}\n'
                f"[/TOOL_CALL]"
            )

        if any(kw in lower for kw in ["hello", "hi", "hey"]):
            return f"Hello! I'm running in Echo mode (free testing). I can demonstrate tools, memory, and channels without any API cost. Try asking me to remember something, search memory, or check files!"

        if any(kw in lower for kw in ["help", "what can"]):
            return (
                "I'm Permafrost Brain running in **Echo mode** (free, no API).\n\n"
                "Things to test:\n"
                "- Say 'remember that I like coffee' → tests memory_note tool\n"
                "- Say 'search memory for coffee' → tests memory_search tool\n"
                "- Say 'what time is it' → tests python_exec tool\n"
                "- Say 'list files' → tests list_files tool\n"
                "- Send from different channels (TG/DC/Web) → tests routing\n\n"
                "Switch to a real provider (Claude/GPT/Gemini) for actual AI responses."
            )

        # Default echo
        return f"[Echo] Received: {user_msg[:200]}\n\nI'm in free testing mode. Say 'help' to see what I can demonstrate!"

    def validate(self) -> tuple[bool, str]:
        return True, ""
