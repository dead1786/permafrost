"""Tests for smart.memory — L1-L6 layered memory system."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.memory import PFMemory


class TestL1Rules:
    def test_ensure_defaults_creates_files(self, data_dir):
        mem = PFMemory(data_dir)
        mem.ensure_defaults()
        assert (mem.memory_dir / "L1" / "rules.md").exists()
        assert (mem.memory_dir / "L1" / "tools.md").exists()
        assert (mem.memory_dir / "L1" / "my_rules.md").exists()

    def test_ensure_defaults_overwrites_framework_rules(self, data_dir):
        mem = PFMemory(data_dir)
        # Write old content
        (mem.memory_dir / "L1" / "rules.md").write_text("old", encoding="utf-8")
        mem.ensure_defaults()
        content = (mem.memory_dir / "L1" / "rules.md").read_text(encoding="utf-8")
        assert content != "old"  # Should be overwritten

    def test_my_rules_not_overwritten(self, data_dir):
        mem = PFMemory(data_dir)
        (mem.memory_dir / "L1" / "my_rules.md").write_text("custom rule", encoding="utf-8")
        mem.ensure_defaults()
        content = (mem.memory_dir / "L1" / "my_rules.md").read_text(encoding="utf-8")
        assert content == "custom rule"  # Should NOT be overwritten

    def test_load_l1(self, data_dir):
        mem = PFMemory(data_dir)
        mem.ensure_defaults()
        text = mem.load_l1()
        assert len(text) > 100  # Should have substantial content


class TestL2:
    def test_save_and_load(self, data_dir):
        mem = PFMemory(data_dir)
        mem.save_l2("test", "test desc", "user", "test content")
        result = mem.load_l2("user_test.md")
        assert result.get("name") == "test"
        assert "test content" in result.get("body", "")

    def test_search(self, data_dir):
        mem = PFMemory(data_dir)
        mem.save_l2("coffee", "likes coffee", "user", "User likes coffee")
        results = mem.search_l2("coffee")
        assert len(results) >= 1

    def test_delete(self, data_dir):
        mem = PFMemory(data_dir)
        mem.save_l2("temp", "temporary", "reference", "temp content")
        assert mem.delete_l2("reference_temp.md")
        assert not mem.load_l2("reference_temp.md")

    def test_list_by_type(self, data_dir):
        mem = PFMemory(data_dir)
        mem.save_l2("a", "desc", "user", "content a")
        mem.save_l2("b", "desc", "feedback", "content b")
        users = mem.list_l2("user")
        assert len(users) >= 1


class TestL3:
    def test_add_and_search(self, data_dir):
        mem = PFMemory(data_dir)
        mem.add_l3("test_key", "test value", "context", 3)
        results = mem.search_l3("test")
        assert len(results) >= 1

    def test_ttl_types(self, data_dir):
        mem = PFMemory(data_dir)
        mem.add_l3("ctx", "val", "context")
        mem.add_l3("pref", "val", "preference")
        mem.add_l3("prog", "val", "progress")
        mem.add_l3("ins", "val", "insight")
        entries = mem.list_l3()
        assert len(entries) == 4

    def test_update_existing(self, data_dir):
        mem = PFMemory(data_dir)
        mem.add_l3("key1", "first", "context")
        mem.add_l3("key1", "second", "context")
        entries = mem.list_l3()
        assert len(entries) == 1
        assert entries[0]["value"] == "second"


class TestGC:
    def test_gc_runs(self, data_dir):
        mem = PFMemory(data_dir)
        mem.add_l3("key1", "val1", "context", 3)
        result = mem.gc()
        assert "kept" in result
        assert "promoted" in result
        assert "archived" in result


class TestContextBlock:
    def test_returns_string(self, data_dir):
        mem = PFMemory(data_dir)
        mem.save_l2("test", "desc", "user", "content")
        mem.add_l3("key", "val", "context")
        block = mem.get_context_block()
        assert isinstance(block, str)
        assert len(block) > 0


class TestStats:
    def test_all_layers(self, data_dir):
        mem = PFMemory(data_dir)
        mem.ensure_defaults()
        stats = mem.get_stats()
        assert "L1" in stats
        assert "L2" in stats
        assert "L3" in stats
        assert "L4" in stats
        assert "L5" in stats
        assert "L6" in stats
