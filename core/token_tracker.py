"""
Permafrost Token Usage Tracker — Records API token consumption and estimates cost.

Usage:
    from core.token_tracker import track_usage, get_usage_summary

    track_usage(prompt_tokens=150, completion_tokens=80, model="claude-sonnet-4-20250514")
    summary = get_usage_summary()
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

log = logging.getLogger("permafrost.token_tracker")

DATA_DIR = Path(os.environ.get("PF_DATA_DIR", os.path.expanduser("~/.permafrost")))
USAGE_FILE = DATA_DIR / "token-usage.json"

_lock = Lock()

# ── Cost table (USD per 1M tokens) ──────────────────────────────
# Updated pricing as of 2026-03. Extend as needed.
COST_PER_1M: dict[str, tuple[float, float]] = {
    # (prompt_cost, completion_cost) per 1M tokens
    # Anthropic
    "claude-opus-4-20250514":      (15.0,  75.0),
    "claude-sonnet-4-20250514":    (3.0,   15.0),
    "claude-haiku-4-5-20251001":   (0.8,   4.0),
    "claude-3-5-haiku-20241022":   (0.8,   4.0),  # Legacy alias
    # OpenAI
    "gpt-4o":                      (2.5,   10.0),
    "gpt-4o-mini":                 (0.15,  0.6),
    "gpt-4.1":                     (2.0,   8.0),
    "gpt-4.1-mini":                (0.4,   1.6),
    "gpt-4.1-nano":                (0.1,   0.4),
    "o3-mini":                     (1.1,   4.4),
    # Google
    "gemini-2.0-flash":            (0.075, 0.3),
    "gemini-2.5-flash":            (0.15,  0.6),
    "gemini-2.5-pro":              (1.25,  10.0),
    "gemini-2.5-flash-lite":       (0.075, 0.3),
    # OpenRouter — same models, different names
    "anthropic/claude-sonnet-4":   (3.0,   15.0),
    "anthropic/claude-opus-4":     (15.0,  75.0),
    "openai/gpt-4o":               (2.5,   10.0),
    "openai/gpt-4.1":              (2.0,   8.0),
    "google/gemini-2.0-flash":     (0.075, 0.3),
    "google/gemini-2.5-flash":     (0.15,  0.6),
}


def _empty_usage() -> dict:
    return {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cost_usd": 0.0,
        "daily": {},
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _load_usage() -> dict:
    if not USAGE_FILE.exists():
        return _empty_usage()
    try:
        raw = USAGE_FILE.read_bytes()
        for enc in ("utf-8-sig", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return _empty_usage()
        data = json.loads(text)
        # ensure all keys exist
        for key in ("total_prompt_tokens", "total_completion_tokens", "total_cost_usd", "daily"):
            if key not in data:
                data[key] = _empty_usage()[key]
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_usage()


def _save_usage(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _estimate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """Estimate cost in USD based on model pricing."""
    rates = COST_PER_1M.get(model)
    if not rates:
        # Try partial match (e.g. "claude-sonnet-4" matches "claude-sonnet-4-20250514")
        for key, val in COST_PER_1M.items():
            if key in model or model in key:
                rates = val
                break
    if not rates:
        return 0.0
    prompt_cost = (prompt_tokens / 1_000_000) * rates[0]
    completion_cost = (completion_tokens / 1_000_000) * rates[1]
    return round(prompt_cost + completion_cost, 6)


def track_usage(prompt_tokens: int, completion_tokens: int, model: str = ""):
    """Record token usage from an API call. Thread-safe."""
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return

    with _lock:
        try:
            data = _load_usage()
            cost = _estimate_cost(prompt_tokens, completion_tokens, model)
            today = datetime.now().strftime("%Y-%m-%d")

            data["total_prompt_tokens"] += prompt_tokens
            data["total_completion_tokens"] += completion_tokens
            data["total_cost_usd"] = round(data["total_cost_usd"] + cost, 6)

            if today not in data["daily"]:
                data["daily"][today] = {"prompt": 0, "completion": 0, "calls": 0, "cost_usd": 0.0}
            data["daily"][today]["prompt"] += prompt_tokens
            data["daily"][today]["completion"] += completion_tokens
            data["daily"][today]["calls"] += 1
            data["daily"][today]["cost_usd"] = round(
                data["daily"][today].get("cost_usd", 0.0) + cost, 6
            )

            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            _save_usage(data)

            log.debug(
                f"Token usage: +{prompt_tokens}p/{completion_tokens}c "
                f"(${cost:.4f}) model={model}"
            )
        except Exception as e:
            log.warning(f"Failed to track token usage: {e}")


def get_usage_summary() -> dict:
    """Get full usage data for display."""
    with _lock:
        return _load_usage()


def get_today_usage() -> dict:
    """Get today's usage stats."""
    data = get_usage_summary()
    today = datetime.now().strftime("%Y-%m-%d")
    return data["daily"].get(today, {"prompt": 0, "completion": 0, "calls": 0, "cost_usd": 0.0})
