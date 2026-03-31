"""Tests for the Provider Fallback Chain system."""

import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.provider_fallback import ProviderFallbackChain, create_fallback_chain
from core.security import classify_provider_error, FailoverReason


# ── Mock Provider Factory ───────────────────────────────────────

class MockProvider:
    """A mock provider that can be configured to succeed or fail."""
    LABEL = "Mock"
    NEEDS_API_KEY = False
    SUPPORTS_TOOLS = True
    DEFAULT_MODEL = "mock-v1"
    KNOWN_MODELS = ["mock-v1"]
    MODEL_HELP = ""

    def __init__(self, responses=None, errors=None, **kwargs):
        self.model = kwargs.get("model", "mock-v1")
        self.api_key = kwargs.get("api_key", "")
        self.timeout = kwargs.get("timeout", 120)
        self.max_retries = kwargs.get("max_retries", 1)
        self.extra = kwargs
        self._responses = list(responses or ["mock response"])
        self._errors = list(errors or [])
        self._call_count = 0

    def chat(self, messages, **kwargs):
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._errors) and self._errors[idx]:
            raise self._errors[idx]
        resp_idx = min(idx, len(self._responses) - 1)
        return self._responses[resp_idx]

    def chat_with_tools(self, messages, tools=None, **kwargs):
        text = self.chat(messages, **kwargs)
        return {"text": text, "tool_calls": []}

    def validate(self):
        return True, ""


def _make_chain_with_mocks(provider_mocks: list[MockProvider], cooldown=1):
    """Create a ProviderFallbackChain with pre-built mock providers injected.

    Args:
        provider_mocks: List of MockProvider instances, one per chain slot.
        cooldown: Cooldown seconds (default 1 for fast tests).
    """
    configs = [{"provider": f"mock_{i}", "model": "v1"} for i in range(len(provider_mocks))]
    mock_index = iter(range(len(provider_mocks)))

    def mock_create(name, **kwargs):
        idx = next(mock_index)
        return provider_mocks[idx]

    with patch("core.provider_fallback.create_provider", side_effect=mock_create):
        chain = ProviderFallbackChain(configs, cooldown=cooldown)
        # Force lazy init of all providers now (while patch is active)
        for i in range(len(provider_mocks)):
            chain._get_provider(i)
    return chain


# ── Tests: Error Classification ─────────────────────────────────

class TestErrorClassification:
    """Test that provider errors are correctly classified."""

    def test_auth_error(self):
        err = RuntimeError("401 Unauthorized: invalid api key")
        assert classify_provider_error(err) == FailoverReason.AUTH

    def test_auth_permanent(self):
        err = RuntimeError("401 permanently revoked banned")
        assert classify_provider_error(err) == FailoverReason.AUTH_PERMANENT

    def test_rate_limit(self):
        err = RuntimeError("429 Too Many Requests: rate_limit exceeded")
        assert classify_provider_error(err) == FailoverReason.RATE_LIMIT

    def test_overloaded(self):
        err = RuntimeError("529 server_overloaded")
        assert classify_provider_error(err) == FailoverReason.OVERLOADED

    def test_billing(self):
        err = RuntimeError("402 Payment Required: insufficient credit")
        assert classify_provider_error(err) == FailoverReason.BILLING

    def test_context_overflow(self):
        err = RuntimeError("context_length exceeded: maximum context window")
        assert classify_provider_error(err) == FailoverReason.CONTEXT_OVERFLOW

    def test_timeout(self):
        err = RuntimeError("Request timed out after 120s")
        assert classify_provider_error(err) == FailoverReason.TIMEOUT

    def test_network(self):
        err = RuntimeError("Connection refused ECONNREFUSED")
        assert classify_provider_error(err) == FailoverReason.NETWORK

    def test_model_not_found(self):
        err = RuntimeError("404 model_not_found: gpt-99 does not exist")
        assert classify_provider_error(err) == FailoverReason.MODEL_NOT_FOUND

    def test_format_error(self):
        err = RuntimeError("400 bad request: malformed JSON")
        assert classify_provider_error(err) == FailoverReason.FORMAT_ERROR

    def test_unknown(self):
        err = RuntimeError("something weird happened")
        assert classify_provider_error(err) == FailoverReason.UNKNOWN

    def test_error_chain(self):
        """Errors with __cause__ are also checked."""
        inner = RuntimeError("429 rate_limit exceeded")
        outer = RuntimeError("provider call failed")
        outer.__cause__ = inner
        assert classify_provider_error(outer) == FailoverReason.RATE_LIMIT


