"""
Permafrost Notifier — Unified notification hub.

Routes notifications to the right channel based on:
  - User preference (TG / Discord / Web / Email)
  - Night silence rules
  - Urgency level

All notifications go through here, never directly to channels.
"""

import json
import os
from datetime import datetime
from pathlib import Path


class PFNotifier:
    """Unified notification routing with night silence support."""

    def __init__(self, config: dict = None, data_dir: str = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.config = config or {}
        self.channels = {}  # name -> send_func
        self.night_silence = None  # PFNightSilence instance

    def register_channel(self, name: str, send_func):
        """Register a channel's send function."""
        self.channels[name] = send_func

    def set_night_silence(self, silence):
        """Set night silence handler."""
        self.night_silence = silence

    def notify(self, text: str, urgent: bool = False, channel: str = None):
        """Send a notification through the appropriate channel.

        Args:
            text: Message content
            urgent: Bypass night silence
            channel: Force specific channel (None = use default)
        """
        # Night silence check
        if self.night_silence and not urgent:
            result = self.night_silence.send_or_queue(text, "notifier", urgent)
            if result == "queued":
                return "queued"

        # Determine channel
        if channel and channel in self.channels:
            self.channels[channel](text)
            return f"sent:{channel}"

        # Use default priority: telegram > discord > web
        for ch_name in ["telegram", "discord", "web"]:
            if ch_name in self.channels:
                self.channels[ch_name](text)
                return f"sent:{ch_name}"

        return "no_channel"

    def flush_queue(self):
        """Flush night silence queue through all channels."""
        if not self.night_silence:
            return []

        queued = self.night_silence.flush()
        if not queued:
            return []

        # Combine all queued messages
        combined = f"[Queued notifications ({len(queued)} items)]\n"
        for q in queued:
            combined += f"- [{q.get('source', '?')}] {q['text']}\n"

        # Send through default channel
        for ch_name in ["telegram", "discord", "web"]:
            if ch_name in self.channels:
                self.channels[ch_name](combined)
                break

        return queued
