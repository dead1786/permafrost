"""
Tests for core/providers.py — Multi-model provider abstraction.
"""

import unittest
from unittest.mock import patch, MagicMock

from core.providers import (
    BaseProvider, create_provider, list_providers,
    ClaudeProvider, OpenAIProvider, GeminiProvider,
    OllamaProvider, OpenRouterProvider,
)


class TestProviderRegistry(unittest.TestCase):
    """Test provider factory and listing."""

    def test_create_claude(self):
        p = create_provider("claude", api_key="test", model="test-model")
        self.assertIsInstance(p, ClaudeProvider)

    def test_create_openai(self):
        p = create_provider("openai", api_key="test", model="gpt-4o")
        self.assertIsInstance(p, OpenAIProvider)

    def test_create_gemini(self):
        p = create_provider("gemini", api_key="test", model="gemini-2.0-flash")
        self.assertIsInstance(p, GeminiProvider)

    def test_create_ollama(self):
        p = create_provider("ollama", model="llama3")
        self.assertIsInstance(p, OllamaProvider)

    def test_create_openrouter(self):
        p = create_provider("openrouter", api_key="test")
        self.assertIsInstance(p, OpenRouterProvider)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            create_provider("nonexistent")

    def test_list_providers(self):
        providers = list_providers()
        self.assertGreater(len(providers), 0)
        names = [p["name"] for p in providers]
        self.assertIn("claude", names)
        self.assertIn("openai", names)
        self.assertIn("ollama", names)

    def test_provider_metadata(self):
        providers = list_providers()
        for p in providers:
            self.assertIn("name", p)
            self.assertIn("label", p)
            self.assertIn("needs_api_key", p)
            self.assertIn("default_model", p)


class TestBaseProviderValidation(unittest.TestCase):
    """Test provider validation logic."""

    def test_claude_needs_api_key(self):
        p = create_provider("claude", api_key="", model="test")
        ok, err = p.validate()
        self.assertFalse(ok)
        self.assertIn("API key", err)

    def test_claude_has_default_model(self):
        p = ClaudeProvider(api_key="test", model="")
        # model="" gets replaced by DEFAULT_MODEL in __init__
        self.assertEqual(p.model, ClaudeProvider.DEFAULT_MODEL)
        ok, _ = p.validate()
        self.assertTrue(ok)

    def test_claude_valid(self):
        p = create_provider("claude", api_key="sk-test", model="claude-sonnet-4-20250514")
        ok, err = p.validate()
        self.assertTrue(ok)

    def test_ollama_no_key_needed(self):
        p = create_provider("ollama", model="llama3")
        ok, err = p.validate()
        self.assertTrue(ok)

    def test_ollama_has_default_model(self):
        p = create_provider("ollama", model="")
        # model="" gets replaced by DEFAULT_MODEL in __init__
        self.assertEqual(p.model, OllamaProvider.DEFAULT_MODEL)
        ok, _ = p.validate()
        self.assertTrue(ok)


class TestProviderSimple(unittest.TestCase):
    """Test convenience methods."""

    def test_simple_delegates_to_chat(self):
        p = create_provider("claude", api_key="test", model="test")
        with patch.object(p, "chat", return_value="hello") as mock_chat:
            result = p.simple("hi")
            mock_chat.assert_called_once()
            self.assertEqual(result, "hello")

    def test_stream_default_fallback(self):
        p = create_provider("claude", api_key="test", model="test")
        with patch.object(p, "chat", return_value="response"):
            chunks = list(p.stream([{"role": "user", "content": "hi"}]))
            self.assertEqual(chunks, ["response"])


class TestProviderRetry(unittest.TestCase):
    """Test retry logic."""

    def test_retry_succeeds_on_second_try(self):
        p = create_provider("claude", api_key="test", model="test", max_retries=3)
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ConnectionError("temporary failure")
            return "success"

        result = p._retry(flaky)
        self.assertEqual(result, "success")
        self.assertEqual(call_count["n"], 2)

    def test_retry_exhausted_raises(self):
        p = create_provider("claude", api_key="test", model="test", max_retries=2)

        def always_fails(*args, **kwargs):
            raise ConnectionError("permanent failure")

        with self.assertRaises(RuntimeError):
            p._retry(always_fails)


class TestClaudeProvider(unittest.TestCase):
    """Test Claude-specific behavior."""

    def test_separates_system_message(self):
        p = ClaudeProvider(api_key="test-key", model="test-model")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hello")]
        mock_client.messages.create.return_value = mock_response

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = p._do_chat([
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hi"},
            ])

        call_kwargs = mock_client.messages.create.call_args
        self.assertEqual(call_kwargs.kwargs.get("system") or call_kwargs[1].get("system"), "you are helpful")
        self.assertEqual(result, "hello")


class TestOllamaProvider(unittest.TestCase):
    """Test Ollama-specific behavior."""

    def test_default_endpoint(self):
        p = OllamaProvider(model="llama3")
        self.assertEqual(p.endpoint, "http://localhost:11434")

    def test_custom_endpoint(self):
        p = OllamaProvider(api_key="http://gpu-server:11434", model="llama3")
        self.assertEqual(p.endpoint, "http://gpu-server:11434")


if __name__ == "__main__":
    unittest.main()
