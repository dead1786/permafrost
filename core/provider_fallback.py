"""
Permafrost Provider Fallback Chain — Automatic failover between AI providers.

When the primary provider fails, automatically tries the next provider in the chain.
Uses error classification to make intelligent failover decisions:
  - Rate limit / overloaded: try next provider immediately
  - Auth error: skip provider permanently until reconfigured
  - Context overflow: compact and retry same provider first
  - Network error: retry with backoff, then failover

Usage:
    chain = ProviderFallbackChain([
        {"provider": "claude", "api_key": "sk-...", "model": "claude-sonnet-4-20250514"},
        {"provider": "openai", "api_key": "sk-...", "model": "gpt-4o"},
        {"provider": "ollama", "model": "llama3"},
    ])
    response = chain.chat(messages)

Config (config.json):
    {
        "fallback_chain": [
            {"provider": "claude", "api_key": "sk-...", "model": "claude-sonnet-4-20250514"},
            {"provider": "openai", "api_key": "sk-...", "model": "gpt-4o"}
        ],
        "fallback_cooldown": 300
    }
"""

import logging
import time
from typing import Optional

from .providers import BaseProvider, create_provider
from .security import classify_provider_error, FailoverReason

log = logging.getLogger("permafrost.fallback")

# Errors that should permanently disable a provider until reconfigured
PERMANENT_ERRORS = {FailoverReason.AUTH_PERMANENT, FailoverReason.MODEL_NOT_FOUND}

# Errors where retrying the same provider later might work
TRANSIENT_ERRORS = {
    FailoverReason.RATE_LIMIT,
    FailoverReason.OVERLOADED,
    FailoverReason.TIMEOUT,
    FailoverReason.NETWORK,
}

# Errors that should skip to next provider immediately
IMMEDIATE_FAILOVER = {
    FailoverReason.AUTH,
    FailoverReason.BILLING,
    FailoverReason.RATE_LIMIT,
    FailoverReason.OVERLOADED,
}

# Default cooldown before retrying a failed provider (seconds)
DEFAULT_COOLDOWN = 300


