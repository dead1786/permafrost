"""
Tests for core/guard.py — PFContextGuard auto-backup and compaction trigger.
"""

import json
import os
import tempfile
import shutil
import time
import unittest
from pathlib import Path

from conftest import make_temp_dir, cleanup_temp_dir
from core.guard import PFContextGuard


class TestContextGuardInit(unittest.TestCase):
    """Test initialization and config defaults."""

    def setUp(self):
        self.tmp = make_temp_dir()

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_default_thresholds(self):
        guard = PFContextGuard(data_dir=self.tmp)
        self.assertEqual(guard.threshold_pct, 70)
        self.assertEqual(guard.emergency_pct, 90)

    def test_custom_config(self):
        guard = PFContextGuard(
            data_dir=self.tmp,
            config={"threshold_pct": 60, "emergency_pct": 85, "cooldown_seconds": 120},
        )
        self.assertEqual(guard.threshold_pct, 60)
        self.assertEqual(guard.emergency_pct, 85)
        self.assertEqual(guard.cooldown_seconds, 120)

    def test_data_dir_resolved(self):
        guard = PFContextGuard(data_dir=self.tmp)
        self.assertEqual(guard.data_dir, Path(self.tmp))

    def test_no_data_dir_uses_default(self):
        guard = PFContextGuard()
        self.assertIn(".permafrost", str(guard.data_dir))


class TestContextLevelReading(unittest.TestCase):
    """Test _get_context_level() with various file states."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.guard = PFContextGuard(data_dir=self.tmp)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def _write_level(self, data: dict):
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_no_file_returns_zero(self):
        self.assertEqual(self.guard._get_context_level(), 0.0)

    def test_reads_percent_key(self):
        self._write_level({"percent": 55.0})
        self.assertAlmostEqual(self.guard._get_context_level(), 55.0)

    def test_reads_percentage_key_fallback(self):
        self._write_level({"percentage": 72.5})
        self.assertAlmostEqual(self.guard._get_context_level(), 72.5)

    def test_reads_level_key_fallback(self):
        self._write_level({"level": 80.0})
        self.assertAlmostEqual(self.guard._get_context_level(), 80.0)

    def test_corrupt_json_returns_zero(self):
        self.guard.context_file.write_text("not-json", encoding="utf-8")
        self.assertEqual(self.guard._get_context_level(), 0.0)

    def test_missing_key_returns_zero(self):
        self._write_level({"something_else": 99})
        self.assertEqual(self.guard._get_context_level(), 0.0)


class TestShouldTrigger(unittest.TestCase):
    """Test _should_trigger() logic."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.guard = PFContextGuard(
            data_dir=self.tmp,
            config={"threshold_pct": 70, "emergency_pct": 90, "cooldown_seconds": 600},
        )

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def _write_level(self, pct: float):
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump({"percent": pct}, f)

    def test_below_threshold_returns_none(self):
        self._write_level(50.0)
        self.assertIsNone(self.guard._should_trigger())

    def test_at_threshold_returns_normal(self):
        self._write_level(70.0)
        self.assertEqual(self.guard._should_trigger(), "normal")

    def test_above_threshold_returns_normal(self):
        self._write_level(80.0)
        self.assertEqual(self.guard._should_trigger(), "normal")

    def test_at_emergency_returns_emergency(self):
        self._write_level(90.0)
        self.assertEqual(self.guard._should_trigger(), "emergency")

    def test_above_emergency_returns_emergency(self):
        self._write_level(95.0)
        self.assertEqual(self.guard._should_trigger(), "emergency")

    def test_cooldown_suppresses_normal_trigger(self):
        self._write_level(75.0)
        self.guard.last_trigger = time.time()  # just triggered
        self.assertIsNone(self.guard._should_trigger())

    def test_emergency_ignores_cooldown(self):
        # emergency should always fire regardless of cooldown
        self._write_level(91.0)
        self.guard.last_trigger = time.time()
        # emergency_pct=90, so 91 triggers emergency — cooldown is only checked for "normal"
        result = self.guard._should_trigger()
        self.assertEqual(result, "emergency")


class TestTriggerCheckpoint(unittest.TestCase):
    """Test _trigger_checkpoint() writes files correctly."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.guard = PFContextGuard(data_dir=self.tmp)
        # write a context level
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump({"percent": 75.0}, f)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_normal_trigger_writes_checkpoint_file(self):
        self.guard._trigger_checkpoint("normal")
        trigger_file = Path(self.tmp) / "checkpoint-trigger.json"
        self.assertTrue(trigger_file.exists())
        with open(trigger_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["urgency"], "normal")
        self.assertIn("checkpoint", data["actions"])
        self.assertIn("compact", data["actions"])
        self.assertNotIn("emergency_compact", data["actions"])

    def test_emergency_trigger_adds_emergency_action(self):
        self.guard._trigger_checkpoint("emergency")
        trigger_file = Path(self.tmp) / "checkpoint-trigger.json"
        with open(trigger_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("emergency_compact", data["actions"])

    def test_trigger_writes_state_file(self):
        self.guard._trigger_checkpoint("normal")
        self.assertTrue(self.guard.state_file.exists())
        with open(self.guard.state_file, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["urgency"], "normal")
        self.assertEqual(state["trigger_count"], 1)

    def test_trigger_increments_count(self):
        self.guard._trigger_checkpoint("normal")
        self.guard._trigger_checkpoint("normal")
        with open(self.guard.state_file, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["trigger_count"], 2)

    def test_trigger_updates_last_trigger(self):
        before = time.time()
        self.guard._trigger_checkpoint("normal")
        self.assertGreaterEqual(self.guard.last_trigger, before)


class TestCheck(unittest.TestCase):
    """Test check() public method."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.guard = PFContextGuard(data_dir=self.tmp)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_check_returns_none_when_low(self):
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump({"percent": 30.0}, f)
        self.assertIsNone(self.guard.check())

    def test_check_returns_normal_when_threshold(self):
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump({"percent": 75.0}, f)
        self.assertEqual(self.guard.check(), "normal")

    def test_check_returns_emergency_when_critical(self):
        with open(self.guard.context_file, "w", encoding="utf-8") as f:
            json.dump({"percent": 92.0}, f)
        self.assertEqual(self.guard.check(), "emergency")

    def test_check_no_file_returns_none(self):
        self.assertIsNone(self.guard.check())


class TestLoadTriggerCount(unittest.TestCase):
    """Test _load_trigger_count() edge cases."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.guard = PFContextGuard(data_dir=self.tmp)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_no_state_file_returns_zero(self):
        self.assertEqual(self.guard._load_trigger_count(), 0)

    def test_reads_count_from_state(self):
        with open(self.guard.state_file, "w", encoding="utf-8") as f:
            json.dump({"trigger_count": 7}, f)
        self.assertEqual(self.guard._load_trigger_count(), 7)

    def test_corrupt_state_returns_zero(self):
        self.guard.state_file.write_text("bad json", encoding="utf-8")
        self.assertEqual(self.guard._load_trigger_count(), 0)

    def test_missing_trigger_count_key_returns_zero(self):
        with open(self.guard.state_file, "w", encoding="utf-8") as f:
            json.dump({"other": "data"}, f)
        self.assertEqual(self.guard._load_trigger_count(), 0)
