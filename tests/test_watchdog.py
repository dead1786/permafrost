"""Tests for core/watchdog.py — PFWatchdog auto-healing daemon monitor."""
import json
import os
import sys
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.watchdog import PFWatchdog


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_watchdog_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wd(data_dir):
    return PFWatchdog(data_dir=data_dir, config={
        "check_interval": 300,
        "heartbeat_max_age": 60,
        "max_fail_count": 3,
        "max_restart_count": 5,
        "restart_cooldown": 5,
    })


def write_heartbeat(path: Path, pid: int = 1234, age_seconds: float = 0.0):
    """Write a heartbeat file with a given age."""
    ts = datetime.now() - timedelta(seconds=age_seconds)
    path.write_text(json.dumps({
        "timestamp": ts.isoformat(),
        "pid": pid,
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------

class TestInit:
    def test_defaults(self, data_dir):
        wd = PFWatchdog(data_dir=data_dir)
        assert wd.check_interval == 300
        assert wd.heartbeat_max_age == 180
        assert wd.max_fail_count == 3
        assert wd.max_restart_count == 5
        assert wd.restart_cooldown == 60

    def test_custom_config(self, data_dir):
        wd = PFWatchdog(data_dir=data_dir, config={"check_interval": 60, "heartbeat_max_age": 30})
        assert wd.check_interval == 60
        assert wd.heartbeat_max_age == 30

    def test_services_empty(self, wd):
        assert wd.services == {}


# ---------------------------------------------------------------------------
# TestRegisterService
# ---------------------------------------------------------------------------

class TestRegisterService:
    def test_registers_service(self, wd, data_dir):
        hb = Path(data_dir) / "brain.hb"
        wd.register_service("brain", str(hb), ["python", "main.py"])
        assert "brain" in wd.services

    def test_heartbeat_path_is_path_object(self, wd, data_dir):
        hb = Path(data_dir) / "brain.hb"
        wd.register_service("brain", str(hb), ["python", "main.py"])
        assert isinstance(wd.services["brain"]["heartbeat_file"], Path)

    def test_restart_cmd_stored(self, wd, data_dir):
        hb = Path(data_dir) / "sched.hb"
        wd.register_service("scheduler", str(hb), ["python", "scheduler.py"])
        assert wd.services["scheduler"]["restart_cmd"] == ["python", "scheduler.py"]


# ---------------------------------------------------------------------------
# TestCheckHeartbeat
# ---------------------------------------------------------------------------

class TestCheckHeartbeat:
    def test_missing_file_returns_false(self, wd, data_dir):
        hb = Path(data_dir) / "missing.hb"
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is False
        assert age == -1
        assert pid == -1

    def test_fresh_heartbeat_returns_true(self, wd, data_dir):
        hb = Path(data_dir) / "fresh.hb"
        write_heartbeat(hb, pid=999, age_seconds=5)
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is True
        assert age < 60
        assert pid == 999

    def test_stale_heartbeat_returns_false(self, wd, data_dir):
        hb = Path(data_dir) / "stale.hb"
        write_heartbeat(hb, pid=111, age_seconds=120)
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is False
        assert age >= 60

    def test_corrupt_heartbeat_returns_false(self, wd, data_dir):
        hb = Path(data_dir) / "corrupt.hb"
        hb.write_text("not-json", encoding="utf-8")
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is False

    def test_missing_timestamp_returns_false(self, wd, data_dir):
        hb = Path(data_dir) / "notimestamp.hb"
        hb.write_text(json.dumps({"pid": 123}), encoding="utf-8")
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is False

    def test_pid_optional(self, wd, data_dir):
        hb = Path(data_dir) / "nopid.hb"
        hb.write_text(json.dumps({"timestamp": datetime.now().isoformat()}), encoding="utf-8")
        ok, age, pid = wd._check_heartbeat(hb)
        assert ok is True
        assert pid == -1


# ---------------------------------------------------------------------------
# TestCanRestart
# ---------------------------------------------------------------------------

class TestCanRestart:
    def test_can_restart_first_time(self, wd):
        assert wd._can_restart("brain") is True

    def test_cannot_restart_after_limit(self, wd):
        now = time.time()
        wd._restart_history["brain"] = [now - 10] * 5  # 5 within last hour
        assert wd._can_restart("brain") is False

    def test_cooldown_prevents_immediate_restart(self, wd):
        now = time.time()
        wd._restart_history["brain"] = [now - 1]  # 1 second ago, cooldown is 5
        assert wd._can_restart("brain") is False

    def test_old_restarts_not_counted(self, wd):
        now = time.time()
        # All restarts older than 1 hour
        wd._restart_history["brain"] = [now - 7200] * 10
        assert wd._can_restart("brain") is True

    def test_cooldown_passed_allows_restart(self, wd):
        now = time.time()
        wd._restart_history["brain"] = [now - 10]  # 10s ago, cooldown is 5
        assert wd._can_restart("brain") is True

    def test_cleans_old_entries_from_history(self, wd):
        now = time.time()
        wd._restart_history["brain"] = [now - 7200, now - 3600, now - 10]
        wd._can_restart("brain")
        assert len(wd._restart_history["brain"]) == 1  # Only recent one kept


# ---------------------------------------------------------------------------
# TestLog
# ---------------------------------------------------------------------------

class TestLog:
    def test_creates_log_file(self, wd):
        wd._log("test message")
        assert wd.log_file.exists()

    def test_log_contains_message(self, wd):
        wd._log("hello watchdog")
        content = wd.log_file.read_text(encoding="utf-8")
        assert "hello watchdog" in content

    def test_log_rotates_when_large(self, wd):
        # Write 1.1MB of log content
        wd.log_file.parent.mkdir(parents=True, exist_ok=True)
        wd.log_file.write_text("x" * 1_100_000, encoding="utf-8")
        wd._log("trigger rotation")
        rotated = wd.log_file.with_suffix(".log.1")
        assert rotated.exists()


# ---------------------------------------------------------------------------
# TestIsProcessAlive
# ---------------------------------------------------------------------------

class TestIsProcessAlive:
    def test_negative_pid_returns_false(self, wd):
        assert wd._is_process_alive(-1) is False

    def test_zero_pid_returns_false(self, wd):
        assert wd._is_process_alive(0) is False

    def test_current_process_alive(self, wd):
        assert wd._is_process_alive(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self, wd):
        # PID 99999999 is extremely unlikely to exist
        assert wd._is_process_alive(99999999) is False


# ---------------------------------------------------------------------------
# TestRestartService
# ---------------------------------------------------------------------------

class TestRestartService:
    def test_restart_when_allowed(self, wd):
        with mock.patch("subprocess.Popen") as mock_popen, \
             mock.patch("time.sleep"):
            mock_popen.return_value = mock.MagicMock()
            result = wd._restart_service("brain", ["python", "main.py"])
        assert result is True

    def test_restart_skipped_when_limit_reached(self, wd):
        now = time.time()
        wd._restart_history["brain"] = [now - 10] * 5
        result = wd._restart_service("brain", ["python", "main.py"])
        assert result is False

    def test_restart_records_timestamp(self, wd):
        with mock.patch("subprocess.Popen") as mock_popen, \
             mock.patch("time.sleep"):
            mock_popen.return_value = mock.MagicMock()
            wd._restart_service("brain", ["python", "main.py"])
        assert len(wd._restart_history.get("brain", [])) == 1

    def test_restart_returns_false_on_exception(self, wd):
        with mock.patch("subprocess.Popen", side_effect=OSError("no binary")):
            result = wd._restart_service("brain", ["bad_cmd"])
        assert result is False


# ---------------------------------------------------------------------------
# TestCheckAll — integration
# ---------------------------------------------------------------------------

class TestCheckAll:
    def test_no_services_no_issues(self, wd):
        assert wd.check_all() == []

    def test_fresh_heartbeat_no_issues(self, wd, data_dir):
        hb = Path(data_dir) / "brain.hb"
        write_heartbeat(hb, pid=os.getpid(), age_seconds=5)
        wd.register_service("brain", str(hb), ["python", "main.py"])
        issues = wd.check_all()
        assert issues == []

    def test_missing_heartbeat_reports_issue(self, wd, data_dir):
        hb = Path(data_dir) / "missing.hb"
        wd.register_service("brain", str(hb), ["python", "main.py"])
        with mock.patch.object(wd, "_restart_service", return_value=True):
            issues = wd.check_all()
        assert any("no heartbeat" in i for i in issues)

    def test_stale_heartbeat_reports_issue(self, wd, data_dir):
        hb = Path(data_dir) / "stale.hb"
        write_heartbeat(hb, pid=os.getpid(), age_seconds=120)
        wd.register_service("brain", str(hb), ["python", "main.py"])
        with mock.patch.object(wd, "_restart_service", return_value=True):
            issues = wd.check_all()
        assert any("stale" in i for i in issues)

    def test_dead_pid_reports_issue(self, wd, data_dir):
        hb = Path(data_dir) / "deadpid.hb"
        write_heartbeat(hb, pid=99999999, age_seconds=5)
        wd.register_service("brain", str(hb), ["python", "main.py"])
        with mock.patch.object(wd, "_restart_service", return_value=True):
            issues = wd.check_all()
        assert any("not alive" in i for i in issues)

    def test_dead_pid_restart_failure_adds_failed_issue(self, wd, data_dir):
        hb = Path(data_dir) / "deadpid2.hb"
        write_heartbeat(hb, pid=99999999, age_seconds=5)
        wd.register_service("brain", str(hb), ["python", "main.py"])
        with mock.patch.object(wd, "_restart_service", return_value=False):
            issues = wd.check_all()
        assert any("not alive" in i for i in issues)
        assert any("FAILED" in i for i in issues)

    def test_restart_failure_adds_second_issue(self, wd, data_dir):
        hb = Path(data_dir) / "missing2.hb"
        wd.register_service("brain", str(hb), ["python", "main.py"])
        with mock.patch.object(wd, "_restart_service", return_value=False):
            issues = wd.check_all()
        assert any("FAILED" in i for i in issues)

    def test_scheduler_fail_count_reported(self, wd, data_dir):
        state = {
            "tasks": {
                "daily_report": {"fail_count": 5},
                "ok_task": {"fail_count": 0},
            }
        }
        state_file = Path(data_dir) / "scheduler-state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        issues = wd.check_all()
        assert any("daily_report" in i and "fail_count=5" in i for i in issues)
        assert not any("ok_task" in i for i in issues)

    def test_corrupt_scheduler_state_no_crash(self, wd, data_dir):
        state_file = Path(data_dir) / "scheduler-state.json"
        state_file.write_text("not-json", encoding="utf-8")
        issues = wd.check_all()
        assert isinstance(issues, list)  # Did not raise

    def test_multiple_services_checked(self, wd, data_dir):
        hb1 = Path(data_dir) / "s1.hb"
        hb2 = Path(data_dir) / "s2.hb"
        write_heartbeat(hb1, pid=os.getpid(), age_seconds=5)
        # hb2 missing
        wd.register_service("service1", str(hb1), ["cmd1"])
        wd.register_service("service2", str(hb2), ["cmd2"])
        with mock.patch.object(wd, "_restart_service", return_value=True):
            issues = wd.check_all()
        assert any("service2" in i for i in issues)
        assert not any("service1" in i for i in issues)


# ---------------------------------------------------------------------------
# TestRunOnce
# ---------------------------------------------------------------------------

class TestRunOnce:
    def test_returns_issue_list(self, wd):
        with mock.patch.object(wd, "check_all", return_value=["brain: stale"]):
            result = wd.run_once()
        assert result == ["brain: stale"]

    def test_returns_empty_when_all_ok(self, wd):
        with mock.patch.object(wd, "check_all", return_value=[]):
            result = wd.run_once()
        assert result == []

    def test_logs_on_run(self, wd):
        with mock.patch.object(wd, "check_all", return_value=[]):
            wd.run_once()
        assert wd.log_file.exists()


# ---------------------------------------------------------------------------
# TestLogEdgeCases — rotation-with-existing-file and write failure
# ---------------------------------------------------------------------------

class TestLogEdgeCases:
    def test_log_rotation_replaces_existing_rotated_file(self, wd):
        # Pre-create an old rotated log so the unlink() branch (line 66) runs.
        wd.log_file.parent.mkdir(parents=True, exist_ok=True)
        rotated = wd.log_file.with_suffix(".log.1")
        rotated.write_text("old rotated content", encoding="utf-8")
        wd.log_file.write_text("x" * 1_100_000, encoding="utf-8")

        wd._log("trigger rotation again")

        assert rotated.exists()
        assert rotated.read_text(encoding="utf-8") != "old rotated content"

    def test_log_swallows_oserror_on_write(self, wd):
        wd.log_file.parent.mkdir(parents=True, exist_ok=True)
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            # Should not raise even though writing the log line fails.
            wd._log("this will fail to persist")


# ---------------------------------------------------------------------------
# TestIsProcessAlivePosix — non-Windows branch of _is_process_alive
# ---------------------------------------------------------------------------

class TestIsProcessAlivePosix:
    def test_posix_alive_process(self, wd):
        with mock.patch("core.watchdog.sys.platform", "linux"), \
             mock.patch("os.kill", return_value=None) as mock_kill:
            assert wd._is_process_alive(1234) is True
        mock_kill.assert_called_once_with(1234, 0)

    def test_posix_dead_process_raises_oserror(self, wd):
        with mock.patch("core.watchdog.sys.platform", "linux"), \
             mock.patch("os.kill", side_effect=OSError("no such process")):
            assert wd._is_process_alive(9999) is False


# ---------------------------------------------------------------------------
# TestRestartServicePosix — non-Windows branch of _restart_service
# ---------------------------------------------------------------------------

class TestRestartServicePosix:
    def test_posix_restart_uses_start_new_session(self, wd):
        with mock.patch("core.watchdog.sys.platform", "linux"), \
             mock.patch("subprocess.Popen") as mock_popen, \
             mock.patch("time.sleep"):
            mock_popen.return_value = mock.MagicMock()
            result = wd._restart_service("brain", ["python", "main.py"])
        assert result is True
        mock_popen.assert_called_once_with(["python", "main.py"], start_new_session=True)


# ---------------------------------------------------------------------------
# TestRun — the continuous monitoring loop
# ---------------------------------------------------------------------------

class TestRun:
    def test_run_loops_until_keyboard_interrupt(self, wd):
        call_count = {"n": 0}

        def fake_sleep(_seconds):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise KeyboardInterrupt()

        with mock.patch("core.watchdog.time.sleep", side_effect=fake_sleep), \
             mock.patch.object(wd, "run_once", return_value=[]) as mock_run_once:
            wd.run()  # should not raise — KeyboardInterrupt is caught

        # First sleep(30) grace period + at least one loop sleep(check_interval)
        assert call_count["n"] >= 2
        assert mock_run_once.called
        assert "watchdog stopped" in wd.log_file.read_text(encoding="utf-8")
