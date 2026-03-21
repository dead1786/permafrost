"""
Permafrost Watchdog — Auto-healing daemon monitor.

Philosophy: Fix first, report second.

Checks:
  1. Brain process alive + heartbeat fresh
  2. Scheduler process alive + heartbeat fresh
  3. All registered channel daemons alive
  4. Task fail counts within threshold

If something is wrong, tries to restart it automatically.
Only alerts user if auto-fix fails.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.watchdog")


class PFWatchdog:
    """Self-healing watchdog that monitors and restarts Permafrost services."""

    def __init__(self, data_dir: str = None, config: dict = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.log_file = self.data_dir / "watchdog.log"

        config = config or {}
        self.check_interval = config.get("check_interval", 300)
        self.heartbeat_max_age = config.get("heartbeat_max_age", 180)
        self.max_fail_count = config.get("max_fail_count", 3)
        self.max_restart_count = config.get("max_restart_count", 5)
        self.restart_cooldown = config.get("restart_cooldown", 60)

        # Services to monitor: name -> {heartbeat_file, restart_cmd}
        self.services = {}
        # Track restart counts and timestamps
        self._restart_history: dict[str, list[float]] = {}

    def register_service(self, name: str, heartbeat_file: str, restart_cmd: list):
        """Register a service to monitor."""
        self.services[name] = {
            "heartbeat_file": Path(heartbeat_file),
            "restart_cmd": restart_cmd,
        }

    def _log(self, msg: str):
        """Write to watchdog log and Python logger."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        log.info(msg)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            # Rotate log if too large (> 1MB)
            if self.log_file.stat().st_size > 1_000_000:
                rotated = self.log_file.with_suffix(".log.1")
                if rotated.exists():
                    rotated.unlink()
                self.log_file.rename(rotated)
        except OSError:
            pass

    def _check_heartbeat(self, hb_file: Path) -> tuple[bool, float, int]:
        """Check if heartbeat file is fresh. Returns (ok, age_seconds, pid)."""
        if not hb_file.exists():
            return False, -1, -1
        try:
            with open(hb_file, "r", encoding="utf-8") as f:
                hb = json.load(f)
            ts = datetime.fromisoformat(hb["timestamp"])
            age = (datetime.now() - ts).total_seconds()
            pid = hb.get("pid", -1)
            return age < self.heartbeat_max_age, age, pid
        except (json.JSONDecodeError, KeyError, OSError):
            return False, -1, -1

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process is running."""
        if pid <= 0:
            return False
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, timeout=10
                )
                return str(pid) in r.stdout
            else:
                os.kill(pid, 0)
                return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _can_restart(self, name: str) -> bool:
        """Check if service hasn't exceeded restart limit."""
        now = time.time()
        history = self._restart_history.get(name, [])
        # Only count recent restarts (last hour)
        recent = [t for t in history if now - t < 3600]
        self._restart_history[name] = recent
        if len(recent) >= self.max_restart_count:
            return False
        # Cooldown check
        if recent and now - recent[-1] < self.restart_cooldown:
            return False
        return True

    def _restart_service(self, name: str, cmd: list) -> bool:
        """Try to restart a service. Returns True if successful."""
        if not self._can_restart(name):
            self._log(f"{name}: restart skipped (limit {self.max_restart_count}/hr or cooldown)")
            return False

        self._log(f"restarting {name}: {' '.join(cmd)}")
        try:
            if sys.platform == "win32":
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(cmd, start_new_session=True)
            self._restart_history.setdefault(name, []).append(time.time())
            time.sleep(3)
            return True
        except Exception as e:
            self._log(f"restart failed for {name}: {e}")
            return False

    def check_all(self) -> list[str]:
        """Run all checks. Returns list of issues."""
        issues = []

        for name, svc in self.services.items():
            hb_ok, age, pid = self._check_heartbeat(svc["heartbeat_file"])

            if not hb_ok:
                if age < 0:
                    issues.append(f"{name}: no heartbeat file")
                else:
                    issues.append(f"{name}: heartbeat stale ({age:.0f}s)")

                if self._restart_service(name, svc["restart_cmd"]):
                    self._log(f"{name}: auto-restarted")
                else:
                    issues.append(f"{name}: auto-restart FAILED")

            elif pid > 0 and not self._is_process_alive(pid):
                issues.append(f"{name}: PID {pid} not alive")
                if self._restart_service(name, svc["restart_cmd"]):
                    self._log(f"{name}: auto-restarted (dead PID)")
                else:
                    issues.append(f"{name}: auto-restart FAILED")

        # Check scheduler fail counts
        state_file = self.data_dir / "scheduler-state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                for task_id, info in state.get("tasks", {}).items():
                    fc = info.get("fail_count", 0)
                    if fc >= self.max_fail_count:
                        issues.append(f"task {task_id}: fail_count={fc}")
            except (json.JSONDecodeError, OSError):
                pass

        return issues

    def run_once(self) -> list[str]:
        """Run a single check cycle."""
        self._log("watchdog check started")
        issues = self.check_all()
        if issues:
            self._log(f"issues found: {issues}")
        else:
            self._log("all OK")
        return issues

    def run(self):
        """Continuous watchdog loop."""
        self._log(f"watchdog started (PID {os.getpid()})")
        try:
            while True:
                self.run_once()
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self._log("watchdog stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    wd = PFWatchdog()
    wd.run()
