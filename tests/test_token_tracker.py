"""
Tests for core/token_tracker.py — token usage recording and cost estimation.
"""

import json
import os
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from conftest import make_temp_dir, cleanup_temp_dir


class TestTokenTrackerCostEstimation(unittest.TestCase):
    """Test _estimate_cost internals."""

    def setUp(self):
        self.tmp = make_temp_dir()
        # Patch DATA_DIR so we don't touch real ~/.permafrost
        self._dir_patcher = patch("core.token_tracker.DATA_DIR", Path(self.tmp))
        self._file_patcher = patch(
            "core.token_tracker.USAGE_FILE", Path(self.tmp) / "token-usage.json"
        )
        self._dir_patcher.start()
        self._file_patcher.start()

    def tearDown(self):
        self._dir_patcher.stop()
        self._file_patcher.stop()
        cleanup_temp_dir(self.tmp)

    def test_known_model_cost(self):
        from core.token_tracker import _estimate_cost

        cost = _estimate_cost(1_000_000, 1_000_000, "claude-sonnet-4-20250514")
        # prompt: 3.0 USD + completion: 15.0 USD = 18.0 USD
        self.assertAlmostEqual(cost, 18.0, places=4)

    def test_unknown_model_returns_zero(self):
        from core.token_tracker import _estimate_cost

        cost = _estimate_cost(500, 500, "unknown-model-xyz")
        self.assertEqual(cost, 0.0)

    def test_partial_model_name_match(self):
        from core.token_tracker import _estimate_cost

        # "claude-sonnet-4" should match "claude-sonnet-4-20250514"
        cost = _estimate_cost(1_000_000, 0, "claude-sonnet-4")
        self.assertGreater(cost, 0.0)

    def test_zero_tokens_cost_is_zero(self):
        from core.token_tracker import _estimate_cost

        cost = _estimate_cost(0, 0, "gpt-4o")
        self.assertEqual(cost, 0.0)

    def test_openai_model_cost(self):
        from core.token_tracker import _estimate_cost

        # gpt-4o: prompt 2.5/1M, completion 10.0/1M
        cost = _estimate_cost(1_000_000, 1_000_000, "gpt-4o")
        self.assertAlmostEqual(cost, 12.5, places=4)

    def test_gemini_flash_cost(self):
        from core.token_tracker import _estimate_cost

        cost = _estimate_cost(1_000_000, 0, "gemini-2.0-flash")
        self.assertAlmostEqual(cost, 0.075, places=4)


