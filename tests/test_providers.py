"""Tests for core.providers — all 13 providers."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.providers import list_providers, create_provider, _PROVIDERS


class TestProviderRegistry:
    def test_provider_count(self):
        assert len(_PROVIDERS) >= 10, f"Expected 10+ providers, got {len(_PROVIDERS)}"

    def test_all_instantiate(self):
        for name, cls in _PROVIDERS.items():
            instance = cls(api_key="test", model="test")
            assert instance is not None, f"Provider {name} failed to instantiate"

    def test_all_have_chat(self):
        for name, cls in _PROVIDERS.items():
            instance = cls(api_key="test", model="test")
            assert hasattr(instance, "chat"), f"Provider {name} missing chat()"

    def test_supports_tools_has_chat_with_tools(self):
        for name, cls in _PROVIDERS.items():
            if getattr(cls, "SUPPORTS_TOOLS", False):
                instance = cls(api_key="test", model="test")
                assert hasattr(instance, "chat_with_tools"), \
                    f"Provider {name} has SUPPORTS_TOOLS=True but no chat_with_tools()"

    def test_list_providers(self):
        providers = list_providers()
        assert len(providers) >= 10
        for p in providers:
            assert "name" in p
            assert "label" in p
            assert "needs_api_key" in p
            assert "known_models" in p

    def test_no_api_key_providers(self):
        no_key = [p for p in list_providers() if not p["needs_api_key"]]
        assert len(no_key) >= 5, "Should have 5+ providers that don't need API keys"


class TestEchoProvider:
    def test_hello(self):
        p = create_provider("echo", model="echo-v1")
        result = p.chat([{"role": "user", "content": "hello"}])
        assert "Echo" in result or "hello" in result.lower()

    def test_help(self):
        p = create_provider("echo", model="echo-v1")
        result = p.chat([{"role": "user", "content": "help"}])
        assert "tool" in result.lower() or "test" in result.lower()


class TestProviderValidation:
    def test_claude_needs_key(self):
        p = create_provider("claude", api_key="", model="test")
        ok, err = p.validate()
        assert not ok

    def test_echo_no_key(self):
        p = create_provider("echo", model="echo-v1")
        ok, err = p.validate()
        assert ok

    def test_ollama_no_key(self):
        p = create_provider("ollama", model="llama3")
        ok, err = p.validate()
        assert ok
