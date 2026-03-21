"""
Permafrost Scheduler — Cron-like task engine with ack-based completion tracking.

Features:
  - Cron schedule (M H DoM Mon DoW) + one-shot datetime
  - Pending queue: tasks write to pending.json, brain picks up
  - Ack system: tasks marked .pending -> .ack when completed
  - Fail tracking: fail_count per task for watchdog alerting
  - Heartbeat: periodic health check for watchdog
  - Notify: push messages to channel inboxes (with night silence support)
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("permafrost.scheduler")


def _safe_read_json(path: Path, default=None):
    """Read JSON file safely. Returns default on any error."""
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


class PFScheduler:
    """Task scheduler with ack-based completion tracking."""

    def __init__(self, data_dir: str = None, config: dict = None):
        self.data_dir = Path(data_dir or os.path.expanduser("~/.permafrost"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.schedule_file = self.data_dir / "schedule.json"
        self.state_file = self.data_dir / "scheduler-state.json"
        self.pending_file = self.data_dir / "pending.json"
        self.heartbeat_file = self.data_dir / "scheduler-heartbeat.json"
        self.ack_dir = self.data_dir / "acks"
        self.ack_dir.mkdir(exist_ok=True)

        config = config or {}
        self.poll_interval = config.get("poll_interval", 30)
        self.max_fail_count = config.get("max_fail_count", 10)
        self.running = False

    def _load_schedule(self) -> list:
        """Load task schedule."""
        if not self.schedule_file.exists():
            return []
        try:
            with open(self.schedule_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get("tasks", [])
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"schedule load failed: {e}")
            return []

    def _load_state(self) -> dict:
        """Load task execution state."""
        if not self.state_file.exists():
            return {"tasks": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"tasks": {}}

    def _save_state(self, state: dict):
        """Save task execution state."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error(f"state save failed: {e}")

    def _write_heartbeat(self):
        """Write heartbeat for watchdog."""
        hb = {
            "pid": os.getpid(),
            "timestamp": datetime.now().isoformat(),
        }
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                json.dump(hb, f, indent=2)
        except OSError:
            pass

    def _cron_match(self, cron: str) -> bool:
        """Check if cron expression matches current time.

        Standard cron: M H DoM Mon DoW
        DoW: 0=Sunday, 1=Monday, ..., 6=Saturday (cron standard)
        Python weekday(): 0=Monday, ..., 6=Sunday
        """
        parts = cron.split()
        if len(parts) != 5:
            return False
        now = datetime.now()
        # Convert Python weekday (0=Mon) to cron weekday (0=Sun)
        cron_weekday = (now.weekday() + 1) % 7
        checks = [
            (parts[0], now.minute),
            (parts[1], now.hour),
            (parts[2], now.day),
            (parts[3], now.month),
            (parts[4], cron_weekday),
        ]
        for pattern, value in checks:
            if not self._cron_field_match(pattern, value):
                return False
        return True

    @staticmethod
    def _cron_field_match(pattern: str, value: int) -> bool:
        """Match a single cron field against a value."""
        if pattern == "*":
            return True
        # Handle step: */5, 0/10
        if "/" in pattern:
            base, step = pattern.split("/", 1)
            try:
                step = int(step)
                base = 0 if base == "*" else int(base)
                return step > 0 and (value - base) % step == 0
            except (ValueError, ZeroDivisionError):
                return False
        # Handle comma-separated: 1,3,5
        if "," in pattern:
            try:
                return value in [int(p) for p in pattern.split(",")]
            except ValueError:
                return False
        # Handle range: 1-5
        if "-" in pattern:
            try:
                lo, hi = pattern.split("-", 1)
                return int(lo) <= value <= int(hi)
            except ValueError:
                return False
        # Exact match
        try:
            return int(pattern) == value
        except ValueError:
            return False

    def _should_run(self, task: dict, state: dict) -> bool:
        """Determine if a task should run now."""
        task_id = task.get("id", "")
        if not task.get("enabled", True):
            return False

        schedule = task.get("schedule", {})
        stype = schedule.get("type", "")

        task_state = state.get("tasks", {}).get(task_id, {})
        last_run = task_state.get("last_run", "")

        if stype == "cron":
            cron = schedule.get("cron", "")
            if not self._cron_match(cron):
                return False
            # Don't run if already ran this minute
            if last_run:
                lr = datetime.fromisoformat(last_run)
                now = datetime.now()
                if lr.strftime("%Y-%m-%d %H:%M") == now.strftime("%Y-%m-%d %H:%M"):
                    return False
            return True

        elif stype == "once":
            dt_str = schedule.get("datetime", "")
            if not dt_str:
                return False
            target = datetime.fromisoformat(dt_str)
            now = datetime.now()
            if now >= target and not last_run:
                return True

        elif stype == "interval":
            minutes = schedule.get("minutes", 60)
            if last_run:
                lr = datetime.fromisoformat(last_run)
                elapsed = (datetime.now() - lr).total_seconds() / 60
                return elapsed >= minutes
            return True  # never run

        elif stype == "daily":
            time_str = schedule.get("time", "08:00")
            now = datetime.now()
            target_time = datetime.strptime(time_str, "%H:%M").time()
            if now.time() >= target_time:
                if last_run:
                    lr = datetime.fromisoformat(last_run)
                    if lr.date() == now.date():
                        return False
                return True

        return False

    def _enqueue(self, task: dict):
        """Write task to pending queue for brain to pick up."""
        pending = []
        if self.pending_file.exists():
            try:
                with open(self.pending_file, "r", encoding="utf-8") as f:
                    pending = json.load(f)
            except Exception:
                pending = []

        entry = {
            "task_id": task["id"],
            "command": task.get("command", ""),
            "description": task.get("description", ""),
            "queued_at": datetime.now().isoformat(),
        }
        pending.append(entry)

        with open(self.pending_file, "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)

        # Write .pending ack file
        ack_file = self.ack_dir / f"{task['id']}.pending"
        ack_file.write_text(datetime.now().isoformat())

        log.info(f"enqueued: {task['id']}")

    def _update_state(self, task_id: str, success: bool, state: dict):
        """Update task execution state."""
        if "tasks" not in state:
            state["tasks"] = {}
        if task_id not in state["tasks"]:
            state["tasks"][task_id] = {
                "last_run": "", "last_success": True,
                "run_count": 0, "fail_count": 0
            }
        ts = state["tasks"][task_id]
        ts["last_run"] = datetime.now().isoformat()
        ts["last_success"] = success
        ts["run_count"] = ts.get("run_count", 0) + 1
        if not success:
            ts["fail_count"] = ts.get("fail_count", 0) + 1

    def ack(self, task_id: str):
        """Mark a task as completed (called by brain after processing)."""
        pending_ack = self.ack_dir / f"{task_id}.pending"
        done_ack = self.ack_dir / f"{task_id}.ack"

        if pending_ack.exists():
            pending_ack.unlink()
        done_ack.write_text(datetime.now().isoformat())

        # Update state
        state = self._load_state()
        self._update_state(task_id, True, state)
        self._save_state(state)

    # ── Reminders ──

    def _load_reminders(self) -> list:
        """Load user-defined reminders from reminders.json."""
        reminder_file = self.data_dir / "reminders.json"
        reminders = _safe_read_json(reminder_file)
        if not isinstance(reminders, list):
            return []
        return [r for r in reminders if isinstance(r, dict) and r.get("enabled", True)]

    def _check_reminders(self, state: dict):
        """Check and fire user reminders that match current time."""
        reminders = self._load_reminders()
        now = datetime.now()
        now_hm = now.strftime("%H:%M")

        reminder_file = self.data_dir / "reminders.json"
        all_reminders = _safe_read_json(reminder_file)
        if not isinstance(all_reminders, list):
            all_reminders = []

        changed = False
        to_remove = []

        for rem in reminders:
            rid = rem.get("id", "")
            if rem.get("time") != now_hm:
                continue

            # Check if already fired this minute
            rem_state = state.get("tasks", {}).get(rid, {})
            last_run = rem_state.get("last_run", "")
            if last_run:
                lr = datetime.fromisoformat(last_run)
                if lr.strftime("%Y-%m-%d %H:%M") == now.strftime("%Y-%m-%d %H:%M"):
                    continue

            # Check day-of-week for weekly (fire only on same weekday as created)
            repeat = rem.get("repeat", "once")
            if repeat == "weekly":
                created = rem.get("created", "")
                if created:
                    created_dt = datetime.fromisoformat(created)
                    if now.weekday() != created_dt.weekday():
                        continue

            # Fire the reminder
            self.notify_user(rem.get("message", "Reminder"))
            self._update_state(rid, True, state)
            log.info(f"reminder fired: {rid}")

            # Remove one-shot reminders
            if repeat == "once":
                to_remove.append(rid)
                changed = True

        # Clean up fired one-shot reminders
        if changed:
            all_reminders = [r for r in all_reminders if r.get("id") not in to_remove]
            reminder_file.write_text(
                json.dumps(all_reminders, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    # ── Config helpers ──

    def _load_config(self) -> dict:
        """Load permafrost config for channel/night settings."""
        config_file = self.data_dir / "config.json"
        if not config_file.exists():
            return {}
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    # ── Night silence ──

    def _is_night(self) -> bool:
        """Check if current time falls within the night silence window."""
        config = self._load_config()
        now = datetime.now().strftime("%H:%M")
        start = config.get("night_start", "00:00")
        end = config.get("night_end", "08:00")
        if start < end:
            return start <= now < end
        else:  # crosses midnight, e.g. 23:00-07:00
            return now >= start or now < end

    # ── Notification system ──

    def notify_user(self, message: str, channel: str = "all"):
        """Send a message to user through enabled channels.

        During night silence hours, messages are queued instead
        and flushed when silence ends.
        """
        if self._is_night():
            self._queue_notification(message)
            log.info(f"notification queued (night silence): {message[:60]}")
            return

        config = self._load_config()
        channels_to_notify = []

        if channel == "all":
            for ch_name in ["web", "telegram", "discord", "line"]:
                if config.get(f"{ch_name}_enabled", ch_name == "web"):
                    channels_to_notify.append(ch_name)
        else:
            channels_to_notify = [channel]

        for ch in channels_to_notify:
            inbox_file = self.data_dir / f"{ch}-inbox.json"
            try:
                inbox = _safe_read_json(inbox_file)
                if not isinstance(inbox, list):
                    inbox = []
                inbox.append({
                    "text": message,
                    "source": "scheduler",
                    "timestamp": datetime.now().isoformat(),
                    "read": False,
                })
                inbox_file.write_text(
                    json.dumps(inbox, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                log.error(f"notify failed for {ch}: {e}")

        log.info(f"notified {channels_to_notify}: {message[:60]}")

    def _queue_notification(self, message: str):
        """Queue a notification for delivery after night silence ends."""
        queue_file = self.data_dir / "notify-queue.json"
        queue = _safe_read_json(queue_file)
        if not isinstance(queue, list):
            queue = []
        queue.append({
            "text": message,
            "timestamp": datetime.now().isoformat(),
        })
        queue_file.write_text(
            json.dumps(queue, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _flush_notification_queue(self):
        """Flush all queued night-silence notifications."""
        queue_file = self.data_dir / "notify-queue.json"
        queue = _safe_read_json(queue_file)
        if not isinstance(queue, list) or not queue:
            return
        log.info(f"flushing {len(queue)} queued notification(s)")
        for item in queue:
            # Send directly (bypass night check since we're flushing)
            config = self._load_config()
            channels_to_notify = []
            for ch_name in ["web", "telegram", "discord", "line"]:
                if config.get(f"{ch_name}_enabled", ch_name == "web"):
                    channels_to_notify.append(ch_name)
            for ch in channels_to_notify:
                inbox_file = self.data_dir / f"{ch}-inbox.json"
                try:
                    inbox = _safe_read_json(inbox_file)
                    if not isinstance(inbox, list):
                        inbox = []
                    inbox.append({
                        "text": item["text"],
                        "source": "scheduler",
                        "timestamp": datetime.now().isoformat(),
                        "read": False,
                    })
                    inbox_file.write_text(
                        json.dumps(inbox, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception as e:
                    log.error(f"flush notify failed for {ch}: {e}")
        # Clear queue
        queue_file.write_text("[]", encoding="utf-8")

    def run(self):
        """Main scheduler loop."""
        self.running = True
        log.info(f"started (PID {os.getpid()})")
        was_night = self._is_night()

        try:
            while self.running:
                self._write_heartbeat()

                # Detect night->day transition and flush queued notifications
                is_night_now = self._is_night()
                if was_night and not is_night_now:
                    self._flush_notification_queue()
                was_night = is_night_now

                schedule = self._load_schedule()
                state = self._load_state()

                for task in schedule:
                    # Skip tasks that have exceeded max fail count
                    task_state = state.get("tasks", {}).get(task.get("id", ""), {})
                    if task_state.get("fail_count", 0) >= self.max_fail_count:
                        continue
                    if self._should_run(task, state):
                        self._enqueue(task)
                        self._update_state(task["id"], True, state)
                        # Notify user about the triggered task
                        desc = task.get("description", task.get("id", "task"))
                        self.notify_user(f"[Scheduler] {desc}")

                # Check user-defined reminders
                self._check_reminders(state)

                self._save_state(state)
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            log.info("shutting down...")
        finally:
            self.running = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    scheduler = PFScheduler()
    scheduler.run()
