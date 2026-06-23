"""
Tests for core/agents.py — PFAgentManager background agent system.
"""
import json
import os
import sys
import time
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agents import AgentResult, PFAgentManager


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def manager(data_dir):
    return PFAgentManager(data_dir=data_dir)


# ── AgentResult ───────────────────────────────────────────────────────────────


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult("my-agent", "my_task")
        assert r.agent_name == "my-agent"
        assert r.task == "my_task"
        assert r.success is False
        assert r.output == ""
        assert r.error == ""
        assert r.completed_at == ""
        assert r.changes == []
        assert r.started_at != ""

    def test_to_dict_contains_all_keys(self):
        r = AgentResult("a", "t")
        d = r.to_dict()
        for key in ("agent", "task", "success", "output", "error",
                    "started_at", "completed_at", "changes"):
            assert key in d

    def test_to_dict_values(self):
        r = AgentResult("myagent", "mytask")
        r.success = True
        r.output = "done"
        r.changes = ["change1"]
        d = r.to_dict()
        assert d["agent"] == "myagent"
        assert d["task"] == "mytask"
        assert d["success"] is True
        assert d["output"] == "done"
        assert d["changes"] == ["change1"]


# ── PFAgentManager basics ─────────────────────────────────────────────────────


class TestAgentManagerInit:
    def test_data_dir_stored(self, manager, data_dir):
        assert str(manager.data_dir) == data_dir

    def test_results_file_path(self, manager, data_dir):
        assert str(manager.results_file) == os.path.join(data_dir, "agent-results.json")

    def test_empty_active_agents(self, manager):
        assert manager.get_active() == []

    def test_custom_config(self, data_dir):
        m = PFAgentManager(data_dir=data_dir, config={"key": "val"})
        assert m.config["key"] == "val"

    def test_default_config_empty(self, manager):
        assert manager.config == {}


# ── is_running ────────────────────────────────────────────────────────────────


class TestIsRunning:
    def test_not_running_initially(self, manager):
        assert manager.is_running("any-agent") is False

    def test_running_while_thread_alive(self, manager):
        barrier = threading.Event()

        def slow_task(data_dir, provider, **kw):
            barrier.wait(timeout=5)
            r = AgentResult("slow", "slow_task")
            r.success = True
            return r

        manager.run_agent("slow", slow_task)
        # Give thread a moment to start
        time.sleep(0.05)
        assert manager.is_running("slow") is True
        barrier.set()
        time.sleep(0.1)


# ── run_agent ─────────────────────────────────────────────────────────────────


class TestRunAgent:
    def _simple_task(self, data_dir, provider, **kw):
        r = AgentResult("simple", "_simple_task")
        r.success = True
        r.output = "hello"
        return r

    def test_agent_runs_and_saves_result(self, manager, data_dir):
        manager.run_agent("simple", self._simple_task)
        # Wait for completion
        for _ in range(50):
            if not manager.is_running("simple"):
                break
            time.sleep(0.05)

        results = manager.get_recent_results()
        assert len(results) == 1
        assert results[0]["agent"] == "simple"

    def test_agent_not_duplicated_when_running(self, manager):
        barrier = threading.Event()

        def blocking_task(data_dir, provider, **kw):
            barrier.wait(timeout=5)
            r = AgentResult("dup", "blocking_task")
            r.success = True
            return r

        manager.run_agent("dup", blocking_task)
        time.sleep(0.05)
        # Second call should be a no-op
        manager.run_agent("dup", blocking_task)
        assert len([t for t in manager.active_agents.values() if t.is_alive()]) == 1
        barrier.set()
        time.sleep(0.1)

    def test_agent_result_saved_on_success(self, manager, data_dir):
        def task(data_dir, provider, **kw):
            r = AgentResult("res-test", "task")
            r.success = True
            r.changes = ["a", "b"]
            return r

        manager.run_agent("res-test", task)
        for _ in range(50):
            if not manager.is_running("res-test"):
                break
            time.sleep(0.05)

        results = manager.get_recent_results()
        assert results[-1]["changes"] == ["a", "b"]

    def test_agent_result_saved_on_exception(self, manager, data_dir):
        def failing_task(data_dir, provider, **kw):
            raise RuntimeError("intentional failure")

        manager.run_agent("fail-test", failing_task)
        for _ in range(50):
            if not manager.is_running("fail-test"):
                break
            time.sleep(0.05)

        results = manager.get_recent_results()
        assert len(results) >= 1
        # The result should be saved even on failure
        last = results[-1]
        assert last["agent"] == "fail-test"

    def test_agent_removed_from_active_after_done(self, manager):
        def quick_task(data_dir, provider, **kw):
            r = AgentResult("quick", "quick_task")
            r.success = True
            return r

        manager.run_agent("quick", quick_task)
        for _ in range(50):
            if not manager.is_running("quick"):
                break
            time.sleep(0.05)

        assert "quick" not in manager.active_agents


