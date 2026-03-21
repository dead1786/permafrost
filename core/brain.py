"""
Permafrost Brain — Persistent AI session engine.

The brain maintains a long-running AI session via stream-JSON protocol,
routing messages from multiple channels through a single persistent context.

Architecture:
  Channels (TG/Web/etc) -> inbox queues (JSON files) -> Brain picks up ->
  AI processes -> Brain routes reply back to source channel

Key features:
  - Zero cold-start: session persists across messages
  - Multi-channel routing: one brain handles all channels
  - Wake signal: channels write trigger file to wake brain from idle
  - Heartbeat: periodic health check file for watchdog monitoring
  - Multi-model: pluggable AI providers via core.providers
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .providers import create_provider, BaseProvider

log = logging.getLogger("permafrost.brain")

# ── Configuration ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "ai_provider": "claude",        # claude | openai | gemini | ollama | openrouter
    "ai_model": "",                 # model ID (provider-specific)
    "api_key": "",                  # API key or endpoint
    "ai_timeout": 120,              # seconds per AI call
    "ai_max_retries": 2,            # retry count on failure
    "poll_interval": 1.0,           # seconds between inbox checks
    "idle_interval": 5.0,           # seconds when no activity
    "heartbeat_interval": 60,       # seconds between heartbeat writes
    "max_context_pct": 70,          # trigger compaction at this %
    "data_dir": "",                 # base directory for all data files
    "system_prompt": "",            # optional system prompt for AI
}


class PFBrain:
    """Persistent AI brain with multi-channel message routing."""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.data_dir = Path(self.config["data_dir"] or os.path.expanduser("~/.permafrost"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.pid_file = self.data_dir / "brain.pid"
        self.heartbeat_file = self.data_dir / "brain-heartbeat.json"
        self.state_file = self.data_dir / "brain-state.json"
        self.wake_trigger = self.data_dir / "brain-wake.trigger"
        self.message_log = self.data_dir / "message-log.json"

        # Channel inboxes (registered dynamically)
        self.channel_inboxes = {}   # name -> Path
        self.channel_handlers = {}  # name -> callback(reply, original_msg)

        # AI provider (lazy init)
        self._provider: BaseProvider | None = None

        # Conversation history for context
        self._conversation: list[dict] = []
        self._max_history = 50  # messages to keep in memory
        self._conversation_file = self.data_dir / "brain-conversation.json"

        # State
        self.running = False
        self.loop_count = 0
        self.last_heartbeat = 0

        # Restore conversation from disk
        self._load_conversation()

    def _load_conversation(self):
        """Restore conversation history from disk."""
        if self._conversation_file.exists():
            try:
                with open(self._conversation_file, "r", encoding="utf-8") as f:
                    self._conversation = json.load(f)
                log.info(f"restored {len(self._conversation)} conversation entries")
            except (json.JSONDecodeError, OSError):
                self._conversation = []

    def _save_conversation(self):
        """Persist conversation history to disk."""
        try:
            with open(self._conversation_file, "w", encoding="utf-8") as f:
                json.dump(self._conversation, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error(f"conversation save failed: {e}")

    def _setup_signal_handlers(self):
        """Register graceful shutdown handlers."""
        def handler(signum, frame):
            log.info(f"received signal {signum}, shutting down...")
            self.running = False

        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGHUP, handler)
        signal.signal(signal.SIGINT, handler)

    def _load_config(self, path: str = None) -> dict:
        """Load config from file, falling back to defaults."""
        config = DEFAULT_CONFIG.copy()
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config.update(user_config)
        return config

    @property
    def provider(self) -> BaseProvider:
        """Lazy-init AI provider from config."""
        if self._provider is None:
            self._provider = create_provider(
                self.config["ai_provider"],
                api_key=self.config["api_key"],
                model=self.config.get("ai_model", ""),
                timeout=self.config.get("ai_timeout", 120),
                max_retries=self.config.get("ai_max_retries", 2),
            )
            ok, err = self._provider.validate()
            if not ok:
                log.warning(f"provider validation: {err}")
        return self._provider

    def register_channel(self, name: str, inbox_path: str, handler=None):
        """Register a channel's inbox file and optional reply handler."""
        self.channel_inboxes[name] = Path(inbox_path)
        if handler:
            self.channel_handlers[name] = handler
        log.info(f"registered channel: {name} -> {inbox_path}")

    def _write_heartbeat(self):
        """Write heartbeat file for watchdog monitoring."""
        now = time.time()
        if now - self.last_heartbeat < self.config["heartbeat_interval"]:
            return
        self.last_heartbeat = now
        hb = {
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
            "loop_count": self.loop_count,
            "channels": list(self.channel_inboxes.keys()),
            "provider": self.config["ai_provider"],
        }
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                json.dump(hb, f, indent=2)
        except OSError as e:
            log.error(f"heartbeat write failed: {e}")

    def _write_pid(self):
        """Write PID file."""
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _check_inboxes(self) -> list:
        """Check all channel inboxes for new messages. Returns list of (channel, unread, all_msgs)."""
        results = []
        for name, inbox_path in self.channel_inboxes.items():
            if not inbox_path.exists():
                continue
            try:
                with open(inbox_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                if not messages:
                    continue
                unread = [m for m in messages if not m.get("read", False)]
                if unread:
                    results.append((name, unread, messages))
            except json.JSONDecodeError:
                log.warning(f"malformed inbox: {inbox_path}")
            except OSError:
                pass
        return results

    def _mark_read(self, inbox_path: Path, messages: list):
        """Clear inbox after processing to prevent re-reading stale messages."""
        try:
            with open(inbox_path, "w", encoding="utf-8") as f:
                json.dump([], f)
        except OSError as e:
            log.error(f"inbox clear failed: {e}")

    def _build_messages(self, channel: str, text: str) -> list[dict]:
        """Build message list with system prompt and conversation history."""
        msgs = []

        # System prompt
        sys_prompt = self.config.get("system_prompt", "")
        if sys_prompt:
            msgs.append({"role": "system", "content": sys_prompt})

        # Conversation history
        msgs.extend(self._conversation)

        # New user message with source tag
        source_tag = f"[source:{channel}]"
        msgs.append({"role": "user", "content": f"{source_tag} {text}"})

        return msgs

    def _process_message(self, channel: str, message: dict) -> str:
        """Send message to AI provider and get response."""
        text = message.get("text", message.get("message", ""))
        msgs = self._build_messages(channel, text)

        try:
            response = self.provider.chat(msgs)
        except Exception as e:
            log.error(f"AI provider error: {e}")
            response = f"[error] AI provider failed: {e}"

        # Update conversation history
        self._conversation.append({"role": "user", "content": text})
        self._conversation.append({"role": "assistant", "content": response})
        # Trim history
        if len(self._conversation) > self._max_history * 2:
            self._conversation = self._conversation[-(self._max_history * 2):]
        # Persist to disk
        self._save_conversation()

        return response

    def _log_message(self, channel: str, direction: str, text: str):
        """Log message to unified message log."""
        try:
            log_data = []
            if self.message_log.exists():
                with open(self.message_log, "r", encoding="utf-8") as f:
                    log_data = json.load(f)
            log_data.append({
                "channel": channel,
                "direction": direction,
                "text": text[:500],
                "timestamp": datetime.now().isoformat(),
            })
            log_data = log_data[-500:]
            with open(self.message_log, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _check_wake(self) -> bool:
        """Check if wake trigger file exists (written by channels)."""
        if self.wake_trigger.exists():
            try:
                self.wake_trigger.unlink()
            except Exception:
                pass
            return True
        return False

    def _check_duplicate(self):
        """Prevent duplicate brain processes."""
        if self.pid_file.exists():
            try:
                old_pid = json.loads(self.pid_file.read_text(encoding="utf-8")).get("pid", 0)
                if old_pid and old_pid != os.getpid():
                    import psutil
                    try:
                        p = psutil.Process(old_pid)
                        if p.is_running() and "main.py" in " ".join(p.cmdline()):
                            log.warning(f"Brain already running (PID {old_pid}), aborting duplicate")
                            return False
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
        return True

    def run(self):
        """Main brain loop."""
        if not self._check_duplicate():
            return

        self.running = True
        self._write_pid()
        self._setup_signal_handlers()

        # Write heartbeat immediately so watchdog doesn't restart us
        self.last_heartbeat = 0
        self._write_heartbeat()

        log.info(f"Permafrost Brain started (PID {os.getpid()})")
        log.info(f"Provider: {self.config['ai_provider']} / Model: {self.provider.model}")
        log.info(f"Data dir: {self.data_dir}")
        log.info(f"Channels: {list(self.channel_inboxes.keys())}")

        try:
            while self.running:
                self._write_heartbeat()
                self.loop_count += 1

                inbox_results = self._check_inboxes()

                if inbox_results:
                    for channel, unread, all_msgs in inbox_results:
                        for msg in unread:
                            text = msg.get("text", msg.get("message", ""))
                            log.info(f"[{channel}] received: {text[:80]}")
                            self._log_message(channel, "in", text)

                            response = self._process_message(channel, msg)
                            log.info(f"[{channel}] reply: {response[:80]}")
                            self._log_message(channel, "out", response)

                            # Route reply to source channel
                            if channel in self.channel_handlers:
                                try:
                                    self.channel_handlers[channel](response, msg)
                                except Exception as e:
                                    log.error(f"handler error [{channel}]: {e}")

                        self._mark_read(self.channel_inboxes[channel], all_msgs)

                    time.sleep(self.config["poll_interval"])
                else:
                    if self._check_wake():
                        time.sleep(self.config["poll_interval"])
                    else:
                        time.sleep(self.config["idle_interval"])

        except KeyboardInterrupt:
            log.info("shutting down...")
        finally:
            self.running = False
            self._save_conversation()
            if self.pid_file.exists():
                self.pid_file.unlink()
            log.info("stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    brain = PFBrain()
    brain.run()
