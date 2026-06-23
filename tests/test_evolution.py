"""
Tests for smart/evolution.py — EvolutionEngine self-improvement queue.
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.evolution import EvolutionEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def evo(tmp_path):
    """EvolutionEngine with a fresh temp data dir."""
    return EvolutionEngine(str(tmp_path))


# ── load_queue ────────────────────────────────────────────────────────────────


class TestLoadQueue:
    def test_returns_default_when_no_file(self, evo):
        q = evo.load_queue()
        assert q == {"items": [], "completed_count": 0}

    def test_returns_existing_data(self, evo):
        data = {"items": [{"id": "evo-001", "status": "queued"}], "completed_count": 0}
        evo.queue_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = evo.load_queue()
        assert len(loaded["items"]) == 1
        assert loaded["items"][0]["id"] == "evo-001"


# ── save_queue ────────────────────────────────────────────────────────────────


class TestSaveQueue:
    def test_saves_to_file(self, evo):
        q = {"items": [], "completed_count": 0}
        evo.save_queue(q)
        assert evo.queue_file.exists()

    def test_sets_last_updated(self, evo):
        q = {"items": [], "completed_count": 0}
        evo.save_queue(q)
        saved = json.loads(evo.queue_file.read_text(encoding="utf-8"))
        assert "last_updated" in saved

    def test_roundtrip(self, evo):
        q = {"items": [{"id": "evo-001", "title": "Test"}], "completed_count": 0}
        evo.save_queue(q)
        loaded = evo.load_queue()
        assert loaded["items"][0]["id"] == "evo-001"


# ── add_item ──────────────────────────────────────────────────────────────────


class TestAddItem:
    def test_returns_new_id(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        assert eid.startswith("evo-")

    def test_id_increments(self, evo):
        id1 = evo.add_item("A", "why", "exp")
        id2 = evo.add_item("B", "why", "exp")
        num1 = int(id1.split("-")[1])
        num2 = int(id2.split("-")[1])
        assert num2 == num1 + 1

    def test_item_has_queued_status(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert item["status"] == "queued"

    def test_item_stores_fields(self, evo):
        eid = evo.add_item("My Title", "My Why", "My Expected", priority=3)
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert item["title"] == "My Title"
        assert item["why"] == "My Why"
        assert item["expected"] == "My Expected"
        assert item["priority"] == 3

    def test_default_priority_is_1(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert item["priority"] == 1

    def test_item_has_created_date(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert "created" in item

    def test_multiple_items_accumulate(self, evo):
        evo.add_item("A", "why", "exp")
        evo.add_item("B", "why", "exp")
        evo.add_item("C", "why", "exp")
        q = evo.load_queue()
        assert len(q["items"]) == 3


# ── get_next ──────────────────────────────────────────────────────────────────


class TestGetNext:
    def test_returns_none_when_empty(self, evo):
        assert evo.get_next() is None

    def test_returns_in_progress_first(self, evo):
        evo.add_item("Queued", "why", "exp")
        q = evo.load_queue()
        q["items"].append({
            "id": "evo-999", "title": "InProgress", "why": "w",
            "expected": "e", "status": "in_progress", "priority": 5,
            "created": "2024-01-01",
        })
        evo.save_queue(q)
        nxt = evo.get_next()
        assert nxt["id"] == "evo-999"

    def test_returns_lowest_priority_queued(self, evo):
        evo.add_item("Low", "why", "exp", priority=3)
        evo.add_item("High", "why", "exp", priority=1)
        nxt = evo.get_next()
        assert nxt["title"] == "High"

    def test_returns_none_when_all_done(self, evo):
        evo.add_item("Done", "why", "exp")
        evo.mark_done(evo.get_next()["id"])
        assert evo.get_next() is None


# ── mark_done ─────────────────────────────────────────────────────────────────


class TestMarkDone:
    def test_marks_item_done(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        evo.mark_done(eid, "success")
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert item["status"] == "done"

    def test_sets_completed_date(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        evo.mark_done(eid)
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert "completed" in item

    def test_stores_result(self, evo):
        eid = evo.add_item("Title", "Why", "Expected")
        evo.mark_done(eid, result="fixed it")
        q = evo.load_queue()
        item = next(i for i in q["items"] if i["id"] == eid)
        assert item["result"] == "fixed it"

    def test_increments_completed_count(self, evo):
        eid = evo.add_item("T", "w", "e")
        evo.mark_done(eid)
        q = evo.load_queue()
        assert q["completed_count"] == 1

    def test_mark_nonexistent_id_no_error(self, evo):
        # Should not raise
        evo.mark_done("evo-999")

    def test_only_target_item_changed(self, evo):
        id1 = evo.add_item("A", "w", "e")
        id2 = evo.add_item("B", "w", "e")
        evo.mark_done(id1)
        q = evo.load_queue()
        item2 = next(i for i in q["items"] if i["id"] == id2)
        assert item2["status"] == "queued"


# ── get_stats ─────────────────────────────────────────────────────────────────


class TestGetStats:
    def test_empty_queue_stats(self, evo):
        stats = evo.get_stats()
        assert stats == {"queued": 0, "in_progress": 0, "done": 0, "total": 0}

    def test_counts_by_status(self, evo):
        id1 = evo.add_item("A", "w", "e")
        evo.add_item("B", "w", "e")
        evo.mark_done(id1)
        stats = evo.get_stats()
        assert stats["queued"] == 1
        assert stats["done"] == 1
        assert stats["total"] == 2
        assert stats["in_progress"] == 0

    def test_in_progress_counted(self, evo):
        q = {"items": [
            {"id": "evo-001", "status": "in_progress", "priority": 1},
        ], "completed_count": 0}
        evo.save_queue(q)
        stats = evo.get_stats()
        assert stats["in_progress"] == 1


# ── log_pitfall ───────────────────────────────────────────────────────────────


class TestLogPitfall:
    def test_creates_pitfalls_file(self, evo):
        evo.log_pitfall("something failed")
        assert evo.pitfalls_file.exists()

    def test_appends_pitfall(self, evo):
        evo.log_pitfall("issue A")
        evo.log_pitfall("issue B")
        data = json.loads(evo.pitfalls_file.read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_pitfall_has_expected_fields(self, evo):
        evo.log_pitfall("test issue", category="logic")
        data = json.loads(evo.pitfalls_file.read_text(encoding="utf-8"))
        p = data[0]
        assert p["description"] == "test issue"
        assert p["category"] == "logic"
        assert "timestamp" in p
        assert p["converted_to_plan"] is False

    def test_default_category_is_general(self, evo):
        evo.log_pitfall("generic issue")
        data = json.loads(evo.pitfalls_file.read_text(encoding="utf-8"))
        assert data[0]["category"] == "general"

    def test_recovers_from_corrupt_pitfalls_file(self, evo):
        evo.pitfalls_file.write_text("not json", encoding="utf-8")
        # Should not raise
        evo.log_pitfall("after corruption")
        data = json.loads(evo.pitfalls_file.read_text(encoding="utf-8"))
        assert len(data) == 1


# ── convert_pitfalls_to_plans ─────────────────────────────────────────────────


class TestConvertPitfallsToPlans:
    def test_returns_0_when_no_file(self, evo):
        count = evo.convert_pitfalls_to_plans()
        assert count == 0

    def test_converts_unconverted_pitfalls(self, evo):
        evo.log_pitfall("bug A")
        evo.log_pitfall("bug B")
        count = evo.convert_pitfalls_to_plans()
        assert count == 2

    def test_creates_evolution_items_for_each(self, evo):
        evo.log_pitfall("bug A")
        evo.log_pitfall("bug B")
        evo.convert_pitfalls_to_plans()
        q = evo.load_queue()
        assert len(q["items"]) == 2

    def test_marks_pitfalls_as_converted(self, evo):
        evo.log_pitfall("bug A")
        evo.convert_pitfalls_to_plans()
        data = json.loads(evo.pitfalls_file.read_text(encoding="utf-8"))
        assert data[0]["converted_to_plan"] is True

    def test_already_converted_pitfalls_skipped(self, evo):
        evo.log_pitfall("bug A")
        evo.convert_pitfalls_to_plans()
        # Run again — should not create duplicate
        count2 = evo.convert_pitfalls_to_plans()
        assert count2 == 0
        q = evo.load_queue()
        assert len(q["items"]) == 1

    def test_mixed_converted_and_not(self, evo):
        evo.log_pitfall("bug A")
        evo.convert_pitfalls_to_plans()  # converts bug A
        evo.log_pitfall("bug B")
        count = evo.convert_pitfalls_to_plans()
        assert count == 1
        q = evo.load_queue()
        assert len(q["items"]) == 2

    def test_title_truncated_to_50_chars(self, evo):
        long_desc = "x" * 100
        evo.log_pitfall(long_desc)
        evo.convert_pitfalls_to_plans()
        q = evo.load_queue()
        title = q["items"][0]["title"]
        # "Fix: " + 50 chars = 55 max
        assert len(title) <= 60