class ProviderFallbackChain:
    """Ordered chain of AI providers with automatic failover.

    Tries providers in order. When one fails, classifies the error and decides:
    - Skip to next provider (rate limit, auth, billing)
    - Retry with backoff then skip (timeout, network)
    - Disable permanently (revoked key, model not found)

    Providers that recover after cooldown are automatically re-enabled.
    """

    def __init__(self, chain_config: list[dict], cooldown: int = DEFAULT_COOLDOWN):
        """Initialize the fallback chain.

        Args:
            chain_config: List of provider configs, each with at least:
                {"provider": str, "api_key": str, "model": str}
            cooldown: Seconds before retrying a failed provider (default 300)
        """
        self.cooldown = cooldown
        self._providers: list[dict] = []  # [{config, instance, status, ...}]
        self._active_index = 0

        for i, cfg in enumerate(chain_config):
            provider_name = cfg.get("provider", "")
            if not provider_name:
                log.warning(f"Fallback chain[{i}]: missing 'provider' field, skipping")
                continue
            self._providers.append({
                "config": cfg,
                "instance": None,  # Lazy init
                "status": "ready",  # ready | cooldown | disabled
                "cooldown_until": 0,
                "fail_count": 0,
                "last_error": "",
                "last_reason": "",
                "total_calls": 0,
                "total_failures": 0,
            })

        if not self._providers:
            raise ValueError("Fallback chain requires at least one provider")

        log.info(
            f"Fallback chain initialized: {len(self._providers)} provider(s) "
            f"[{', '.join(p['config']['provider'] for p in self._providers)}]"
        )

    def _get_provider(self, index: int) -> BaseProvider:
        """Lazy-init and return provider instance at given index."""
        entry = self._providers[index]
        if entry["instance"] is None:
            cfg = entry["config"]
            entry["instance"] = create_provider(
                cfg["provider"],
                api_key=cfg.get("api_key", ""),
                model=cfg.get("model", ""),
                timeout=cfg.get("timeout", 120),
                max_retries=cfg.get("max_retries", 1),  # Low retries — chain handles retry
            )
        return entry["instance"]

    def _is_available(self, index: int) -> bool:
        """Check if a provider is available (not disabled or in cooldown)."""
        entry = self._providers[index]
        if entry["status"] == "disabled":
            return False
        if entry["status"] == "cooldown":
            if time.time() >= entry["cooldown_until"]:
                entry["status"] = "ready"
                entry["fail_count"] = 0
                log.info(f"Provider '{entry['config']['provider']}' cooldown expired, re-enabled")
                return True
            return False
        return True

    def _mark_failure(self, index: int, error: Exception):
        """Record a failure and update provider status based on error classification."""
        entry = self._providers[index]
        reason = classify_provider_error(error)
        entry["fail_count"] += 1
        entry["total_failures"] += 1
        entry["last_error"] = str(error)[:200]
        entry["last_reason"] = reason

        provider_name = entry["config"]["provider"]

        if reason in PERMANENT_ERRORS:
            entry["status"] = "disabled"
            log.warning(
                f"Provider '{provider_name}' DISABLED (permanent error: {reason}): {error}"
            )
        elif reason in TRANSIENT_ERRORS:
            # Exponential cooldown: 1x, 2x, 4x base cooldown (capped at 4x)
            multiplier = min(2 ** (entry["fail_count"] - 1), 4)
            cooldown_secs = self.cooldown * multiplier
            entry["status"] = "cooldown"
            entry["cooldown_until"] = time.time() + cooldown_secs
            log.info(
                f"Provider '{provider_name}' in cooldown for {cooldown_secs}s "
                f"(reason: {reason}, failures: {entry['fail_count']})"
            )
        else:
            # Unknown or format errors: short cooldown
            entry["status"] = "cooldown"
            entry["cooldown_until"] = time.time() + 60
            log.info(f"Provider '{provider_name}' short cooldown (reason: {reason})")

    def _mark_success(self, index: int):
        """Record a successful call, resetting failure state."""
        entry = self._providers[index]
        entry["status"] = "ready"
        entry["fail_count"] = 0
        entry["total_calls"] += 1

    def _get_next_available(self) -> Optional[int]:
        """Find the next available provider index. Returns None if all exhausted."""
        n = len(self._providers)
        for offset in range(n):
            idx = (self._active_index + offset) % n
            if self._is_available(idx):
                return idx
        return None

    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send messages through the fallback chain. Returns first successful response.

        Tries providers in order, skipping unavailable ones.
        On failure, classifies error and decides whether to try next provider.

        Raises RuntimeError if all providers fail.
        """
        errors = []
        tried = set()

        # Try up to len(providers) times (each provider at most once per call)
        for _ in range(len(self._providers)):
            idx = self._get_next_available()
            if idx is None or idx in tried:
                break

            tried.add(idx)
            entry = self._providers[idx]
            provider_name = entry["config"]["provider"]

            try:
                provider = self._get_provider(idx)
                log.debug(f"Trying provider '{provider_name}' (index {idx})")
                response = provider.chat(messages, **kwargs)
                self._mark_success(idx)
                self._active_index = idx  # Stick with working provider
                return response

            except Exception as e:
                reason = classify_provider_error(e)
                log.warning(
                    f"Provider '{provider_name}' failed ({reason}): {e}"
                )
                errors.append(f"{provider_name}: {e}")
                self._mark_failure(idx, e)

                # If context overflow, don't try other providers (they'll likely fail too)
                if reason == FailoverReason.CONTEXT_OVERFLOW:
                    raise

                # Move to next provider
                self._active_index = (idx + 1) % len(self._providers)

        # All providers exhausted
        error_summary = "; ".join(errors) if errors else "all providers unavailable"
        raise RuntimeError(f"All providers in fallback chain failed: {error_summary}")

    def chat_with_tools(self, messages: list[dict], tools: list[dict] = None,
                        **kwargs) -> dict:
        """Send messages with tool calling through the fallback chain.

        Same failover logic as chat(), but uses chat_with_tools for native tool support.
        Falls back to regular chat() if provider doesn't support tools.
        """
        errors = []
        tried = set()

        for _ in range(len(self._providers)):
            idx = self._get_next_available()
            if idx is None or idx in tried:
                break

            tried.add(idx)
            entry = self._providers[idx]
            provider_name = entry["config"]["provider"]

            try:
                provider = self._get_provider(idx)
                log.debug(f"Trying provider '{provider_name}' with tools (index {idx})")

                if hasattr(provider, "SUPPORTS_TOOLS") and provider.SUPPORTS_TOOLS:
                    result = provider.chat_with_tools(messages, tools=tools, **kwargs)
                else:
                    # Provider doesn't support tools — fall back to text mode
                    text = provider.chat(messages, **kwargs)
                    result = {"text": text, "tool_calls": []}

                self._mark_success(idx)
                self._active_index = idx
                return result

            except Exception as e:
                reason = classify_provider_error(e)
                log.warning(f"Provider '{provider_name}' failed ({reason}): {e}")
                errors.append(f"{provider_name}: {e}")
                self._mark_failure(idx, e)

                if reason == FailoverReason.CONTEXT_OVERFLOW:
                    raise

                self._active_index = (idx + 1) % len(self._providers)

        error_summary = "; ".join(errors) if errors else "all providers unavailable"
        raise RuntimeError(f"All providers in fallback chain failed: {error_summary}")

    @property
    def active_provider(self) -> Optional[str]:
        """Name of the currently active (preferred) provider."""
        idx = self._get_next_available()
        if idx is not None:
            return self._providers[idx]["config"]["provider"]
        return None

    @property
    def supports_tools(self) -> bool:
        """Whether the currently active provider supports native tool calling."""
        idx = self._get_next_available()
        if idx is not None:
            try:
                provider = self._get_provider(idx)
                return getattr(provider, "SUPPORTS_TOOLS", False)
            except Exception:
                return False
        return False

    def get_status(self) -> list[dict]:
        """Return status of all providers in the chain."""
        statuses = []
        for i, entry in enumerate(self._providers):
            status = {
                "index": i,
                "provider": entry["config"]["provider"],
                "model": entry["config"].get("model", ""),
                "status": entry["status"],
                "fail_count": entry["fail_count"],
                "total_calls": entry["total_calls"],
                "total_failures": entry["total_failures"],
                "last_error": entry["last_error"],
                "last_reason": entry["last_reason"],
                "is_active": i == self._active_index,
            }
            if entry["status"] == "cooldown":
                remaining = max(0, entry["cooldown_until"] - time.time())
                status["cooldown_remaining"] = round(remaining, 1)
            statuses.append(status)
        return statuses

    def reset(self, provider_name: str = None):
        """Reset failure state for a specific provider or all providers.

        Args:
            provider_name: Reset only this provider. If None, reset all.
        """
        for entry in self._providers:
            if provider_name and entry["config"]["provider"] != provider_name:
                continue
            entry["status"] = "ready"
            entry["fail_count"] = 0
            entry["cooldown_until"] = 0
            entry["last_error"] = ""
            entry["last_reason"] = ""
            if provider_name:
                log.info(f"Provider '{provider_name}' reset to ready")
                return
        if not provider_name:
            self._active_index = 0
            log.info("All providers reset to ready")


def create_fallback_chain(config: dict) -> Optional[ProviderFallbackChain]:
    """Create a fallback chain from Permafrost config.

    Config format:
        {
            "fallback_chain": [
                {"provider": "claude", "api_key": "...", "model": "..."},
                {"provider": "openai", "api_key": "...", "model": "..."},
            ],
            "fallback_cooldown": 300
        }

    Returns None if no fallback chain is configured.
    """
    chain_config = config.get("fallback_chain", [])
    if not chain_config or not isinstance(chain_config, list):
        return None

    cooldown = config.get("fallback_cooldown", DEFAULT_COOLDOWN)

    try:
        return ProviderFallbackChain(chain_config, cooldown=cooldown)
    except (ValueError, Exception) as e:
        log.warning(f"Failed to create fallback chain: {e}")
        return None
