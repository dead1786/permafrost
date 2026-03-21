"""
Permafrost Context Guard — Auto-backup and compaction when context fills up.

Monitors AI context usage and triggers:
  1. Memory checkpoint (save important context to files)
  2. Context compaction (summarize and compress)

Prevents context overflow that would cause session loss.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.guard")


class PFContextGuard:
    """Monitors context usage and triggers auto-backup/compaction."""

    def __init__(self, data_dir: str = None, config: dict = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.state_file = self.data_dir / "guard-state.json"
        self.context_file = self.data_dir / "context-level.json"

        config = config or {}
        self.threshold_pct = config.get("threshold_pct", 70)
        self.emergency_pct = config.get("emergency_pct", 90)
        self.cooldown_seconds = config.get("cooldown_seconds", 600)
        self.check_interval = config.get("check_interval", 60)

        self.last_trigger = 0

    def _get_context_level(self) -> float:
        """Read current context usage percentage.

        Reads from context-level.json which is written by PFBrain
        every time conversation changes.
        """
        if not self.context_file.exists():
            return 0.0
        try:
            with open(self.context_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return float(data.get("percent", data.get("percentage", data.get("level", 0.0))))
        except (json.JSONDecodeError, OSError, ValueError):
            return 0.0

    def _should_trigger(self) -> str | None:
        """Check if compaction should be triggered.

        Returns: "emergency", "normal", or None.
        """
        level = self._get_context_level()
        if level >= self.emergency_pct:
            return "emergency"
        if level >= self.threshold_pct:
            if time.time() - self.last_trigger < self.cooldown_seconds:
                return None
            return "normal"
        return None

    def _trigger_checkpoint(self, urgency: str):
        """Trigger memory checkpoint + compaction."""
        self.last_trigger = time.time()
        level = self._get_context_level()

        actions = ["checkpoint", "compact"]
        if urgency == "emergency":
            actions.append("emergency_compact")

        trigger = self.data_dir / "checkpoint-trigger.json"
        try:
            with open(trigger, "w", encoding="utf-8") as f:
                json.dump({
                    "triggered_at": datetime.now().isoformat(),
                    "context_level": level,
                    "urgency": urgency,
                    "actions": actions,
                }, f, indent=2)
        except OSError as e:
            log.error(f"trigger write failed: {e}")
            return

        # Update state
        state = {
            "last_trigger": datetime.now().isoformat(),
            "context_at_trigger": level,
            "urgency": urgency,
            "trigger_count": self._load_trigger_count() + 1,
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass

        log.info(f"checkpoint triggered [{urgency}] at {level:.1f}%")

    def _load_trigger_count(self) -> int:
        """Load historical trigger count."""
        if not self.state_file.exists():
            return 0
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f).get("trigger_count", 0)
        except (json.JSONDecodeError, OSError):
            return 0

    def check(self) -> str | None:
        """Run a single check. Returns urgency level if triggered, None otherwise."""
        urgency = self._should_trigger()
        if urgency:
            self._trigger_checkpoint(urgency)
        return urgency

    def run(self):
        """Continuous monitoring loop."""
        log.info(f"started (threshold={self.threshold_pct}%, emergency={self.emergency_pct}%)")
        try:
            while True:
                self.check()
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            log.info("stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    guard = PFContextGuard()
    guard.run()
