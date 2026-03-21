"""
Permafrost Web Channel — Built-in web chat via inbox/outbox files.

The Web channel doesn't poll an external API. Instead:
  - Console app writes to web-inbox.json
  - Brain reads inbox, processes, writes reply to web-outbox.json
  - Console app polls outbox and displays reply

This channel is always available and requires no configuration.
"""

import json
import logging
from datetime import datetime

from .base import BaseChannel, register_channel

log = logging.getLogger("permafrost.channels.web")


@register_channel("web")
class PFWeb(BaseChannel):
    """Built-in web chat channel (no external API needed)."""

    LABEL = "Web Chat"
    CONFIG_FIELDS = []  # No configuration needed

    @property
    def name(self) -> str:
        return "web"

    def validate(self) -> tuple[bool, str]:
        return True, ""  # Always valid

    def send_message(self, text: str, **kwargs) -> bool:
        """Write reply to web-outbox.json for console to pick up."""
        outbox_file = self.data_dir / "web-outbox.json"
        try:
            outbox = []
            if outbox_file.exists():
                try:
                    outbox = json.loads(outbox_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    outbox = []

            outbox.append({
                "text": text,
                "timestamp": datetime.now().isoformat(),
                "read": False,
            })
            # Keep last 100
            outbox = outbox[-100:]
            outbox_file.write_text(
                json.dumps(outbox, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except OSError as e:
            log.error(f"outbox write failed: {e}")
            return False

    def reply_handler(self, response: str, original_msg: dict = None):
        """Write response to outbox and also update chat history."""
        self.send_message(response)

        # Also append to chat history for UI display
        history_file = self.data_dir / "chat-history.json"
        try:
            history = []
            if history_file.exists():
                try:
                    history = json.loads(history_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    history = []
            history.append({"role": "assistant", "content": response,
                           "timestamp": datetime.now().isoformat()})
            history = history[-200:]
            history_file.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def run(self):
        """Web channel doesn't need a polling loop — console handles UI."""
        log.info("web channel ready (no polling needed)")
