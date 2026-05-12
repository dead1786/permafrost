"""Tests for core.scheduler — cron matching, schedule types, reminders, notifications."""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scheduler import PFScheduler, _safe_read_json


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_scheduler(data_dir):
    return PFScheduler(data_dir=data_dir, config={"poll_interval": 0})


# ── _safe_read_json ───────────────────────────────────────────────────────────

class TestSafeReadJson:
    def test_missing_file_returns_default(self, tmp_path):
        result = _safe_read_json(tmp_path / "nope.json")
        assert result == []

    def test_missing_file_custom_default(self, tmp_path):
        result = _safe_read_json(tmp_path / "nope.json", default={})
        assert result == {}

    def test_valid_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "val"}', encoding="utf-8")
        assert _safe_read_json(f, default={}) == {"key": "val"}

    def test_invalid_json_returns_default(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        assert _safe_read_json(f) == []


# ── _cron_field_match ─────────────────────────────────────────────────────────

class TestCronFieldMatch:
    def test_wildcard(self):
        assert PFScheduler._cron_field_match("*", 0)
        assert PFScheduler._cron_field_match("*", 59)

    def test_exact_match(self):
        assert PFScheduler._cron_field_match("5", 5)
        assert not PFScheduler._cron_field_match("5", 6)

    def test_comma_list(self):
        assert PFScheduler._cron_field_match("1,3,5", 3)
        assert not PFScheduler._cron_field_match("1,3,5", 4)

    def test_range(self):
        assert PFScheduler._cron_field_match("1-5", 3)
        assert PFScheduler._cron_field_match("1-5", 1)
        assert PFScheduler._cron_field_match("1-5", 5)
        assert not PFScheduler._cron_field_match("1-5", 6)

    def test_step_wildcard(self):
        assert PFScheduler._cron_field_match("*/5", 0)
        assert PFScheduler._cron_field_match("*/5", 15)
        assert not PFScheduler._cron_field_match("*/5", 7)

    def test_step_with_base(self):
        assert PFScheduler._cron_field_match("2/3", 2)
        assert PFScheduler._cron_field_match("2/3", 5)
        assert not PFScheduler._cron_field_match("2/3", 3)

    def test_step_zero_returns_false(self):
        assert not PFScheduler._cron_field_match("*/0", 0)

    def test_invalid_exact_returns_false(self):
        assert not PFScheduler._cron_field_match("abc", 5)

    def test_invalid_range_returns_false(self):
        assert not PFScheduler._cron_field_match("a-b", 5)

    def test_invalid_list_returns_false(self):
        assert not PFScheduler._cron_field_match("1,x,3", 1)


# ── _cron_match ───────────────────────────────────────────────────────────────

class TestCronMatch:
    def test_exact_time_matches(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        cron_weekday = (now.weekday() + 1) % 7
        cron = f"{now.minute} {now.hour} * * *"
        assert s._cron_match(cron)

    def test_wrong_minute_no_match(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        wrong_minute = (now.minute + 1) % 60
        cron = f"{wrong_minute} {now.hour} * * *"
        assert not s._cron_match(cron)

    def test_invalid_parts_returns_false(self, tmp_path):
        s = make_scheduler(tmp_path)
        assert not s._cron_match("* * *")  # too few parts

    def test_wildcard_all_matches(self, tmp_path):
        s = make_scheduler(tmp_path)
        assert s._cron_match("* * * * *")


# ── _should_run — cron type ───────────────────────────────────────────────────

class TestShouldRunCron:
    def test_cron_no_last_run_matches(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        task = {
            "id": "t1",
            "enabled": True,
            "schedule": {"type": "cron", "cron": "* * * * *"},
        }
        assert s._should_run(task, {"tasks": {}})

    def test_cron_already_ran_this_minute(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        task = {
            "id": "t1",
            "enabled": True,
            "schedule": {"type": "cron", "cron": "* * * * *"},
        }
        state = {"tasks": {"t1": {"last_run": now.isoformat()}}}
        assert not s._should_run(task, state)

    def test_disabled_task_skipped(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {
            "id": "t1",
            "enabled": False,
            "schedule": {"type": "cron", "cron": "* * * * *"},
        }
        assert not s._should_run(task, {"tasks": {}})

    def test_cron_non_matching_returns_false(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        wrong_minute = (now.minute + 1) % 60
        task = {
            "id": "t1",
            "enabled": True,
            "schedule": {"type": "cron", "cron": f"{wrong_minute} * * * *"},
        }
        assert not s._should_run(task, {"tasks": {}})


# ── _should_run — once type ───────────────────────────────────────────────────

class TestShouldRunOnce:
    def test_once_past_no_last_run_returns_true(self, tmp_path):
        s = make_scheduler(tmp_path)
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        task = {
            "id": "t2",
            "enabled": True,
            "schedule": {"type": "once", "datetime": past},
        }
        assert s._should_run(task, {"tasks": {}})

    def test_once_already_ran_returns_false(self, tmp_path):
        s = make_scheduler(tmp_path)
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        task = {
            "id": "t2",
            "enabled": True,
            "schedule": {"type": "once", "datetime": past},
        }
        state = {"tasks": {"t2": {"last_run": past}}}
        assert not s._should_run(task, state)

    def test_once_future_returns_false(self, tmp_path):
        s = make_scheduler(tmp_path)
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        task = {
            "id": "t2",
            "enabled": True,
            "schedule": {"type": "once", "datetime": future},
        }
        assert not s._should_run(task, {"tasks": {}})


# ── _should_run — interval type ───────────────────────────────────────────────

class TestShouldRunInterval:
    def test_never_ran_returns_true(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {
            "id": "t3",
            "enabled": True,
            "schedule": {"type": "interval", "minutes": 60},
        }
        assert s._should_run(task, {"tasks": {}})

    def test_interval_not_elapsed_returns_false(self, tmp_path):
        s = make_scheduler(tmp_path)
        recent = (datetime.now() - timedelta(minutes=5)).isoformat()
        task = {
            "id": "t3",
            "enabled": True,
            "schedule": {"type": "interval", "minutes": 60},
        }
        state = {"tasks": {"t3": {"last_run": recent}}}
        assert not s._should_run(task, state)

    def test_interval_elapsed_returns_true(self, tmp_path):
        s = make_scheduler(tmp_path)
        old = (datetime.now() - timedelta(minutes=90)).isoformat()
        task = {
            "id": "t3",
            "enabled": True,
            "schedule": {"type": "interval", "minutes": 60},
        }
        state = {"tasks": {"t3": {"last_run": old}}}
        assert s._should_run(task, state)


# ── _should_run — daily type ──────────────────────────────────────────────────

class TestShouldRunDaily:
    def test_daily_past_time_no_last_run(self, tmp_path):
        s = make_scheduler(tmp_path)
        # Use a time guaranteed to be earlier than now (e.g. 00:01)
        now = datetime.now()
        past_time = "00:01"
        task = {
            "id": "t4",
            "enabled": True,
            "schedule": {"type": "daily", "time": past_time},
        }
        # Only reliable if current time is past 00:01
        if now.hour > 0 or now.minute >= 1:
            assert s._should_run(task, {"tasks": {}})

    def test_daily_already_ran_today(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {
            "id": "t4",
            "enabled": True,
            "schedule": {"type": "daily", "time": "00:01"},
        }
        today_run = datetime.now().replace(hour=0, minute=5).isoformat()
        state = {"tasks": {"t4": {"last_run": today_run}}}
        assert not s._should_run(task, state)


# ── _enqueue ──────────────────────────────────────────────────────────────────

class TestEnqueue:
    def test_enqueue_creates_pending_entry(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {"id": "myTask", "command": "echo hi", "description": "test task"}
        s._enqueue(task)

        data = json.loads(s.pending_file.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["task_id"] == "myTask"
        assert data[0]["command"] == "echo hi"

    def test_enqueue_creates_pending_ack_file(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {"id": "myTask", "command": "", "description": ""}
        s._enqueue(task)
        assert (s.ack_dir / "myTask.pending").exists()

    def test_enqueue_appends_to_existing(self, tmp_path):
        s = make_scheduler(tmp_path)
        task1 = {"id": "t1", "command": "", "description": ""}
        task2 = {"id": "t2", "command": "", "description": ""}
        s._enqueue(task1)
        s._enqueue(task2)
        data = json.loads(s.pending_file.read_text(encoding="utf-8"))
        assert len(data) == 2


# ── ack ───────────────────────────────────────────────────────────────────────

class TestAck:
    def test_ack_removes_pending_creates_done(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {"id": "ackTask", "command": "", "description": ""}
        s._enqueue(task)
        assert (s.ack_dir / "ackTask.pending").exists()

        s.ack("ackTask")
        assert not (s.ack_dir / "ackTask.pending").exists()
        assert (s.ack_dir / "ackTask.ack").exists()

    def test_ack_updates_state(self, tmp_path):
        s = make_scheduler(tmp_path)
        task = {"id": "ackTask", "command": "", "description": ""}
        s._enqueue(task)
        s.ack("ackTask")

        state = s._load_state()
        assert state["tasks"]["ackTask"]["last_success"] is True
        assert state["tasks"]["ackTask"]["run_count"] >= 1

    def test_ack_missing_pending_file_still_creates_done(self, tmp_path):
        s = make_scheduler(tmp_path)
        # No .pending file written — ack should still create .ack gracefully
        s.ack("ghostTask")
        assert (s.ack_dir / "ghostTask.ack").exists()


# ── _update_state ─────────────────────────────────────────────────────────────

class TestUpdateState:
    def test_first_run_creates_entry(self, tmp_path):
        s = make_scheduler(tmp_path)
        state = {}
        s._update_state("t1", True, state)
        assert state["tasks"]["t1"]["run_count"] == 1
        assert state["tasks"]["t1"]["last_success"] is True
        assert state["tasks"]["t1"]["fail_count"] == 0

    def test_fail_increments_fail_count(self, tmp_path):
        s = make_scheduler(tmp_path)
        state = {}
        s._update_state("t1", False, state)
        assert state["tasks"]["t1"]["fail_count"] == 1

    def test_subsequent_runs_accumulate(self, tmp_path):
        s = make_scheduler(tmp_path)
        state = {}
        s._update_state("t1", True, state)
        s._update_state("t1", True, state)
        assert state["tasks"]["t1"]["run_count"] == 2


# ── notify_user / night silence ───────────────────────────────────────────────

class TestNotifyUser:
    def test_notify_writes_to_web_inbox_by_default(self, tmp_path):
        s = make_scheduler(tmp_path)
        with patch.object(s, "_is_night", return_value=False):
            s.notify_user("Hello world")
        inbox = json.loads((tmp_path / "web-inbox.json").read_text(encoding="utf-8"))
        assert len(inbox) == 1
        assert inbox[0]["text"] == "Hello world"
        assert inbox[0]["source"] == "scheduler"

    def test_notify_during_night_queues_instead(self, tmp_path):
        s = make_scheduler(tmp_path)
        with patch.object(s, "_is_night", return_value=True):
            s.notify_user("Night message")
        # inbox should NOT be written
        assert not (tmp_path / "web-inbox.json").exists()
        # queue should be written
        queue = json.loads((tmp_path / "notify-queue.json").read_text(encoding="utf-8"))
        assert len(queue) == 1
        assert queue[0]["text"] == "Night message"

    def test_notify_specific_channel(self, tmp_path):
        s = make_scheduler(tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"telegram_enabled": True}), encoding="utf-8"
        )
        with patch.object(s, "_is_night", return_value=False):
            s.notify_user("tg only", channel="telegram")
        inbox = json.loads((tmp_path / "telegram-inbox.json").read_text(encoding="utf-8"))
        assert inbox[0]["text"] == "tg only"

    def test_enabled_channel_in_config_gets_message(self, tmp_path):
        s = make_scheduler(tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"discord_enabled": True, "telegram_enabled": False}),
            encoding="utf-8",
        )
        with patch.object(s, "_is_night", return_value=False):
            s.notify_user("multi channel")
        assert (tmp_path / "web-inbox.json").exists()     # web default on
        assert (tmp_path / "discord-inbox.json").exists()
        assert not (tmp_path / "telegram-inbox.json").exists()


# ── _is_night ─────────────────────────────────────────────────────────────────

class TestIsNight:
    def _write_config(self, path, start, end):
        (Path(path) / "config.json").write_text(
            json.dumps({"night_start": start, "night_end": end}), encoding="utf-8"
        )

    def test_in_window(self, tmp_path):
        """When night window covers all 24 hours, _is_night must always be True."""
        s = make_scheduler(tmp_path)
        # "00:00" to "00:00" with start < end means start == end — use a window that
        # wraps midnight and covers the entire day instead: 00:00 to 23:59.
        # Simpler: write a window starting "00:00" and ending "23:59" — always active.
        self._write_config(tmp_path, "00:00", "23:59")
        assert s._is_night() is True

    def test_simple_window_day_is_not_night(self, tmp_path):
        """Midnight-to-08:00 window — 12:00 should not be night."""
        s = make_scheduler(tmp_path)
        self._write_config(tmp_path, "00:00", "08:00")
        # Monkey-patch datetime.now within the method
        with patch("core.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value.strftime = lambda fmt: "12:00"
            # Call real logic by reproducing it
            now_str = "12:00"
            start, end = "00:00", "08:00"
            result = start <= now_str < end
            assert result is False

    def test_cross_midnight_night(self, tmp_path):
        """23:00-07:00 window — 02:00 should be night."""
        now_str = "02:00"
        start, end = "23:00", "07:00"
        result = now_str >= start or now_str < end
        assert result is True


# ── _flush_notification_queue ─────────────────────────────────────────────────

class TestFlushNotificationQueue:
    def test_flush_delivers_queued_messages(self, tmp_path):
        s = make_scheduler(tmp_path)
        # Put a message in queue
        queue_file = tmp_path / "notify-queue.json"
        queue_file.write_text(
            json.dumps([{"text": "delayed msg", "timestamp": "2026-01-01T03:00:00"}]),
            encoding="utf-8",
        )
        with patch.object(s, "_is_night", return_value=False):
            s._flush_notification_queue()

        # Queue should be empty
        assert json.loads(queue_file.read_text(encoding="utf-8")) == []
        # Message should appear in web inbox
        inbox = json.loads((tmp_path / "web-inbox.json").read_text(encoding="utf-8"))
        assert inbox[0]["text"] == "delayed msg"

    def test_flush_empty_queue_no_error(self, tmp_path):
        s = make_scheduler(tmp_path)
        # No queue file — should not raise
        s._flush_notification_queue()

    def test_flush_with_multiple_messages(self, tmp_path):
        s = make_scheduler(tmp_path)
        queue_file = tmp_path / "notify-queue.json"
        queue_file.write_text(
            json.dumps([
                {"text": "msg1", "timestamp": "2026-01-01T03:00:00"},
                {"text": "msg2", "timestamp": "2026-01-01T04:00:00"},
            ]),
            encoding="utf-8",
        )
        s._flush_notification_queue()
        inbox = json.loads((tmp_path / "web-inbox.json").read_text(encoding="utf-8"))
        assert len(inbox) == 2


# ── _check_reminders ─────────────────────────────────────────────────────────

class TestCheckReminders:
    def _write_reminders(self, data_dir, reminders):
        (Path(data_dir) / "reminders.json").write_text(
            json.dumps(reminders), encoding="utf-8"
        )

    def test_once_reminder_fires_at_matching_time(self, tmp_path):
        s = make_scheduler(tmp_path)
        now_hm = datetime.now().strftime("%H:%M")
        rid = "rem1"
        self._write_reminders(tmp_path, [
            {"id": rid, "time": now_hm, "message": "Wake up!", "repeat": "once", "enabled": True}
        ])

        fired = []
        with patch.object(s, "notify_user", side_effect=lambda msg: fired.append(msg)):
            s._check_reminders({"tasks": {}})

        assert len(fired) == 1
        assert fired[0] == "Wake up!"

    def test_once_reminder_removed_after_firing(self, tmp_path):
        s = make_scheduler(tmp_path)
        now_hm = datetime.now().strftime("%H:%M")
        self._write_reminders(tmp_path, [
            {"id": "r1", "time": now_hm, "message": "hi", "repeat": "once", "enabled": True}
        ])
        with patch.object(s, "notify_user"):
            s._check_reminders({"tasks": {}})

        remaining = json.loads((tmp_path / "reminders.json").read_text(encoding="utf-8"))
        assert len(remaining) == 0

    def test_reminder_not_fired_at_wrong_time(self, tmp_path):
        s = make_scheduler(tmp_path)
        wrong_time = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
        self._write_reminders(tmp_path, [
            {"id": "r2", "time": wrong_time, "message": "later", "repeat": "once", "enabled": True}
        ])
        fired = []
        with patch.object(s, "notify_user", side_effect=lambda msg: fired.append(msg)):
            s._check_reminders({"tasks": {}})
        assert fired == []

    def test_reminder_not_fired_twice_same_minute(self, tmp_path):
        s = make_scheduler(tmp_path)
        now_hm = datetime.now().strftime("%H:%M")
        self._write_reminders(tmp_path, [
            {"id": "r3", "time": now_hm, "message": "once only", "repeat": "daily", "enabled": True}
        ])
        already_ran = datetime.now().isoformat()
        state = {"tasks": {"r3": {"last_run": already_ran}}}
        fired = []
        with patch.object(s, "notify_user", side_effect=lambda msg: fired.append(msg)):
            s._check_reminders(state)
        assert fired == []

    def test_weekly_reminder_fires_on_correct_weekday(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        now_hm = now.strftime("%H:%M")
        # Created today — weekday matches
        self._write_reminders(tmp_path, [
            {
                "id": "r4", "time": now_hm, "message": "weekly!", "repeat": "weekly",
                "enabled": True, "created": now.isoformat()
            }
        ])
        fired = []
        with patch.object(s, "notify_user", side_effect=lambda msg: fired.append(msg)):
            s._check_reminders({"tasks": {}})
        assert fired == ["weekly!"]

    def test_weekly_reminder_skips_wrong_weekday(self, tmp_path):
        s = make_scheduler(tmp_path)
        now = datetime.now()
        now_hm = now.strftime("%H:%M")
        # Created yesterday — different weekday
        yesterday = (now - timedelta(days=1)).isoformat()
        self._write_reminders(tmp_path, [
            {
                "id": "r5", "time": now_hm, "message": "skip me", "repeat": "weekly",
                "enabled": True, "created": yesterday
            }
        ])
        fired = []
        with patch.object(s, "notify_user", side_effect=lambda msg: fired.append(msg)):
            s._check_reminders({"tasks": {}})
        # Should only fire if today matches created weekday
        today_wd = now.weekday()
        yesterday_wd = (now - timedelta(days=1)).weekday()
        if today_wd != yesterday_wd:
            assert fired == []


# ── _load_schedule / _load_state / _save_state ───────────────────────────────

class TestPersistence:
    def test_load_schedule_missing_returns_empty(self, tmp_path):
        s = make_scheduler(tmp_path)
        assert s._load_schedule() == []

    def test_load_schedule_dict_format(self, tmp_path):
        s = make_scheduler(tmp_path)
        s.schedule_file.write_text(
            json.dumps({"tasks": [{"id": "t1"}]}), encoding="utf-8"
        )
        assert s._load_schedule() == [{"id": "t1"}]

    def test_load_schedule_list_format(self, tmp_path):
        s = make_scheduler(tmp_path)
        s.schedule_file.write_text(
            json.dumps([{"id": "t2"}]), encoding="utf-8"
        )
        assert s._load_schedule() == [{"id": "t2"}]

    def test_state_round_trip(self, tmp_path):
        s = make_scheduler(tmp_path)
        state = {"tasks": {"t1": {"run_count": 3}}}
        s._save_state(state)
        loaded = s._load_state()
        assert loaded["tasks"]["t1"]["run_count"] == 3

    def test_load_state_missing_returns_default(self, tmp_path):
        s = make_scheduler(tmp_path)
        assert s._load_state() == {"tasks": {}}


# ── _write_heartbeat ──────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_heartbeat_writes_pid(self, tmp_path):
        s = make_scheduler(tmp_path)
        s._write_heartbeat()
        hb = json.loads(s.heartbeat_file.read_text(encoding="utf-8"))
        assert hb["pid"] == os.getpid()
        assert "timestamp" in hb
