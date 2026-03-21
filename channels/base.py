"""
Permafrost Channel Base — Abstract base class for all communication channels.

Every channel plugin must:
  1. Implement receive logic (polling or webhook)
  2. Write incoming messages to its inbox file
  3. Implement send_message() for brain to route replies back
  4. Write wake trigger to notify brain of new messages
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.channels")

# ── Channel registry ───────────────────────────────────────────

_CHANNELS: dict[str, type] = {}


def register_channel(name: str):
    """Decorator to register a channel plugin class."""
    def decorator(cls):
        _CHANNELS[name] = cls
        return cls
    return decorator


def create_channel(name: str, config: dict, data_dir: str = None) -> "BaseChannel":
    """Factory: create a channel by name."""
    if name not in _CHANNELS:
        raise ValueError(f"Unknown channel '{name}'. Available: {list(_CHANNELS.keys())}")
    return _CHANNELS[name](config=config, data_dir=data_dir)


def list_channels() -> list[dict]:
    """List all registered channels with metadata."""
    return [
        {"name": name, "label": cls.LABEL, "config_fields": cls.CONFIG_FIELDS}
        for name, cls in _CHANNELS.items()
    ]


# ── Base class ─────────────────────────────────────────────────

class BaseChannel(ABC):
    """Abstract base class for communication channel plugins."""

    LABEL: str = "Unknown Channel"
    CONFIG_FIELDS: list[dict] = []  # [{name, label, type, help, required}]

    def __init__(self, config: dict, data_dir: str = None):
        self.config = config
        self.data_dir = Path(data_dir or "~/.permafrost").expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.running = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier (e.g. 'telegram', 'discord')."""
        ...

    @property
    def inbox_file(self) -> Path:
        """Path to this channel's inbox file."""
        return self.data_dir / f"{self.name}-inbox.json"

    @abstractmethod
    def send_message(self, text: str, **kwargs) -> bool:
        """Send a message through this channel. Returns success."""
        ...

    @abstractmethod
    def run(self):
        """Start the channel's receive loop (blocking)."""
        ...

    def stop(self):
        """Signal the channel to stop."""
        self.running = False

    def validate(self) -> tuple[bool, str]:
        """Validate channel configuration. Returns (ok, error_message)."""
        return True, ""

    # ── Shared helpers ─────────────────────────────────────────

    def write_to_inbox(self, text: str, metadata: dict = None):
        """Write an incoming message to the inbox file for brain pickup."""
        inbox = self._read_inbox()
        entry = {
            "text": text,
            "source": self.name,
            "timestamp": datetime.now().isoformat(),
            "read": False,
        }
        if metadata:
            entry.update(metadata)
        inbox.append(entry)

        try:
            self.inbox_file.write_text(
                json.dumps(inbox, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            log.error(f"[{self.name}] inbox write failed: {e}")
            return

        self._wake_brain()

    def reply_handler(self, response: str, original_msg: dict = None):
        """Default reply handler — sends response back through this channel."""
        self.send_message(response)

    def _read_inbox(self) -> list:
        """Read current inbox contents."""
        if not self.inbox_file.exists():
            return []
        try:
            return json.loads(self.inbox_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _wake_brain(self):
        """Write wake trigger file to notify brain of new message."""
        wake_file = self.data_dir / "brain-wake.trigger"
        try:
            wake_file.write_text(datetime.now().isoformat())
        except OSError:
            pass
