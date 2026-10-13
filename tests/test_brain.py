"""
Tests for core/brain.py — PFBrain persistent AI session engine.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from conftest import make_temp_dir, cleanup_temp_dir, write_json, read_json
from core.brain import PFBrain, DEFAULT_CONFIG


class TestBrainInit(unittest.TestCase):
    """Test brain initialization."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "ai_provider": "claude", "api_key": "test"})

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_default_config(self):
        brain = PFBrain()
        self.assertEqual(brain.config["ai_provider"], "claude")
        self.assertEqual(brain.config["poll_interval"], 1.0)

    def test_load_config_from_file(self):
        brain = PFBrain(self.config_path)
        self.assertEqual(brain.config["data_dir"], self.tmp)
        self.assertEqual(brain.config["api_key"], "test")

    def test_data_dir_created(self):
        brain = PFBrain(self.config_path)
        self.assertTrue(brain.data_dir.exists())

    def test_empty_conversation_on_start(self):
        brain = PFBrain(self.config_path)
        self.assertEqual(brain._conversation, [])

    def test_conversation_restore(self):
        conv = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        write_json(os.path.join(self.tmp, "brain-conversation.json"), conv)
        brain = PFBrain(self.config_path)
        self.assertEqual(len(brain._conversation), 2)
        self.assertEqual(brain._conversation[0]["content"], "hi")


class TestChannelRegistration(unittest.TestCase):
    """Test channel inbox registration."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_register_channel(self):
        inbox = os.path.join(self.tmp, "tg-inbox.json")
        self.brain.register_channel("telegram", inbox)
        self.assertIn("telegram", self.brain.channel_inboxes)

    def test_register_with_handler(self):
        handler = lambda resp, msg: None
        self.brain.register_channel("discord", "/tmp/dc.json", handler)
        self.assertIn("discord", self.brain.channel_handlers)

    def test_multiple_channels(self):
        self.brain.register_channel("telegram", "/tmp/tg.json")
        self.brain.register_channel("discord", "/tmp/dc.json")
        self.brain.register_channel("web", "/tmp/web.json")
        self.assertEqual(len(self.brain.channel_inboxes), 3)


class TestInboxChecking(unittest.TestCase):
    """Test inbox polling and message pickup."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_empty_inbox(self):
        inbox = os.path.join(self.tmp, "tg-inbox.json")
        write_json(inbox, [])
        self.brain.register_channel("telegram", inbox)
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 0)

    def test_unread_messages_found(self):
        inbox = os.path.join(self.tmp, "tg-inbox.json")
        write_json(inbox, [{"text": "hello", "read": False}])
        self.brain.register_channel("telegram", inbox)
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 1)
        channel, unread, all_msgs = results[0]
        self.assertEqual(channel, "telegram")
        self.assertEqual(len(unread), 1)

    def test_read_messages_skipped(self):
        inbox = os.path.join(self.tmp, "tg-inbox.json")
        write_json(inbox, [{"text": "old", "read": True}])
        self.brain.register_channel("telegram", inbox)
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 0)

    def test_mixed_read_unread(self):
        inbox = os.path.join(self.tmp, "tg-inbox.json")
        write_json(inbox, [
            {"text": "old", "read": True},
            {"text": "new", "read": False},
        ])
        self.brain.register_channel("telegram", inbox)
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 1)
        _, unread, _ = results[0]
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0]["text"], "new")

    def test_nonexistent_inbox_ignored(self):
        self.brain.register_channel("telegram", os.path.join(self.tmp, "nope.json"))
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 0)

    def test_malformed_inbox_ignored(self):
        inbox = os.path.join(self.tmp, "bad.json")
        with open(inbox, "w") as f:
            f.write("{invalid json")
        self.brain.register_channel("bad", inbox)
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 0)