# ── Tests: Fallback Chain Logic ─────────────────────────────────

class TestFallbackChain:
    """Test the ProviderFallbackChain logic."""

    def test_single_provider_success(self):
        """Single provider, no errors."""
        chain = _make_chain_with_mocks([
            MockProvider(responses=["hello!"]),
        ])
        result = chain.chat([{"role": "user", "content": "hi"}])
        assert result == "hello!"

    def test_failover_to_second_provider(self):
        """First provider fails with rate limit, second succeeds."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("429 rate_limit")]),
            MockProvider(responses=["backup response"]),
        ])
        result = chain.chat([{"role": "user", "content": "hi"}])
        assert result == "backup response"

    def test_failover_to_third_provider(self):
        """First two providers fail, third succeeds."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("401 unauthorized")]),
            MockProvider(errors=[RuntimeError("503 overloaded")]),
            MockProvider(responses=["third works"]),
        ])
        result = chain.chat([{"role": "user", "content": "hi"}])
        assert result == "third works"

    def test_all_providers_fail(self):
        """All providers fail, raises RuntimeError."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("401 unauthorized")]),
            MockProvider(errors=[RuntimeError("503 overloaded")]),
        ])
        with pytest.raises(RuntimeError, match="All providers"):
            chain.chat([{"role": "user", "content": "hi"}])

    def test_context_overflow_not_failover(self):
        """Context overflow should raise immediately, not try next provider."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("context_length exceeded")]),
            MockProvider(responses=["should not reach"]),
        ])
        with pytest.raises(RuntimeError, match="context_length"):
            chain.chat([{"role": "user", "content": "hi"}])

    def test_cooldown_recovery(self):
        """Provider recovers after cooldown period."""
        # Provider that fails first call but succeeds on second
        call_count = [0]
        provider = MockProvider()

        # Override chat to fail first, succeed second
        original_chat = provider.chat
        def flaky_chat(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("429 rate_limit")
            return "recovered!"
        provider.chat = flaky_chat

        chain = _make_chain_with_mocks([provider], cooldown=1)

        # First call fails
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "hi"}])

        # Wait for cooldown
        time.sleep(1.5)

        # Should recover
        result = chain.chat([{"role": "user", "content": "hi"}])
        assert result == "recovered!"

    def test_permanent_error_disables_provider(self):
        """Auth permanent error disables provider (no cooldown recovery)."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("401 permanently revoked banned")]),
            MockProvider(responses=["backup ok"]),
        ])
        result = chain.chat([{"role": "user", "content": "hi"}])
        assert result == "backup ok"

        status = chain.get_status()
        assert status[0]["status"] == "disabled"  # Permanently disabled
        assert status[0]["last_reason"] == FailoverReason.AUTH_PERMANENT

    def test_sticks_with_working_provider(self):
        """After failover, stays with the working provider."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("429 rate_limit")]),
            MockProvider(responses=["backup"]),
        ])
        chain.chat([{"role": "user", "content": "first"}])
        assert chain._active_index == 1

        # Second call should go to backup directly
        result = chain.chat([{"role": "user", "content": "second"}])
        assert result == "backup"


# ── Tests: chat_with_tools ──────────────────────────────────────

