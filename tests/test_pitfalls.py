"""
Tests for smart/pitfalls.py — PFPitfalls mistake tracker.
"""

import json
import os
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.pitfalls import PFPitfalls


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_test_pitfalls_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def pf(data_dir):
    return PFPitfalls(data_dir=data_dir)


class TestInit:
    def test_data_dir_stored(self, pf, data_dir):
        from pathlib import Path
        assert pf.data_dir == Path(data_dir)

    def test_no_data_dir_uses_default(self):
        p = PFPitfalls()
        assert ".permafrost" in str(p.data_dir)

    def test_pitfalls_file_path(self, pf, data_dir):
        from pathlib import Path
        assert pf.pitfalls_file == Path(data_dir) / "pitfalls.json"


class TestRecord:
    def test_record_single_pitfall(self, pf):
        pf.record("scheduling", "Missed a slot", "Always double-check calendar")
        pitfalls = pf._load()
        assert len(pitfalls) == 1
        p = pitfalls[0]
        assert p["id"] == 1
        assert p["category"] == "scheduling"
        assert p["description"] == "Missed a slot"
        assert p["lesson"] == "Always double-check calendar"
        assert p["severity"] == "medium"
        assert p["recurrence_count"] == 0
        assert "timestamp" in p

    def test_record_custom_severity(self, pf):
        pf.record("decision", "Wrong call", "Think twice", severity="critical")
        pitfalls = pf._load()
        assert pitfalls[0]["severity"] == "critical"

    def test_record_multiple_increments_id(self, pf):
        pf.record("cat1", "desc1", "lesson1")
        pf.record("cat2", "desc2", "lesson2")
        pitfalls = pf._load()
        assert len(pitfalls) == 2
        assert pitfalls[0]["id"] == 1
        assert pitfalls[1]["id"] == 2

    def test_record_persists_to_file(self, pf):
        pf.record("cat", "desc", "lesson")
        data = json.loads(pf.pitfalls_file.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_record_all_severity_levels(self, pf):
        for sev in ["low", "medium", "high", "critical"]:
            pf.record("cat", "desc", "lesson", severity=sev)
        pitfalls = pf._load()
        severities = [p["severity"] for p in pitfalls]
        assert "low" in severities
        assert "critical" in severities


class TestCheckBeforeAction:
    def test_returns_empty_when_no_pitfalls(self, pf):
        result = pf.check_before_action("scheduling")
        assert result == []

    def test_returns_lessons_for_matching_category(self, pf):
        pf.record("scheduling", "desc", "Check calendar first")
        result = pf.check_before_action("scheduling")
        assert "Check calendar first" in result

    def test_ignores_other_categories(self, pf):
        pf.record("decision", "desc", "Think twice")
        result = pf.check_before_action("scheduling")
        assert result == []

    def test_sorting_critical_first(self, pf):
        pf.record("cat", "low desc", "low lesson", severity="low")
        pf.record("cat", "critical desc", "critical lesson", severity="critical")
        pf.record("cat", "high desc", "high lesson", severity="high")
        result = pf.check_before_action("cat")
        # Critical should come first
        assert result[0] == "critical lesson"

    def test_recurrence_affects_order(self, pf):
        pf.record("cat", "desc1", "lesson1", severity="medium")
        pf.record("cat", "desc2", "lesson2", severity="medium")
        # Mark first pitfall as recurring
        pf.mark_recurrence(2)
        pf.mark_recurrence(2)
        result = pf.check_before_action("cat")
        # Higher recurrence should come first (within same severity)
        assert result[0] == "lesson2"

    def test_multiple_lessons_returned(self, pf):
        pf.record("cat", "desc1", "lesson1")
        pf.record("cat", "desc2", "lesson2")
        result = pf.check_before_action("cat")
        assert len(result) == 2


class TestMarkRecurrence:
    def test_increments_recurrence_count(self, pf):
        pf.record("cat", "desc", "lesson")
        pf.mark_recurrence(1)
        pitfalls = pf._load()
        assert pitfalls[0]["recurrence_count"] == 1

    def test_multiple_increments(self, pf):
        pf.record("cat", "desc", "lesson")
        pf.mark_recurrence(1)
        pf.mark_recurrence(1)
        pf.mark_recurrence(1)
        pitfalls = pf._load()
        assert pitfalls[0]["recurrence_count"] == 3

    def test_sets_last_recurrence_timestamp(self, pf):
        pf.record("cat", "desc", "lesson")
        pf.mark_recurrence(1)
        pitfalls = pf._load()
        assert "last_recurrence" in pitfalls[0]

    def test_nonexistent_id_does_nothing(self, pf):
        pf.record("cat", "desc", "lesson")
        pf.mark_recurrence(999)  # ID doesn't exist
        pitfalls = pf._load()
        assert pitfalls[0]["recurrence_count"] == 0

    def test_correct_pitfall_updated(self, pf):
        pf.record("cat", "desc1", "lesson1")
        pf.record("cat", "desc2", "lesson2")
        pf.mark_recurrence(2)
        pitfalls = pf._load()
        assert pitfalls[0]["recurrence_count"] == 0
        assert pitfalls[1]["recurrence_count"] == 1


class TestGetChecklist:
    def test_empty_pitfalls_returns_empty_list(self, pf):
        result = pf.get_checklist()
        assert result == []

    def test_groups_by_category(self, pf):
        pf.record("cat1", "desc", "lesson A")
        pf.record("cat2", "desc", "lesson B")
        checklist = pf.get_checklist()
        categories = {item["category"] for item in checklist}
        assert "cat1" in categories
        assert "cat2" in categories

    def test_deduplicates_lessons(self, pf):
        pf.record("cat", "desc1", "same lesson")
        pf.record("cat", "desc2", "same lesson")
        checklist = pf.get_checklist()
        cat_entry = next(c for c in checklist if c["category"] == "cat")
        assert cat_entry["checks"].count("same lesson") == 1

    def test_caps_at_five_lessons_per_category(self, pf):
        for i in range(10):
            pf.record("cat", f"desc{i}", f"unique lesson {i}")
        checklist = pf.get_checklist()
        cat_entry = next(c for c in checklist if c["category"] == "cat")
        assert len(cat_entry["checks"]) <= 5


class TestGetSummary:
    def test_empty_summary(self, pf):
        summary = pf.get_summary()
        assert summary["total"] == 0
        assert summary["recurring"] == []
        assert summary["recent"] == []

    def test_total_count(self, pf):
        pf.record("cat1", "desc", "lesson")
        pf.record("cat2", "desc", "lesson")
        summary = pf.get_summary()
        assert summary["total"] == 2

    def test_severity_breakdown(self, pf):
        pf.record("cat", "desc", "lesson", severity="critical")
        pf.record("cat", "desc", "lesson", severity="high")
        pf.record("cat", "desc", "lesson", severity="medium")
        pf.record("cat", "desc", "lesson", severity="low")
        summary = pf.get_summary()
        assert summary["by_severity"]["critical"] == 1
        assert summary["by_severity"]["high"] == 1
        assert summary["by_severity"]["medium"] == 1
        assert summary["by_severity"]["low"] == 1

    def test_recurring_pitfalls(self, pf):
        pf.record("cat", "desc", "lesson")
        pf.mark_recurrence(1)
        summary = pf.get_summary()
        assert len(summary["recurring"]) == 1
        assert summary["recurring"][0]["recurrence_count"] == 1

    def test_non_recurring_excluded(self, pf):
        pf.record("cat", "desc", "lesson")
        summary = pf.get_summary()
        assert summary["recurring"] == []

    def test_recent_returns_up_to_five(self, pf):
        for i in range(8):
            pf.record("cat", f"desc{i}", f"lesson{i}")
        summary = pf.get_summary()
        assert len(summary["recent"]) == 5

    def test_recent_is_last_items(self, pf):
        for i in range(6):
            pf.record("cat", f"desc{i}", f"lesson{i}")
        summary = pf.get_summary()
        # Last 5 items: desc1..desc5
        descriptions = [p["description"] for p in summary["recent"]]
        assert "desc5" in descriptions
        assert "desc0" not in descriptions


class TestLoadEmpty:
    def test_missing_file_returns_empty_list(self, pf):
        assert not pf.pitfalls_file.exists()
        result = pf._load()
        assert result == []