class TestTokenTrackerTrackUsage(unittest.TestCase):
    """Test track_usage() and persistence."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self._dir_patcher = patch("core.token_tracker.DATA_DIR", Path(self.tmp))
        self._file_patcher = patch(
            "core.token_tracker.USAGE_FILE", Path(self.tmp) / "token-usage.json"
        )
        self._dir_patcher.start()
        self._file_patcher.start()

    def tearDown(self):
        self._dir_patcher.stop()
        self._file_patcher.stop()
        cleanup_temp_dir(self.tmp)

    def _usage_file(self):
        return Path(self.tmp) / "token-usage.json"

    def test_track_creates_file(self):
        from core.token_tracker import track_usage

        track_usage(100, 50, "gpt-4o")
        self.assertTrue(self._usage_file().exists())

    def test_track_accumulates_tokens(self):
        from core.token_tracker import track_usage, get_usage_summary

        track_usage(100, 50, "gpt-4o")
        track_usage(200, 80, "gpt-4o")
        summary = get_usage_summary()
        self.assertEqual(summary["total_prompt_tokens"], 300)
        self.assertEqual(summary["total_completion_tokens"], 130)

    def test_track_accumulates_cost(self):
        from core.token_tracker import track_usage, get_usage_summary

        track_usage(1_000_000, 0, "gpt-4o")  # $2.50
        track_usage(0, 1_000_000, "gpt-4o")  # $10.00
        summary = get_usage_summary()
        self.assertAlmostEqual(summary["total_cost_usd"], 12.5, places=4)

    def test_track_skips_all_zero_tokens(self):
        from core.token_tracker import track_usage

        track_usage(0, 0, "gpt-4o")
        self.assertFalse(self._usage_file().exists())

    def test_track_records_daily_breakdown(self):
        from core.token_tracker import track_usage, get_usage_summary
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        track_usage(100, 50, "gpt-4o")
        summary = get_usage_summary()
        self.assertIn(today, summary["daily"])
        daily = summary["daily"][today]
        self.assertEqual(daily["prompt"], 100)
        self.assertEqual(daily["completion"], 50)
        self.assertEqual(daily["calls"], 1)

    def test_track_daily_call_count_increments(self):
        from core.token_tracker import track_usage, get_usage_summary
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        for _ in range(3):
            track_usage(10, 5, "gpt-4o")
        summary = get_usage_summary()
        self.assertEqual(summary["daily"][today]["calls"], 3)

    def test_track_unknown_model_zero_cost(self):
        from core.token_tracker import track_usage, get_usage_summary

        track_usage(500, 200, "mystery-model")
        summary = get_usage_summary()
        self.assertEqual(summary["total_cost_usd"], 0.0)
        # But tokens are still counted
        self.assertEqual(summary["total_prompt_tokens"], 500)


class TestTokenTrackerGetTodayUsage(unittest.TestCase):
    """Test get_today_usage()."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self._dir_patcher = patch("core.token_tracker.DATA_DIR", Path(self.tmp))
        self._file_patcher = patch(
            "core.token_tracker.USAGE_FILE", Path(self.tmp) / "token-usage.json"
        )
        self._dir_patcher.start()
        self._file_patcher.start()

    def tearDown(self):
        self._dir_patcher.stop()
        self._file_patcher.stop()
        cleanup_temp_dir(self.tmp)

    def test_today_empty_when_no_data(self):
        from core.token_tracker import get_today_usage

        result = get_today_usage()
        self.assertEqual(result["prompt"], 0)
        self.assertEqual(result["completion"], 0)
        self.assertEqual(result["calls"], 0)

    def test_today_reflects_current_calls(self):
        from core.token_tracker import track_usage, get_today_usage

        track_usage(300, 150, "claude-haiku-4-5-20251001")
        result = get_today_usage()
        self.assertEqual(result["prompt"], 300)
        self.assertEqual(result["completion"], 150)
        self.assertEqual(result["calls"], 1)


class TestTokenTrackerLoadCorruptFile(unittest.TestCase):
    """Test robustness against corrupt/missing usage file."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self._dir_patcher = patch("core.token_tracker.DATA_DIR", Path(self.tmp))
        usage_path = Path(self.tmp) / "token-usage.json"
        self._file_patcher = patch("core.token_tracker.USAGE_FILE", usage_path)
        self._dir_patcher.start()
        self._file_patcher.start()
        self._usage_path = usage_path

    def tearDown(self):
        self._dir_patcher.stop()
        self._file_patcher.stop()
        cleanup_temp_dir(self.tmp)

    def test_corrupt_json_returns_empty(self):
        self._usage_path.write_text("not-valid-json", encoding="utf-8")
        from core.token_tracker import get_usage_summary

        result = get_usage_summary()
        self.assertEqual(result["total_prompt_tokens"], 0)

    def test_partial_keys_filled_in(self):
        # Write a file missing some keys — should be backfilled
        self._usage_path.write_text(
            json.dumps({"total_prompt_tokens": 99}), encoding="utf-8"
        )
        from core.token_tracker import get_usage_summary

        result = get_usage_summary()
        self.assertEqual(result["total_prompt_tokens"], 99)
        self.assertIn("total_completion_tokens", result)
        self.assertIn("daily", result)

    def test_track_after_corrupt_file_recovers(self):
        self._usage_path.write_text("{bad json", encoding="utf-8")
        from core.token_tracker import track_usage, get_usage_summary

        track_usage(50, 25, "gpt-4o")
        result = get_usage_summary()
        self.assertEqual(result["total_prompt_tokens"], 50)