class TestMarkRead(unittest.TestCase):
    """Test marking messages as read."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_mark_read(self):
        inbox = os.path.join(self.tmp, "inbox.json")
        msgs = [{"text": "hello", "read": False}, {"text": "world", "read": False}]
        write_json(inbox, msgs)
        self.brain._mark_read(Path(inbox), msgs)
        # Everything handed to the brain was processed, so nothing is left.
        self.assertEqual(read_json(inbox), [])

    def test_mark_read_keeps_messages_that_arrived_meanwhile(self):
        """A message written while the AI was replying must survive."""
        inbox = os.path.join(self.tmp, "inbox.json")
        processed = [{"text": "first", "read": False, "timestamp": "t1"}]
        late = {"text": "sent during reply", "read": False, "timestamp": "t2"}
        write_json(inbox, processed + [late])
        self.brain._mark_read(Path(inbox), processed)
        self.assertEqual(read_json(inbox), [late])

    def test_mark_read_removes_only_one_of_two_identical_messages(self):
        inbox = os.path.join(self.tmp, "inbox.json")
        dup = {"text": "ping", "read": False}
        write_json(inbox, [dup, dict(dup)])
        self.brain._mark_read(Path(inbox), [dup])
        self.assertEqual(read_json(inbox), [dup])

    def test_mark_read_recovers_from_corrupt_inbox(self):
        inbox = os.path.join(self.tmp, "inbox.json")
        with open(inbox, "w") as f:
            f.write("{not json")
        self.brain._mark_read(Path(inbox), [{"text": "x"}])
        self.assertEqual(read_json(inbox), [])

    def test_mark_read_missing_inbox_does_not_raise(self):
        inbox = Path(self.tmp) / "gone.json"
        self.brain._mark_read(inbox, [{"text": "x"}])
        self.assertEqual(read_json(str(inbox)), [])


class TestInboxRobustness(unittest.TestCase):
    """Malformed inbox content and failing handlers must not kill the brain."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})
        self.brain = PFBrain(self.config_path)
        self.inbox = os.path.join(self.tmp, "tg-inbox.json")
        self.brain.register_channel("telegram", self.inbox)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_non_dict_inbox_entries_are_skipped(self):
        write_json(self.inbox, ["stray string", 42, None,
                                {"text": "real", "read": False}])
        results = self.brain._check_inboxes()
        self.assertEqual(len(results), 1)
        _, unread, _ = results[0]
        self.assertEqual([m["text"] for m in unread], ["real"])

    def test_handle_message_routes_reply_to_channel_handler(self):
        handler = MagicMock()
        self.brain.channel_handlers["telegram"] = handler
        msg = {"text": "hi", "read": False}
        with patch.object(self.brain, "_process_message", return_value="hello back"):
            self.brain._handle_inbox_message("telegram", msg)
        handler.assert_called_once_with("hello back", msg)

    def test_handle_message_blocked_sends_no_reply(self):
        handler = MagicMock()
        self.brain.channel_handlers["telegram"] = handler
        with patch.object(self.brain, "_process_message", return_value=None):
            self.brain._handle_inbox_message("telegram", {"text": "hi"})
        handler.assert_not_called()

    def test_handle_message_swallows_processing_error(self):
        handler = MagicMock()
        self.brain.channel_handlers["telegram"] = handler
        with patch.object(self.brain, "_process_message",
                          side_effect=RuntimeError("boom")):
            # Must not raise: an exception here would terminate run().
            self.brain._handle_inbox_message("telegram", {"text": "hi"})
        handler.assert_not_called()

    def test_handle_message_swallows_reply_handler_error(self):
        self.brain.channel_handlers["telegram"] = MagicMock(
            side_effect=ConnectionError("telegram down"))
        with patch.object(self.brain, "_process_message", return_value="ok"):
            self.brain._handle_inbox_message("telegram", {"text": "hi"})

    def test_handle_message_logs_in_and_out(self):
        with patch.object(self.brain, "_process_message", return_value="pong"):
            self.brain._handle_inbox_message("telegram", {"text": "ping"})
        log = read_json(str(self.brain.message_log))
        self.assertEqual([(e["direction"], e["text"]) for e in log],
                         [("in", "ping"), ("out", "pong")])


class TestMessageBuilding(unittest.TestCase):
    """Test message building with system prompt and history."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {
            "data_dir": self.tmp,
            "api_key": "test",
            "system_prompt": "You are a helpful assistant.",
        })
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_includes_system_prompt(self):
        msgs = self.brain._build_messages("telegram", "hello")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("helpful assistant", msgs[0]["content"])

    def test_includes_source_tag(self):
        msgs = self.brain._build_messages("discord", "hello")
        # Channel context is injected as a system message (not in user content)
        system_msgs = [m for m in msgs if m["role"] == "system"]
        channel_ctx = any("discord" in m["content"] for m in system_msgs)
        self.assertTrue(channel_ctx, "Expected channel context in a system message")
        # User message should be clean
        last = msgs[-1]
        self.assertEqual(last["content"], "hello")

    def test_includes_conversation_history(self):
        self.brain._conversation = [
            {"role": "user", "content": "previous"},
            {"role": "assistant", "content": "response"},
        ]
        msgs = self.brain._build_messages("telegram", "new message")
        # system(prompt) + 2 history + system(channel context) + user = 5
        self.assertEqual(len(msgs), 5)


class TestConversationPersistence(unittest.TestCase):
    """Test conversation save/restore."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_save_and_restore(self):
        brain1 = PFBrain(self.config_path)
        brain1._conversation = [{"role": "user", "content": "test"}]
        brain1._save_conversation()

        brain2 = PFBrain(self.config_path)
        self.assertEqual(len(brain2._conversation), 1)
        self.assertEqual(brain2._conversation[0]["content"], "test")


class TestHeartbeat(unittest.TestCase):
    """Test heartbeat file writing."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test", "heartbeat_interval": 0})
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_heartbeat_written(self):
        self.brain.last_heartbeat = 0  # force write
        self.brain._write_heartbeat()
        self.assertTrue(self.brain.heartbeat_file.exists())
        hb = read_json(str(self.brain.heartbeat_file))
        self.assertIn("pid", hb)
        self.assertIn("timestamp", hb)
        self.assertIn("provider", hb)


class TestWakeTrigger(unittest.TestCase):
    """Test wake signal mechanism."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.config_path = os.path.join(self.tmp, "config.json")
        write_json(self.config_path, {"data_dir": self.tmp, "api_key": "test"})
        self.brain = PFBrain(self.config_path)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_no_wake_file(self):
        self.assertFalse(self.brain._check_wake())

    def test_wake_file_detected_and_removed(self):
        self.brain.wake_trigger.write_text("wake")
        self.assertTrue(self.brain._check_wake())
        self.assertFalse(self.brain.wake_trigger.exists())


if __name__ == "__main__":
    unittest.main()
