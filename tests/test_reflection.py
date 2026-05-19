"""
Tests for smart/reflection.py — PFReflection nightly self-improvement loop.
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.reflection import PFReflection


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_test_reflect_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def reflection(data_dir):
    return PFReflection(data_dir=data_dir)


def write_reflection(reflection_obj: PFReflection, day_offset: int = 0, **kwargs):
    """Helper: write a reflection file for a specific date offset from today."""
    target_date = date.today() - timedelta(days=day_offset)
    defaults = {
        "went_well": ["Task A completed"],
        "went_wrong": ["Missed deadline"],
        "patterns": ["Procrastination"],
        "actions": ["Start earlier tomorrow"],
        "score": {"overall": "7/10"},
    }
    defaults.update(kwargs)
    filepath = reflection_obj.reflections_dir / f"{target_date.isoformat()}.json"
    data = {
        "date": target_date.isoformat(),
        "timestamp": f"{target_date.isoformat()}T23:00:00",
        "went_well": defaults["went_well"],
        "went_wrong": defaults["went_wrong"],
        "patterns_detected": defaults["patterns"],
        "action_items": defaults["actions"],
        "score": defaults["score"],
    }
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return filepath


class TestInit:
    def test_reflections_dir_created(self, reflection, data_dir):
        assert (Path(data_dir) / "reflections").exists()

    def test_no_data_dir_uses_default(self):
        r = PFReflection()
        assert ".permafrost" in str(r.data_dir)

    def test_reflections_subdir(self, reflection, data_dir):
        assert reflection.reflections_dir == Path(data_dir) / "reflections"


class TestCreate:
    def test_creates_file_for_today(self, reflection):
        filepath = reflection.create(
            went_well=["Good thing"],
            went_wrong=["Bad thing"],
            patterns=["Pattern"],
            actions=["Action"],
        )
        assert os.path.exists(filepath)
        today = date.today().isoformat()
        assert today in filepath

    def test_file_content_correct(self, reflection):
        reflection.create(
            went_well=["W1", "W2"],
            went_wrong=["X1"],
            patterns=["P1"],
            actions=["A1"],
            score={"overall": "8/10"},
        )
        today = date.today().isoformat()
        filepath = reflection.reflections_dir / f"{today}.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["went_well"] == ["W1", "W2"]
        assert data["went_wrong"] == ["X1"]
        assert data["patterns_detected"] == ["P1"]
        assert data["action_items"] == ["A1"]
        assert data["score"] == {"overall": "8/10"}
        assert data["date"] == today
        assert "timestamp" in data

    def test_no_score_defaults_empty_dict(self, reflection):
        reflection.create(["w"], ["x"], ["p"], ["a"])
        today = date.today().isoformat()
        filepath = reflection.reflections_dir / f"{today}.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["score"] == {}

    def test_overwrite_existing_reflection(self, reflection):
        reflection.create(["first"], [], [], [])
        reflection.create(["second"], [], [], [])
        today = date.today().isoformat()
        filepath = reflection.reflections_dir / f"{today}.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["went_well"] == ["second"]


class TestGetRecent:
    def test_empty_when_no_reflections(self, reflection):
        result = reflection.get_recent()
        assert result == []

    def test_returns_most_recent_first(self, reflection):
        write_reflection(reflection, day_offset=0)
        write_reflection(reflection, day_offset=1)
        write_reflection(reflection, day_offset=2)
        result = reflection.get_recent(days=3)
        assert len(result) == 3
        # Files are sorted in reverse order (newest first)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates, reverse=True)

    def test_respects_days_limit(self, reflection):
        for i in range(5):
            write_reflection(reflection, day_offset=i)
        result = reflection.get_recent(days=3)
        assert len(result) == 3

    def test_default_days_is_seven(self, reflection):
        for i in range(10):
            write_reflection(reflection, day_offset=i)
        result = reflection.get_recent()
        assert len(result) == 7

    def test_skips_corrupted_files(self, reflection):
        write_reflection(reflection, day_offset=0)
        # Inject a corrupted file
        bad_file = reflection.reflections_dir / "2000-01-01.json"
        bad_file.write_text("NOT JSON", encoding="utf-8")
        result = reflection.get_recent(days=10)
        # Should return at least the good one
        assert len(result) >= 1
        assert all("date" in r for r in result)


class TestAnalyzeTrends:
    def test_no_reflections_returns_message(self, reflection):
        result = reflection.analyze_trends()
        assert "message" in result
        assert "No reflections" in result["message"]

    def test_days_analyzed(self, reflection):
        write_reflection(reflection, day_offset=0)
        write_reflection(reflection, day_offset=1)
        result = reflection.analyze_trends(days=7)
        assert result["days_analyzed"] == 2

    def test_detects_recurring_patterns(self, reflection):
        write_reflection(reflection, day_offset=0, patterns=["Procrastination"])
        write_reflection(reflection, day_offset=1, patterns=["Procrastination"])
        result = reflection.analyze_trends(days=7)
        # Should detect "procrastination" as recurring (>= 2 occurrences)
        recurring = result["recurring_patterns"]
        assert any("procrastination" in k for k in recurring)

    def test_non_recurring_pattern_not_included(self, reflection):
        write_reflection(reflection, day_offset=0, patterns=["Unique thing"])
        result = reflection.analyze_trends(days=7)
        recurring = result["recurring_patterns"]
        # "unique thing" only occurs once — should not appear
        assert not any("unique thing" in k for k in recurring)

    def test_total_issues_counted(self, reflection):
        write_reflection(reflection, day_offset=0, went_wrong=["A", "B"])
        write_reflection(reflection, day_offset=1, went_wrong=["C"])
        result = reflection.analyze_trends(days=7)
        assert result["total_issues"] == 3

    def test_avg_score_calculated(self, reflection):
        write_reflection(reflection, day_offset=0, score={"overall": "8/10"})
        write_reflection(reflection, day_offset=1, score={"overall": "6/10"})
        result = reflection.analyze_trends(days=7)
        assert result["avg_score"] == pytest.approx(7.0)

    def test_no_scores_avg_is_none(self, reflection):
        write_reflection(reflection, day_offset=0, score={})
        result = reflection.analyze_trends(days=7)
        assert result["avg_score"] is None

    def test_score_trend_improving(self, reflection):
        # Newest first when returned: today=high score, yesterday=low score
        write_reflection(reflection, day_offset=0, score={"overall": "9/10"})
        write_reflection(reflection, day_offset=1, score={"overall": "5/10"})
        result = reflection.analyze_trends(days=7)
        assert result["score_trend"] == "improving"

    def test_score_trend_declining(self, reflection):
        write_reflection(reflection, day_offset=0, score={"overall": "4/10"})
        write_reflection(reflection, day_offset=1, score={"overall": "9/10"})
        result = reflection.analyze_trends(days=7)
        assert result["score_trend"] == "declining"

    def test_score_trend_stable_single_entry(self, reflection):
        write_reflection(reflection, day_offset=0, score={"overall": "7/10"})
        result = reflection.analyze_trends(days=7)
        assert result["score_trend"] == "stable"


class TestGetFollowUpItems:
    def test_empty_when_no_reflections(self, reflection):
        result = reflection.get_follow_up_items()
        assert result == []

    def test_returns_action_items(self, reflection):
        write_reflection(reflection, day_offset=0, actions=["Do X", "Do Y"])
        result = reflection.get_follow_up_items()
        assert len(result) == 2
        actions = [item["action"] for item in result]
        assert "Do X" in actions
        assert "Do Y" in actions

    def test_includes_date(self, reflection):
        write_reflection(reflection, day_offset=0, actions=["Action A"])
        result = reflection.get_follow_up_items()
        assert "date" in result[0]

    def test_collects_from_multiple_days(self, reflection):
        write_reflection(reflection, day_offset=0, actions=["Action today"])
        write_reflection(reflection, day_offset=1, actions=["Action yesterday"])
        result = reflection.get_follow_up_items()
        assert len(result) == 2

    def test_only_reads_three_most_recent_files(self, reflection):
        # get_follow_up_items calls get_recent(3), meaning the 3 newest files.
        # With 4 reflection files, the oldest (day_offset=3) should be excluded.
        for i in range(4):
            write_reflection(reflection, day_offset=i, actions=[f"Action day -{i}"])
        result = reflection.get_follow_up_items()
        actions = [item["action"] for item in result]
        # 3 most recent: day -0, -1, -2 should be included
        assert "Action day -0" in actions
        assert "Action day -1" in actions
        assert "Action day -2" in actions
        # 4th oldest file (day -3) should be excluded
        assert "Action day -3" not in actions
