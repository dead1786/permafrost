"""
Permafrost Context Compactor — AI-powered conversation compression.

Instead of blindly truncating old messages, uses AI to create a summary
of older conversation history, preserving key context while reducing token count.

Flow:
  1. Brain detects conversation is getting long (> threshold)
  2. Compactor takes the oldest N messages
  3. AI summarizes them into a compact context block
  4. Old messages replaced with single summary message
  5. Brain continues with full context but fewer tokens
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.compactor")

# Approximate tokens per character (rough estimate across models)
CHARS_PER_TOKEN = 3.5

COMPACT_PROMPT = """You are a context compactor. Summarize the following conversation history into a concise context block that preserves:

1. Key decisions and their reasons
2. User preferences and corrections
3. Important facts and state changes
4. Ongoing tasks and their status
5. Any errors or issues encountered

Be concise but preserve ALL important information. Write in the same language as the conversation.
Do NOT add commentary — just output the summary.

Format:
## Context Summary (compacted)
- [key point 1]
- [key point 2]
...

Conversation to summarize:
"""


class PFCompactor:
    """AI-powered conversation compaction to prevent context overflow."""

    def __init__(self, data_dir: str = None, config: dict = None):
        self.data_dir = Path(data_dir) if data_dir else None
        config = config or {}
        # Trigger compaction when conversation exceeds this many messages
        self.message_threshold = config.get("compact_message_threshold", 30)
        # Keep this many recent messages untouched during compaction
        self.keep_recent = config.get("compact_keep_recent", 10)
        # Max tokens to estimate before triggering (0 = use message count only)
        self.token_threshold = config.get("compact_token_threshold", 0)
        # Cooldown between compactions (seconds)
        self.cooldown = config.get("compact_cooldown", 300)
        self._last_compact = 0
        # Compaction history
        self.history_file = Path(data_dir) / "compact-history.json" if data_dir else None

    def estimate_tokens(self, messages: list[dict]) -> int:
        """Rough token estimate for a message list."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return int(total_chars / CHARS_PER_TOKEN)

    def should_compact(self, conversation: list[dict]) -> bool:
        """Check if compaction should be triggered."""
        import time
        if time.time() - self._last_compact < self.cooldown:
            return False

        msg_count = len(conversation)
        if msg_count > self.message_threshold:
            return True

        if self.token_threshold > 0:
            estimated = self.estimate_tokens(conversation)
            if estimated > self.token_threshold:
                return True

        return False

    def compact(self, conversation: list[dict], provider) -> list[dict]:
        """Compact conversation by summarizing old messages.

        Args:
            conversation: Full conversation history
            provider: AI provider instance for summarization

        Returns:
            Compacted conversation list
        """
        import time

        if len(conversation) <= self.keep_recent + 2:
            return conversation  # Too short to compact

        # Split: old messages to summarize, recent to keep
        split_point = len(conversation) - self.keep_recent
        old_messages = conversation[:split_point]
        recent_messages = conversation[split_point:]

        old_token_est = self.estimate_tokens(old_messages)
        log.info(f"Compacting {len(old_messages)} old messages "
                 f"(~{old_token_est} tokens), keeping {len(recent_messages)} recent")

        # Build text representation of old messages for summarization
        conv_text = self._messages_to_text(old_messages)

        # Ask AI to summarize
        try:
            summary = provider.chat([
                {"role": "system", "content": "You are a context compactor. Be concise but preserve all important information."},
                {"role": "user", "content": COMPACT_PROMPT + conv_text},
            ])
        except Exception as e:
            log.error(f"Compaction AI call failed: {e}. Falling back to truncation.")
            # Fallback: just keep recent messages
            return recent_messages

        summary_token_est = self.estimate_tokens([{"content": summary}])
        log.info(f"Compacted: {old_token_est} tokens -> {summary_token_est} tokens "
                 f"({(1 - summary_token_est / max(old_token_est, 1)) * 100:.0f}% reduction)")

        # Build compacted conversation
        compacted = [
            {"role": "system", "content": f"[Context from previous conversation — auto-compacted at {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n\n{summary}"}
        ] + recent_messages

        self._last_compact = time.time()

        # Log compaction event
        self._log_compaction(len(old_messages), len(recent_messages),
                            old_token_est, summary_token_est)

        return compacted

    def get_context_level(self, conversation: list[dict], max_history: int = 50) -> float:
        """Calculate context usage as a percentage.

        Based on message count relative to max_history,
        weighted by estimated token count.
        """
        if max_history <= 0:
            return 0.0
        msg_pct = (len(conversation) / (max_history * 2)) * 100
        return min(msg_pct, 100.0)

    def _messages_to_text(self, messages: list[dict]) -> str:
        """Convert messages to readable text for summarization."""
        lines = []
        for m in messages:
            role = m.get("role", "unknown").upper()
            content = m.get("content", "")
            # Truncate very long individual messages
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    def _log_compaction(self, old_count: int, kept_count: int,
                       old_tokens: int, summary_tokens: int):
        """Log compaction event to history file."""
        if not self.history_file:
            return
        try:
            history = []
            if self.history_file.exists():
                history = json.loads(self.history_file.read_text(encoding="utf-8"))
            history.append({
                "timestamp": datetime.now().isoformat(),
                "messages_compacted": old_count,
                "messages_kept": kept_count,
                "tokens_before": old_tokens,
                "tokens_after": summary_tokens,
                "reduction_pct": round((1 - summary_tokens / max(old_tokens, 1)) * 100, 1),
            })
            # Keep last 50 entries
            history = history[-50:]
            self.history_file.write_text(
                json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log.debug(f"compaction log failed: {e}")