# ── _save_result / get_recent_results ─────────────────────────────────────────


class TestResultPersistence:
    def test_get_recent_results_empty_when_no_file(self, manager):
        assert manager.get_recent_results() == []

    def test_get_recent_results_returns_last_n(self, manager):
        # Manually save 5 results
        results = [{"agent": f"a{i}", "task": "t", "success": True,
                    "output": "", "error": "", "started_at": "", "completed_at": "",
                    "changes": []} for i in range(5)]
        manager.results_file.write_text(json.dumps(results), encoding="utf-8")
        recent = manager.get_recent_results(limit=3)
        assert len(recent) == 3
        assert recent[-1]["agent"] == "a4"

    def test_get_recent_results_handles_corrupt_file(self, manager):
        manager.results_file.write_text("CORRUPT", encoding="utf-8")
        assert manager.get_recent_results() == []

    def test_save_result_appends(self, manager):
        r1 = AgentResult("a1", "t1")
        r1.success = True
        r2 = AgentResult("a2", "t2")
        r2.success = True
        manager._save_result(r1)
        manager._save_result(r2)
        results = json.loads(manager.results_file.read_text(encoding="utf-8"))
        assert len(results) == 2

    def test_save_result_caps_at_100(self, manager):
        # Fill with 100 existing results
        existing = [{"agent": f"a{i}", "task": "t", "success": True,
                     "output": "", "error": "", "started_at": "", "completed_at": "",
                     "changes": []} for i in range(100)]
        manager.results_file.write_text(json.dumps(existing), encoding="utf-8")
        r = AgentResult("new", "t")
        r.success = True
        manager._save_result(r)
        results = json.loads(manager.results_file.read_text(encoding="utf-8"))
        assert len(results) == 100
        assert results[-1]["agent"] == "new"


# ── get_active ────────────────────────────────────────────────────────────────


class TestGetActive:
    def test_empty_when_no_agents(self, manager):
        assert manager.get_active() == []

    def test_returns_running_agent_names(self, manager):
        barrier = threading.Event()

        def blocking(data_dir, provider, **kw):
            barrier.wait(timeout=5)
            r = AgentResult("bg", "blocking")
            r.success = True
            return r

        manager.run_agent("bg", blocking)
        time.sleep(0.05)
        active = manager.get_active()
        assert "bg" in active
        barrier.set()
        time.sleep(0.1)


# ── check_stalls ──────────────────────────────────────────────────────────────


class TestCheckStalls:
    def test_no_stalls_when_no_agents(self, manager):
        warnings = manager.check_stalls()
        assert warnings == []

    def test_stall_detected_when_activity_old(self, manager):
        barrier = threading.Event()

        def slow(data_dir, provider, **kw):
            barrier.wait(timeout=10)
            r = AgentResult("stall-test", "slow")
            r.success = True
            return r

        manager.run_agent("stall-test", slow)
        time.sleep(0.05)

        # Manually backdate the activity timestamp
        with manager._lock:
            manager._agent_activity["stall-test"]["last"] = time.time() - 200

        warnings = manager.check_stalls()
        assert any("stall-test" in w for w in warnings)
        barrier.set()
        time.sleep(0.1)

    def test_no_duplicate_stall_warnings(self, manager):
        barrier = threading.Event()

        def slow(data_dir, provider, **kw):
            barrier.wait(timeout=10)
            r = AgentResult("stall2", "slow")
            r.success = True
            return r

        manager.run_agent("stall2", slow)
        time.sleep(0.05)

        with manager._lock:
            manager._agent_activity["stall2"]["last"] = time.time() - 200

        warnings1 = manager.check_stalls()
        warnings2 = manager.check_stalls()
        # Second call should not re-warn (stall_warned=True)
        assert len(warnings1) >= 1
        assert len(warnings2) == 0
        barrier.set()
        time.sleep(0.1)
