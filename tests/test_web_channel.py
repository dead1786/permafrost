"""
Tests for channels/web.py — PFWeb (built-in inbox/outbox web chat channel).

The web channel had zero dedicated test coverage (44%) despite being the
always-on, zero-config channel every console/CLI install depends on.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from conftest import make_temp_dir, cleanup_temp_dir, read_json
from channels.base import create_channel, list_channels
from channels.web import PFWeb


class TestWebRegistry(unittest.TestCase):
    """Test web channel factory registration."""

    def setUp(self):
        self.tmp = make_temp_dir()

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_listed_in_channels(self):
        names = [c["name"] for c in list_channels()]
        self.assertIn("web", names)

    def test_create_via_factory(self):
        # Explicit data_dir — omitting it defaults to ~/.permafrost and would
        # create/mutate a real directory in the developer's home folder.
        ch = create_channel("web", {}, data_dir=self.tmp)
        self.assertIsInstance(ch, PFWeb)

    def test_no_config_fields_required(self):
        self.assertEqual(PFWeb.CONFIG_FIELDS, [])


class TestWebValidation(unittest.TestCase):
    """Web channel requires no configuration — always valid."""

    def setUp(self):
        self.tmp = make_temp_dir()

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_validate_always_true(self):
        web = PFWeb(config={}, data_dir=self.tmp)
        ok, err = web.validate()
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_name(self):
        web = PFWeb(config={}, data_dir=self.tmp)
        self.assertEqual(web.name, "web")


class TestWebSendMessage(unittest.TestCase):
    """Test writing replies to web-outbox.json."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.web = PFWeb(config={}, data_dir=self.tmp)
        self.outbox_file = Path(self.tmp) / "web-outbox.json"

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_creates_outbox_file(self):
        result = self.web.send_message("hello")
        self.assertTrue(result)
        self.assertTrue(self.outbox_file.exists())

    def test_entry_contents(self):
        self.web.send_message("hello there")
        outbox = read_json(str(self.outbox_file))
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["text"], "hello there")
        self.assertFalse(outbox[0]["read"])
        self.assertIn("timestamp", outbox[0])

    def test_appends_to_existing_outbox(self):
        self.web.send_message("first")
        self.web.send_message("second")
        outbox = read_json(str(self.outbox_file))
        self.assertEqual(len(outbox), 2)
        self.assertEqual(outbox[0]["text"], "first")
        self.assertEqual(outbox[1]["text"], "second")

    def test_keeps_only_last_100(self):
        for i in range(105):
            self.web.send_message(f"msg-{i}")
        outbox = read_json(str(self.outbox_file))
        self.assertEqual(len(outbox), 100)
        # oldest 5 dropped, newest retained in order
        self.assertEqual(outbox[0]["text"], "msg-5")
        self.assertEqual(outbox[-1]["text"], "msg-104")

    def test_corrupt_existing_outbox_recovers(self):
        self.outbox_file.write_text("not valid json{{{", encoding="utf-8")
        result = self.web.send_message("hello")
        self.assertTrue(result)
        outbox = read_json(str(self.outbox_file))
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["text"], "hello")

    def test_write_failure_returns_false(self):
        self.web.send_message("first")
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = self.web.send_message("second")
        self.assertFalse(result)


class TestWebReplyHandler(unittest.TestCase):
    """reply_handler should just forward to send_message (outbox only)."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.web = PFWeb(config={}, data_dir=self.tmp)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_reply_handler_writes_outbox(self):
        self.web.reply_handler("a reply", {"some": "metadata"})
        outbox = read_json(str(Path(self.tmp) / "web-outbox.json"))
        self.assertEqual(outbox[0]["text"], "a reply")

    def test_reply_handler_ignores_original_msg(self):
        # original_msg is accepted but unused — console manages history itself
        with patch.object(self.web, "send_message") as mock_send:
            self.web.reply_handler("response", {"chat_id": "ignored"})
            mock_send.assert_called_once_with("response")


class TestWebRun(unittest.TestCase):
    """run() has no polling loop — just logs readiness, then returns."""

    def setUp(self):
        self.tmp = make_temp_dir()

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_run_logs_ready_and_returns_immediately(self):
        web = PFWeb(config={}, data_dir=self.tmp)
        with self.assertLogs("permafrost.channels.web", level="INFO") as cap:
            web.run()  # must not block — no polling loop to run
        self.assertTrue(
            any("ready" in msg for msg in cap.output),
            f"expected a 'ready' log message, got: {cap.output}",
        )


if __name__ == "__main__":
    unittest.main()
