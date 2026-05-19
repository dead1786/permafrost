"""
Tests for smart/night_silence.py — PFNightSilence notification queuing.
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.night_silence import PFNightSilence


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_test_night_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def silence(data_dir):
    return PFNightSilence(data_dir=data_dir, config={
        "night_start": "00:00",
        "night_end": "08:00",
        "flush_time": "08:05",
    })


class TestInit:
    def test_defaults(self, data_dir):
        ns = PFNightSilence(data_dir=data_dir)
        assert ns.silence_start == "00:00"
        assert ns.silence_end == "08:00"
        assert ns.flush_time == "08:05"

    def test_custom_config(self, data_dir):
        ns = PFNightSilence(data_dir=data_dir, config={
            "night_start": "23:00",
            "night_end": "07:30",
            "flush_time": "07:35",
        })
        assert ns.silence_start == "23:00"
        assert ns.silence_end == "07:30"
        assert ns.flush_time == "07:35"

    def test_queue_file_path(self, data_dir):
        ns = PFNightSilence(data_dir=data_dir)
        assert ns.queue_file == Path(data_dir) / "notify-queue.json"

    def test_no_data_dir_uses_default(self):
        ns = PFNightSilence()
        assert ".permafrost" in str(ns.data_dir)


class TestParseTime:
    def test_parse_midnight(self, silence):
        assert silence._parse_time("00:00") == (0, 0)

    def test_parse_hour_minute(self, silence):
        assert silence._parse_time("08:05") == (8, 5)

    def test_parse_late_night(self, silence):
        assert silence._parse_time("23:59") == (23, 59)


class TestIsSilent:
    def test_silent_during_night(self, silence):
        # 03:00 is within 00:00–08:00
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
            assert silence.is_silent() is True

    def test_not_silent_during_day(self, silence):
        # 12:00 is outside 00:00–08:00
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0)
            assert silence.is_silent() is False

    def test_boundary_start(self, silence):
        # Exactly 00:00 — start of silence
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 0, 0)
            assert silence.is_silent() is True

    def test_boundary_end(self, silence):
        # Exactly 08:00 — end of silence (exclusive)
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 8, 0)
            assert silence.is_silent() is False

    def test_midnight_wrap(self, data_dir):
        # 23:00–06:00 spans midnight
        ns = PFNightSilence(data_dir=data_dir, config={
            "night_start": "23:00",
            "night_end": "06:00",
        })
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 23, 30)
            assert ns.is_silent() is True

        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 2, 0)
            assert ns.is_silent() is True

        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 10, 0)
            assert ns.is_silent() is False

    def test_one_minute_before_end(self, silence):
        # 07:59 should still be silent
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 7, 59)
            assert silence.is_silent() is True


class TestShouldFlush:
    def test_flush_at_flush_time(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 8, 5)
            assert silence.should_flush() is True

    def test_no_flush_at_other_time(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 9, 0)
            assert silence.should_flush() is False

    def test_no_flush_same_hour_different_minute(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 8, 6)
            assert silence.should_flush() is False


class TestQueue:
    def test_queue_single_message(self, silence):
        silence.queue("hello", source="test")
        q = silence._load_queue()
        assert len(q) == 1
        assert q[0]["text"] == "hello"
        assert q[0]["source"] == "test"
        assert "queued_at" in q[0]

    def test_queue_multiple_messages(self, silence):
        silence.queue("msg1", source="a")
        silence.queue("msg2", source="b")
        q = silence._load_queue()
        assert len(q) == 2
        assert q[0]["text"] == "msg1"
        assert q[1]["text"] == "msg2"

    def test_queue_persists_across_instances(self, data_dir):
        ns1 = PFNightSilence(data_dir=data_dir)
        ns1.queue("persistent message", source="src")
        ns2 = PFNightSilence(data_dir=data_dir)
        q = ns2._load_queue()
        assert len(q) == 1
        assert q[0]["text"] == "persistent message"

    def test_queue_default_source(self, silence):
        silence.queue("msg")
        q = silence._load_queue()
        assert q[0]["source"] == "system"


class TestFlush:
    def test_flush_returns_messages(self, silence):
        silence.queue("a")
        silence.queue("b")
        result = silence.flush()
        assert len(result) == 2
        assert result[0]["text"] == "a"

    def test_flush_clears_queue(self, silence):
        silence.queue("a")
        silence.flush()
        assert silence.get_queue_count() == 0

    def test_flush_empty_queue(self, silence):
        result = silence.flush()
        assert result == []


class TestSendOrQueue:
    def test_queues_during_silence(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
            result = silence.send_or_queue("night msg", urgent=False)
        assert result == "queued"
        assert silence.get_queue_count() == 1

    def test_sends_outside_silence(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0)
            result = silence.send_or_queue("day msg", urgent=False)
        assert result == "send"
        assert silence.get_queue_count() == 0

    def test_urgent_bypasses_silence(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
            result = silence.send_or_queue("urgent!", urgent=True)
        assert result == "send"
        assert silence.get_queue_count() == 0

    def test_default_not_urgent(self, silence):
        with mock.patch("smart.night_silence.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 1, 1, 3, 0)
            result = silence.send_or_queue("msg")
        assert result == "queued"


class TestGetQueueCount:
    def test_empty_queue(self, silence):
        assert silence.get_queue_count() == 0

    def test_count_after_enqueue(self, silence):
        silence.queue("a")
        silence.queue("b")
        assert silence.get_queue_count() == 2

    def test_count_after_flush(self, silence):
        silence.queue("a")
        silence.flush()
        assert silence.get_queue_count() == 0


class TestLoadQueueCorrupted:
    def test_corrupted_queue_file_returns_empty(self, data_dir):
        ns = PFNightSilence(data_dir=data_dir)
        ns.queue_file.write_text("not valid json", encoding="utf-8")
        q = ns._load_queue()
        assert q == []

    def test_missing_queue_file_returns_empty(self, data_dir):
        ns = PFNightSilence(data_dir=data_dir)
        # queue_file doesn't exist yet
        assert not ns.queue_file.exists()
        q = ns._load_queue()
        assert q == []
