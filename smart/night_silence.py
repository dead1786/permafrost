"""
Permafrost Night Silence — Queue non-urgent notifications during sleep hours.

Instead of waking the user at 3 AM for a non-critical alert,
queue it and flush everything at the configured wake time.
Urgent alerts (system crash, security) bypass silence.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFNightSilence:
    """Smart notification queuing during sleep hours."""

    def __init__(self, data_dir: str = None, config: dict = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.queue_file = self.data_dir / "notify-queue.json"
        config = config or {}
        self.silence_start = config.get("night_start", "00:00")
        self.silence_end = config.get("night_end", "08:00")
        self.flush_time = config.get("flush_time", "08:05")

    def _parse_time(self, t: str) -> tuple:
        parts = t.split(":")
        return int(parts[0]), int(parts[1])

    def is_silent(self) -> bool:
        """Check if current time is within silence window."""
        now = datetime.now()
        cur = (now.hour, now.minute)
        start = self._parse_time(self.silence_start)
        end = self._parse_time(self.silence_end)

        if start > end:  # wraps midnight
            return cur >= start or cur < end
        return start <= cur < end

    def should_flush(self) -> bool:
        """Check if it's time to flush the queue."""
        now = datetime.now()
        flush = self._parse_time(self.flush_time)
        return (now.hour, now.minute) == flush

    def queue(self, text: str, source: str = "system"):
        """Queue a notification for later delivery."""
        q = self._load_queue()
        q.append({
            "text": text,
            "source": source,
            "queued_at": datetime.now().isoformat(),
        })
        self._save_queue(q)

    def flush(self) -> list:
        """Flush all queued notifications. Returns list of messages."""
        q = self._load_queue()
        self._save_queue([])  # clear
        return q

    def send_or_queue(self, text: str, source: str = "system", urgent: bool = False) -> str:
        """Smart send: queue during silence, send immediately otherwise.

        Returns "queued" or "send" to indicate what happened.
        """
        if urgent or not self.is_silent():
            return "send"
        self.queue(text, source)
        return "queued"

    def _load_queue(self) -> list:
        if self.queue_file.exists():
            try:
                return json.loads(self.queue_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_queue(self, q: list):
        self.queue_file.write_text(
            json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_queue_count(self) -> int:
        """Get number of queued notifications."""
        return len(self._load_queue())
