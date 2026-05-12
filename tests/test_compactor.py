"""Tests for core.compactor — context compaction logic."""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.compactor import PFCompactor, CHARS_PER_TOKEN


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_messages(n: int, role: str = "user", content_len: int = 100) -> list:
    """Generate a list of n fake messages."""
    return [{"role": role, "content": "x" * content_len} for _ in range(n)]


def make_compactor(data_dir=None, **kwargs):
    config = {"compact_message_threshold": 30, "compact_keep_recent": 10, **kwargs}
    return PFCompactor(data_dir=str(data_dir) if data_dir else None, config=config)


# ── estimate_tokens ───────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_list(self, tmp_path):
        c = make_compactor(tmp_path)
        assert c.estimate_tokens([]) == 0

    def test_single_message(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [{"role": "user", "content": "x" * 350}]
        expected = int(350 / CHARS_PER_TOKEN)
        assert c.estimate_tokens(msgs) == expected

    def test_multiple_messages_sum(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": "b" * 200},
        ]
        expected = int(300 / CHARS_PER_TOKEN)
        assert c.estimate_tokens(msgs) == expected

    def test_message_without_content_key(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [{"role": "user"}]  # no content key
        assert c.estimate_tokens(msgs) == 0


# ── should_compact ────────────────────────────────────────────────────────────

class TestShouldCompact:
    def test_short_conversation_no_compact(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=30)
        msgs = make_messages(5)
        assert c.should_compact(msgs) is False

    def test_over_threshold_triggers(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=10)
        msgs = make_messages(15)
        assert c.should_compact(msgs) is True

    def test_cooldown_prevents_recompact(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=5, compact_cooldown=300)
        c._last_compact = time.time()  # just compacted
        msgs = make_messages(20)
        assert c.should_compact(msgs) is False

    def test_token_threshold_triggers(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=1000,
                           compact_token_threshold=50)
        # Each message is 100 chars → ~28 tokens; 3 msgs → ~85 tokens > 50
        msgs = make_messages(3, content_len=100)
        assert c.should_compact(msgs) is True

    def test_token_threshold_zero_ignored(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=100,
                           compact_token_threshold=0)
        msgs = make_messages(5, content_len=10000)  # many tokens but count < threshold
        assert c.should_compact(msgs) is False

    def test_cooldown_expired_allows_compact(self, tmp_path):
        c = make_compactor(tmp_path, compact_message_threshold=5, compact_cooldown=1)
        c._last_compact = time.time() - 5  # expired
        msgs = make_messages(10)
        assert c.should_compact(msgs) is True


# ── compact ───────────────────────────────────────────────────────────────────

class TestCompact:
    def test_short_conversation_returned_unchanged(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=10)
        msgs = make_messages(5)
        result = c.compact(msgs, provider=MagicMock())
        assert result == msgs

    def test_ai_summary_replaces_old_messages(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=5)
        msgs = make_messages(20)
        provider = MagicMock()
        provider.chat.return_value = "## Context Summary\n- key point"

        result = c.compact(msgs, provider)
        # Should have 1 summary message + 5 recent messages = 6
        assert len(result) == 6
        assert result[0]["role"] == "system"
        assert "Context Summary" in result[0]["content"]

    def test_fallback_on_provider_error(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=5)
        msgs = make_messages(20)
        provider = MagicMock()
        provider.chat.side_effect = RuntimeError("API error")

        result = c.compact(msgs, provider)
        # Fallback: just keep recent 5
        assert len(result) == 5

    def test_compact_updates_last_compact_timestamp(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=5)
        msgs = make_messages(20)
        provider = MagicMock()
        provider.chat.return_value = "Summary"

        before = time.time()
        c.compact(msgs, provider)
        assert c._last_compact >= before

    def test_compact_logs_to_history(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=5)
        msgs = make_messages(20)
        provider = MagicMock()
        provider.chat.return_value = "Summary text"

        c.compact(msgs, provider)

        history_file = tmp_path / "compact-history.json"
        assert history_file.exists()
        history = json.loads(history_file.read_text(encoding="utf-8"))
        assert len(history) == 1
        assert history[0]["messages_compacted"] == 15
        assert history[0]["messages_kept"] == 5

    def test_compact_history_capped_at_50(self, tmp_path):
        c = make_compactor(tmp_path, compact_keep_recent=5)
        # Pre-fill history with 50 entries
        history_file = tmp_path / "compact-history.json"
        existing = [{"timestamp": "x", "messages_compacted": 1, "messages_kept": 1,
                     "tokens_before": 10, "tokens_after": 5, "reduction_pct": 50.0}
                    for _ in range(50)]
        history_file.write_text(json.dumps(existing), encoding="utf-8")

        # Reset cooldown
        c._last_compact = 0
        msgs = make_messages(20)
        provider = MagicMock()
        provider.chat.return_value = "Summary"
        c.compact(msgs, provider)

        history = json.loads(history_file.read_text(encoding="utf-8"))
        assert len(history) == 50  # still capped at 50


# ── get_context_level ─────────────────────────────────────────────────────────

class TestGetContextLevel:
    def test_empty_conversation(self, tmp_path):
        c = make_compactor(tmp_path)
        assert c.get_context_level([], max_history=50) == 0.0

    def test_zero_max_history_returns_zero(self, tmp_path):
        c = make_compactor(tmp_path)
        assert c.get_context_level(make_messages(10), max_history=0) == 0.0

    def test_level_capped_at_100(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = make_messages(1000)
        assert c.get_context_level(msgs, max_history=50) == 100.0

    def test_half_capacity(self, tmp_path):
        c = make_compactor(tmp_path)
        # 50 messages / (50*2) = 50%
        msgs = make_messages(50)
        level = c.get_context_level(msgs, max_history=50)
        assert level == 50.0


# ── _messages_to_text ─────────────────────────────────────────────────────────

class TestMessagesToText:
    def test_basic_conversion(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        text = c._messages_to_text(msgs)
        assert "[USER]: hello" in text
        assert "[ASSISTANT]: world" in text

    def test_long_content_truncated(self, tmp_path):
        c = make_compactor(tmp_path)
        long_content = "x" * 3000
        msgs = [{"role": "user", "content": long_content}]
        text = c._messages_to_text(msgs)
        assert "[truncated]" in text
        assert len(text) < 3000

    def test_missing_content_handled(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [{"role": "user"}]  # no content
        text = c._messages_to_text(msgs)
        assert "[USER]:" in text

    def test_messages_separated_by_double_newline(self, tmp_path):
        c = make_compactor(tmp_path)
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        text = c._messages_to_text(msgs)
        assert "\n\n" in text


# ── _log_compaction ───────────────────────────────────────────────────────────

class TestLogCompaction:
    def test_creates_history_file(self, tmp_path):
        c = make_compactor(tmp_path)
        c._log_compaction(10, 5, 200, 50)
        assert c.history_file.exists()

    def test_log_entry_contents(self, tmp_path):
        c = make_compactor(tmp_path)
        c._log_compaction(old_count=10, kept_count=5, old_tokens=200, summary_tokens=50)
        history = json.loads(c.history_file.read_text(encoding="utf-8"))
        entry = history[0]
        assert entry["messages_compacted"] == 10
        assert entry["messages_kept"] == 5
        assert entry["tokens_before"] == 200
        assert entry["tokens_after"] == 50
        assert entry["reduction_pct"] == 75.0

    def test_no_data_dir_does_not_raise(self):
        c = PFCompactor(data_dir=None)
        c._log_compaction(5, 3, 100, 30)  # should silently skip

    def test_appends_to_existing_log(self, tmp_path):
        c = make_compactor(tmp_path)
        c._log_compaction(5, 3, 100, 30)
        c._log_compaction(8, 4, 150, 40)
        history = json.loads(c.history_file.read_text(encoding="utf-8"))
        assert len(history) == 2
