"""Tests for smart/handover.py — PFHandover session continuity."""
import json
import pytest
from pathlib import Path
from smart.handover import PFHandover


@pytest.fixture
def handover(tmp_path):
    return PFHandover(data_dir=str(tmp_path))


class TestRead:
    def test_returns_defaults_when_file_missing(self, handover):
        result = handover.read()
        assert result == {"active_tasks": [], "standing_rules": []}

    def test_returns_defaults_on_corrupt_json(self, handover):
        handover.handover_file.write_text("not-valid-json", encoding="utf-8")
        result = handover.read()
        assert result == {"active_tasks": [], "standing_rules": []}

    def test_reads_existing_file(self, handover):
        data = {"active_tasks": [{"id": "t1"}], "standing_rules": ["rule-a"]}
        handover.handover_file.write_text(json.dumps(data), encoding="utf-8")
        assert handover.read()["standing_rules"] == ["rule-a"]


class TestWrite:
    def test_creates_file_with_updated_timestamp(self, handover):
        handover.write({"active_tasks": []})
        assert handover.handover_file.exists()
        saved = json.loads(handover.handover_file.read_text(encoding="utf-8"))
        assert "_updated" in saved

    def test_preserves_unicode(self, handover):
        handover.write({"active_tasks": [], "note": "凱的備註"})
        saved = json.loads(handover.handover_file.read_text(encoding="utf-8"))
        assert saved["note"] == "凱的備註"


class TestAddTask:
    def test_adds_new_task(self, handover):
        handover.add_task("t1", "Task One", 1, "do X", "50%", "check Y")
        tasks = handover.read()["active_tasks"]
        assert len(tasks) == 1
        assert tasks[0]["id"] == "t1"
        assert tasks[0]["title"] == "Task One"

    def test_updates_existing_task(self, handover):
        handover.add_task("t1", "Task One", 1, "do X", "0%", "start")
        handover.add_task("t1", "Task One v2", 1, "do X+Y", "80%", "verify")
        tasks = handover.read()["active_tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Task One v2"
        assert tasks[0]["last_progress"] == "80%"

    def test_sorts_by_priority(self, handover):
        handover.add_task("low", "Low", 5, "", "", "")
        handover.add_task("high", "High", 1, "", "", "")
        handover.add_task("mid", "Mid", 3, "", "", "")
        ids = [t["id"] for t in handover.read()["active_tasks"]]
        assert ids == ["high", "mid", "low"]

    def test_missing_priority_field_goes_last(self, handover):
        handover.add_task("normal", "Normal", 2, "", "", "")
        # Inject a task with no priority field directly
        h = handover.read()
        h["active_tasks"].append({"id": "nopri", "title": "No Priority"})
        handover.write(h)
        handover.add_task("urgent", "Urgent", 1, "", "", "")
        ids = [t["id"] for t in handover.read()["active_tasks"]]
        assert ids[0] == "urgent"
        assert ids[-1] == "nopri"


class TestCompleteTask:
    def test_removes_task(self, handover):
        handover.add_task("t1", "T1", 1, "", "", "")
        handover.add_task("t2", "T2", 2, "", "", "")
        handover.complete_task("t1")
        ids = [t["id"] for t in handover.read()["active_tasks"]]
        assert "t1" not in ids
        assert "t2" in ids

    def test_noop_for_missing_task(self, handover):
        handover.add_task("t1", "T1", 1, "", "", "")
        handover.complete_task("nonexistent")
        assert len(handover.read()["active_tasks"]) == 1


class TestUpdateProgress:
    def test_updates_progress_and_checkpoint(self, handover):
        handover.add_task("t1", "T1", 1, "do X", "0%", "start")
        handover.update_progress("t1", "75%", "final check")
        t = handover.read()["active_tasks"][0]
        assert t["last_progress"] == "75%"
        assert t["next_checkpoint"] == "final check"

    def test_noop_for_missing_task(self, handover):
        handover.add_task("t1", "T1", 1, "do X", "0%", "start")
        handover.update_progress("ghost", "100%", "done")
        t = handover.read()["active_tasks"][0]
        assert t["last_progress"] == "0%"


class TestGetNextAction:
    def test_returns_highest_priority(self, handover):
        handover.add_task("low", "Low", 9, "", "", "low-cp")
        handover.add_task("top", "Top", 1, "", "", "top-cp")
        result = handover.get_next_action()
        assert result["id"] == "top"
        assert result["next_checkpoint"] == "top-cp"

    def test_returns_empty_dict_when_no_tasks(self, handover):
        assert handover.get_next_action() == {}
