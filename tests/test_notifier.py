"""Tests for core/notifier.py — PFNotifier unified notification routing."""
import os
import sys
import shutil
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notifier import PFNotifier


@pytest.fixture
def data_dir():
    d = tempfile.mkdtemp(prefix="pf_notifier_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def notifier(data_dir):
    return PFNotifier(data_dir=data_dir)


def make_capture():
    """Returns (send_func, captured_list) pair."""
    captured = []
    return lambda text: captured.append(text), captured


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------

class TestInit:
    def test_no_channels_registered_by_default(self, notifier):
        assert notifier.channels == {}

    def test_no_night_silence_by_default(self, notifier):
        assert notifier.night_silence is None

    def test_accepts_config(self, data_dir):
        n = PFNotifier(config={"key": "val"}, data_dir=data_dir)
        assert n.config == {"key": "val"}


# ---------------------------------------------------------------------------
# TestRegisterChannel
# ---------------------------------------------------------------------------

class TestRegisterChannel:
    def test_registers_channel(self, notifier):
        fn, _ = make_capture()
        notifier.register_channel("telegram", fn)
        assert "telegram" in notifier.channels

    def test_registers_multiple_channels(self, notifier):
        fn_tg, _ = make_capture()
        fn_dc, _ = make_capture()
        notifier.register_channel("telegram", fn_tg)
        notifier.register_channel("discord", fn_dc)
        assert len(notifier.channels) == 2

    def test_overwrite_channel(self, notifier):
        fn1, cap1 = make_capture()
        fn2, cap2 = make_capture()
        notifier.register_channel("telegram", fn1)
        notifier.register_channel("telegram", fn2)
        notifier.notify("test")
        assert cap2 == ["test"]
        assert cap1 == []


# ---------------------------------------------------------------------------
# TestSetNightSilence
# ---------------------------------------------------------------------------

class TestSetNightSilence:
    def test_sets_night_silence(self, notifier):
        silence = mock.MagicMock()
        notifier.set_night_silence(silence)
        assert notifier.night_silence is silence


# ---------------------------------------------------------------------------
# TestNotify — channel selection
# ---------------------------------------------------------------------------

class TestNotifyChannelSelection:
    def test_no_channel_returns_no_channel(self, notifier):
        result = notifier.notify("hello")
        assert result == "no_channel"

    def test_uses_telegram_by_default(self, notifier):
        fn_tg, cap_tg = make_capture()
        fn_dc, cap_dc = make_capture()
        notifier.register_channel("telegram", fn_tg)
        notifier.register_channel("discord", fn_dc)
        result = notifier.notify("prefer tg")
        assert result == "sent:telegram"
        assert cap_tg == ["prefer tg"]
        assert cap_dc == []

    def test_falls_back_to_discord_when_no_telegram(self, notifier):
        fn_dc, cap_dc = make_capture()
        notifier.register_channel("discord", fn_dc)
        result = notifier.notify("dc fallback")
        assert result == "sent:discord"
        assert cap_dc == ["dc fallback"]

    def test_falls_back_to_web(self, notifier):
        fn_web, cap_web = make_capture()
        notifier.register_channel("web", fn_web)
        result = notifier.notify("web fallback")
        assert result == "sent:web"
        assert cap_web == ["web fallback"]

    def test_force_specific_channel(self, notifier):
        fn_tg, cap_tg = make_capture()
        fn_dc, cap_dc = make_capture()
        notifier.register_channel("telegram", fn_tg)
        notifier.register_channel("discord", fn_dc)
        result = notifier.notify("force discord", channel="discord")
        assert result == "sent:discord"
        assert cap_dc == ["force discord"]
        assert cap_tg == []

    def test_force_unknown_channel_falls_back_to_default(self, notifier):
        fn_tg, cap_tg = make_capture()
        notifier.register_channel("telegram", fn_tg)
        result = notifier.notify("unknown channel", channel="nonexistent")
        # Falls back to priority order
        assert result == "sent:telegram"

    def test_message_content_delivered(self, notifier):
        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)
        notifier.notify("exact message content")
        assert cap == ["exact message content"]


# ---------------------------------------------------------------------------
# TestNotify — night silence integration
# ---------------------------------------------------------------------------

class TestNotifyNightSilence:
    def test_queued_when_night_silence_active(self, notifier):
        silence = mock.MagicMock()
        silence.send_or_queue.return_value = "queued"
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        result = notifier.notify("quiet hours")
        assert result == "queued"
        assert cap == []  # Not sent to channel

    def test_not_queued_when_urgent(self, notifier):
        silence = mock.MagicMock()
        silence.send_or_queue.return_value = "queued"
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        # urgent=True bypasses night silence check
        result = notifier.notify("emergency", urgent=True)
        assert result == "sent:telegram"
        assert cap == ["emergency"]
        silence.send_or_queue.assert_not_called()

    def test_sent_when_night_silence_allows(self, notifier):
        silence = mock.MagicMock()
        silence.send_or_queue.return_value = "sent"  # not "queued"
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        result = notifier.notify("daytime msg")
        assert result == "sent:telegram"
        assert cap == ["daytime msg"]


# ---------------------------------------------------------------------------
# TestFlushQueue
# ---------------------------------------------------------------------------

class TestFlushQueue:
    def test_returns_empty_when_no_night_silence(self, notifier):
        assert notifier.flush_queue() == []

    def test_returns_empty_when_queue_empty(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = []
        notifier.set_night_silence(silence)
        assert notifier.flush_queue() == []

    def test_sends_queued_messages_via_telegram(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [
            {"text": "msg1", "source": "scheduler"},
            {"text": "msg2", "source": "brain"},
        ]
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        result = notifier.flush_queue()
        assert len(result) == 2
        assert len(cap) == 1  # Combined into one message
        assert "msg1" in cap[0]
        assert "msg2" in cap[0]

    def test_flush_labels_source(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [
            {"text": "hello", "source": "myservice"},
        ]
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        notifier.flush_queue()
        assert "myservice" in cap[0]

    def test_flush_falls_back_to_discord(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [{"text": "x", "source": "s"}]
        notifier.set_night_silence(silence)

        fn_dc, cap_dc = make_capture()
        notifier.register_channel("discord", fn_dc)

        notifier.flush_queue()
        assert len(cap_dc) == 1

    def test_flush_no_channel_does_not_raise(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [{"text": "lost", "source": "x"}]
        notifier.set_night_silence(silence)
        # No channels registered — should not raise
        result = notifier.flush_queue()
        assert len(result) == 1

    def test_flush_returns_queued_items(self, notifier):
        queued = [
            {"text": "a", "source": "s1"},
            {"text": "b", "source": "s2"},
            {"text": "c", "source": "s3"},
        ]
        silence = mock.MagicMock()
        silence.flush.return_value = queued
        notifier.set_night_silence(silence)

        fn, _ = make_capture()
        notifier.register_channel("telegram", fn)

        result = notifier.flush_queue()
        assert result == queued

    def test_flush_count_in_header(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [
            {"text": "a", "source": "x"},
            {"text": "b", "source": "y"},
        ]
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        notifier.flush_queue()
        assert "2" in cap[0]

    def test_flush_missing_source_uses_question_mark(self, notifier):
        silence = mock.MagicMock()
        silence.flush.return_value = [{"text": "no source here"}]
        notifier.set_night_silence(silence)

        fn, cap = make_capture()
        notifier.register_channel("telegram", fn)

        notifier.flush_queue()
        assert "?" in cap[0]