class TestFallbackChainTools:
    """Test chat_with_tools through the fallback chain."""

    def test_chat_with_tools_success(self):
        """chat_with_tools works through chain."""
        chain = _make_chain_with_mocks([
            MockProvider(responses=["tool response"]),
        ])
        result = chain.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
        assert result["text"] == "tool response"
        assert result["tool_calls"] == []

    def test_chat_with_tools_failover(self):
        """chat_with_tools fails over to backup provider."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("429 rate_limit")]),
            MockProvider(responses=["backup tools"]),
        ])
        result = chain.chat_with_tools(
            [{"role": "user", "content": "hi"}],
        )
        assert result["text"] == "backup tools"

    def test_non_tool_provider_fallback(self):
        """Provider without SUPPORTS_TOOLS falls back to regular chat."""
        provider = MockProvider(responses=["plain text"])
        provider.SUPPORTS_TOOLS = False
        chain = _make_chain_with_mocks([provider])
        result = chain.chat_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
        )
        assert result["text"] == "plain text"
        assert result["tool_calls"] == []


# ── Tests: Status and Reset ─────────────────────────────────────

class TestFallbackChainStatus:
    """Test status reporting and reset functionality."""

    def test_get_status(self):
        """Status report includes all providers."""
        chain = _make_chain_with_mocks([
            MockProvider(),
            MockProvider(),
        ])
        status = chain.get_status()
        assert len(status) == 2
        assert status[0]["provider"] == "mock_0"
        assert status[0]["status"] == "ready"
        assert status[0]["is_active"] is True
        assert status[1]["is_active"] is False

    def test_status_after_failure(self):
        """Status reflects failure state."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("503 overloaded")]),
        ])
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "hi"}])

        status = chain.get_status()
        assert status[0]["status"] == "cooldown"
        assert status[0]["fail_count"] == 1
        assert "cooldown_remaining" in status[0]

    def test_status_after_success(self):
        """Status reflects success state."""
        chain = _make_chain_with_mocks([
            MockProvider(responses=["ok"]),
        ])
        chain.chat([{"role": "user", "content": "hi"}])

        status = chain.get_status()
        assert status[0]["total_calls"] == 1
        assert status[0]["fail_count"] == 0

    def test_active_provider_property(self):
        """active_provider returns the current provider name."""
        chain = _make_chain_with_mocks([
            MockProvider(),
        ])
        assert chain.active_provider == "mock_0"

    def test_supports_tools_property(self):
        """supports_tools reflects active provider capability."""
        provider = MockProvider()
        provider.SUPPORTS_TOOLS = True
        chain = _make_chain_with_mocks([provider])
        assert chain.supports_tools is True

    def test_reset_specific_provider(self):
        """Reset a specific provider clears its failure state."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("503 overloaded")]),
        ])
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "hi"}])

        assert chain.get_status()[0]["status"] != "ready"
        chain.reset("mock_0")
        assert chain.get_status()[0]["status"] == "ready"

    def test_reset_all(self):
        """Reset all providers."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("429 rate_limit")]),
            MockProvider(errors=[RuntimeError("503 overloaded")]),
        ])
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "hi"}])

        chain.reset()
        for s in chain.get_status():
            assert s["status"] == "ready"


# ── Tests: Factory Function ─────────────────────────────────────

class TestCreateFallbackChain:
    """Test the create_fallback_chain factory function."""

    def test_no_config_returns_none(self):
        assert create_fallback_chain({}) is None
        assert create_fallback_chain({"fallback_chain": []}) is None

    def test_valid_config(self):
        chain = create_fallback_chain({
            "fallback_chain": [
                {"provider": "echo", "model": "echo-v1"},
            ],
        })
        assert chain is not None
        assert len(chain._providers) == 1

    def test_custom_cooldown(self):
        chain = create_fallback_chain({
            "fallback_chain": [
                {"provider": "echo", "model": "echo-v1"},
            ],
            "fallback_cooldown": 600,
        })
        assert chain is not None
        assert chain.cooldown == 600

    def test_empty_provider_skipped(self):
        """Entries with no provider name are skipped."""
        chain = create_fallback_chain({
            "fallback_chain": [
                {"model": "v1"},  # Missing provider -- skipped
                {"provider": "echo", "model": "echo-v1"},
            ],
        })
        assert chain is not None
        assert len(chain._providers) == 1


# ── Tests: Edge Cases ───────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_chain_raises(self):
        with pytest.raises(ValueError, match="at least one provider"):
            ProviderFallbackChain([])

    def test_all_disabled_returns_none_active(self):
        """When all providers are disabled, active_provider returns None."""
        chain = _make_chain_with_mocks([
            MockProvider(errors=[RuntimeError("401 permanently revoked banned")]),
        ])
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "hi"}])
        assert chain.active_provider is None

    def test_exponential_cooldown(self):
        """Repeated failures increase cooldown duration."""
        call_count = [0]
        provider = MockProvider()
        def always_fail(messages, **kwargs):
            call_count[0] += 1
            raise RuntimeError("503 overloaded")
        provider.chat = always_fail

        chain = _make_chain_with_mocks([provider], cooldown=10)

        # First failure
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "first"}])

        entry = chain._providers[0]
        # After first failure: cooldown = 10 * 1 = 10s
        assert entry["fail_count"] == 1

        # Force ready to test again
        entry["status"] = "ready"
        with pytest.raises(RuntimeError):
            chain.chat([{"role": "user", "content": "second"}])

        # After second failure: cooldown = 10 * 2 = 20s
        assert entry["fail_count"] == 2
        # Check cooldown is longer than first time
        remaining = entry["cooldown_until"] - time.time()
        assert remaining > 10  # Should be ~20s
