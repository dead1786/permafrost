"""
Tests for channels/ — BaseChannel, Telegram, Discord.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from conftest import make_temp_dir, cleanup_temp_dir, read_json
from channels.base import BaseChannel, create_channel, list_channels
from channels.telegram import PFTelegram
from channels.discord import PFDiscord
from channels.line import PFLine


class TestChannelRegistry(unittest.TestCase):
    """Test channel factory and listing."""

    def test_list_channels(self):
        channels = list_channels()
        names = [c["name"] for c in channels]
        self.assertIn("telegram", names)
        self.assertIn("discord", names)

    def test_create_telegram(self):
        ch = create_channel("telegram", {"telegram_token": "t", "telegram_chat_id": "123"})
        self.assertIsInstance(ch, PFTelegram)

    def test_create_discord(self):
        ch = create_channel("discord", {"discord_token": "t", "discord_channel_id": "123"})
        self.assertIsInstance(ch, PFDiscord)

    def test_unknown_channel_raises(self):
        with self.assertRaises(ValueError):
            create_channel("smoke_signal", {})


class TestTelegramValidation(unittest.TestCase):
    """Test Telegram channel configuration validation."""

    def test_valid_config(self):
        tg = PFTelegram(config={"telegram_token": "bot123:abc", "telegram_chat_id": "456"})
        ok, err = tg.validate()
        self.assertTrue(ok)

    def test_missing_token(self):
        tg = PFTelegram(config={"telegram_chat_id": "456"})
        ok, err = tg.validate()
        self.assertFalse(ok)
        self.assertIn("Token", err)

    def test_missing_chat_id(self):
        # chat_id is optional — auto-detected on first message
        tg = PFTelegram(config={"telegram_token": "bot123:abc"})
        ok, err = tg.validate()
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_name(self):
        tg = PFTelegram(config={"telegram_token": "t", "telegram_chat_id": "c"})
        self.assertEqual(tg.name, "telegram")


class TestTelegramSend(unittest.TestCase):
    """Test Telegram message sending."""

    def test_send_success(self):
        tg = PFTelegram(config={"telegram_token": "test", "telegram_chat_id": "123"})
        mock_response = MagicMock(ok=True)
        with patch("channels.telegram.requests.post", return_value=mock_response) as mock_post:
            result = tg.send_message("hello")
            self.assertTrue(result)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            self.assertEqual(payload["chat_id"], "123")
            self.assertEqual(payload["text"], "hello")

    def test_send_failure(self):
        tg = PFTelegram(config={"telegram_token": "test", "telegram_chat_id": "123"})
        mock_response = MagicMock(ok=False, status_code=403, text="Forbidden")
        with patch("channels.telegram.requests.post", return_value=mock_response):
            result = tg.send_message("hello")
            self.assertFalse(result)

    def test_long_message_chunked(self):
        tg = PFTelegram(config={"telegram_token": "test", "telegram_chat_id": "123"})
        long_text = "x" * 5000  # > 4096 limit
        mock_response = MagicMock(ok=True)
        with patch("channels.telegram.requests.post", return_value=mock_response) as mock_post:
            with patch("channels.telegram.time.sleep"):
                tg.send_message(long_text)
                self.assertEqual(mock_post.call_count, 2)

    def test_parse_mode_passed(self):
        tg = PFTelegram(config={
            "telegram_token": "test",
            "telegram_chat_id": "123",
            "telegram_parse_mode": "HTML",
        })
        mock_response = MagicMock(ok=True)
        with patch("channels.telegram.requests.post", return_value=mock_response) as mock_post:
            tg.send_message("hello")
            payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
            self.assertEqual(payload.get("parse_mode"), "HTML")


class TestTelegramAuth(unittest.TestCase):
    """Test Telegram message authorization."""

    def test_authorized_user(self):
        tg = PFTelegram(config={"telegram_token": "t", "telegram_chat_id": "123"})
        msg = {"chat": {"id": 123}}
        self.assertTrue(tg._is_authorized(msg))

    def test_unauthorized_user(self):
        tg = PFTelegram(config={"telegram_token": "t", "telegram_chat_id": "123"})
        msg = {"chat": {"id": 999}}
        self.assertFalse(tg._is_authorized(msg))


class TestTelegramProcessUpdate(unittest.TestCase):
    """Test Telegram update processing."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.tg = PFTelegram(
            config={"telegram_token": "t", "telegram_chat_id": "123"},
            data_dir=self.tmp,
        )

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_text_message_written_to_inbox(self):
        update = {
            "update_id": 1,
            "message": {
                "text": "hello",
                "chat": {"id": 123},
                "message_id": 42,
            },
        }
        with patch.object(self.tg, "send_typing"):
            self.tg._process_update(update)
        inbox = read_json(str(self.tg.inbox_file))
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["text"], "hello")
        self.assertFalse(inbox[0]["read"])

    def test_photo_message(self):
        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 123},
                "message_id": 43,
                "photo": [{"file_id": "photo123", "width": 100}],
                "caption": "my photo",
            },
        }
        with patch.object(self.tg, "send_typing"):
            self.tg._process_update(update)
        inbox = read_json(str(self.tg.inbox_file))
        self.assertIn("[photo:", inbox[0]["text"])
        self.assertIn("my photo", inbox[0]["text"])

    def test_unauthorized_message_ignored(self):
        update = {
            "update_id": 3,
            "message": {
                "text": "hack",
                "chat": {"id": 999},
                "message_id": 44,
            },
        }
        self.tg._process_update(update)
        self.assertFalse(self.tg.inbox_file.exists())


class TestDiscordValidation(unittest.TestCase):
    """Test Discord channel configuration validation."""

    def test_valid_config(self):
        dc = PFDiscord(config={"discord_token": "token", "discord_channel_id": "123"})
        ok, err = dc.validate()
        self.assertTrue(ok)

    def test_missing_token(self):
        dc = PFDiscord(config={"discord_channel_id": "123"})
        ok, err = dc.validate()
        self.assertFalse(ok)

    def test_missing_channel(self):
        dc = PFDiscord(config={"discord_token": "token"})
        ok, err = dc.validate()
        self.assertFalse(ok)


class TestDiscordSend(unittest.TestCase):
    """Test Discord message sending."""

    def test_send_success(self):
        dc = PFDiscord(config={"discord_token": "token", "discord_channel_id": "123"})
        mock_response = MagicMock(ok=True)
        with patch("channels.discord.requests.post", return_value=mock_response) as mock_post:
            result = dc.send_message("hello")
            self.assertTrue(result)

    def test_long_message_chunked_2000(self):
        dc = PFDiscord(config={"discord_token": "token", "discord_channel_id": "123"})
        long_text = "x" * 3000  # > 2000 limit
        mock_response = MagicMock(ok=True)
        with patch("channels.discord.requests.post", return_value=mock_response) as mock_post:
            with patch("channels.discord.time.sleep"):
                dc.send_message(long_text)
                self.assertEqual(mock_post.call_count, 2)

    def test_reply_handler_routes_to_source(self):
        dc = PFDiscord(config={"discord_token": "token", "discord_channel_id": "123"})
        with patch.object(dc, "send_message") as mock_send:
            dc.reply_handler("response", {"channel_id": "456"})
            mock_send.assert_called_once_with("response", channel_id="456")


class TestDiscordAuth(unittest.TestCase):
    """Test Discord message authorization."""

    def test_bot_messages_rejected(self):
        dc = PFDiscord(config={"discord_token": "t", "discord_channel_id": "c"})
        msg = {"author": {"id": "1", "bot": True}}
        self.assertFalse(dc._is_authorized(msg))

    def test_no_user_restriction(self):
        dc = PFDiscord(config={"discord_token": "t", "discord_channel_id": "c"})
        msg = {"author": {"id": "1", "bot": False}}
        self.assertTrue(dc._is_authorized(msg))

    def test_allowed_users_filter(self):
        dc = PFDiscord(config={
            "discord_token": "t",
            "discord_channel_id": "c",
            "discord_allowed_users": "100,200",
        })
        self.assertTrue(dc._is_authorized({"author": {"id": "100"}}))
        self.assertFalse(dc._is_authorized({"author": {"id": "999"}}))


class TestBaseChannelInbox(unittest.TestCase):
    """Test BaseChannel shared inbox helpers."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.tg = PFTelegram(
            config={"telegram_token": "t", "telegram_chat_id": "c"},
            data_dir=self.tmp,
        )

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def test_write_to_inbox_creates_file(self):
        self.tg.write_to_inbox("hello")
        self.assertTrue(self.tg.inbox_file.exists())

    def test_write_to_inbox_appends(self):
        self.tg.write_to_inbox("first")
        self.tg.write_to_inbox("second")
        inbox = read_json(str(self.tg.inbox_file))
        self.assertEqual(len(inbox), 2)

    def test_write_to_inbox_metadata(self):
        self.tg.write_to_inbox("hello", {"chat_id": "123"})
        inbox = read_json(str(self.tg.inbox_file))
        self.assertEqual(inbox[0]["chat_id"], "123")
        self.assertEqual(inbox[0]["source"], "telegram")
        self.assertFalse(inbox[0]["read"])

    def test_wake_trigger_created(self):
        self.tg.write_to_inbox("hello")
        wake_file = Path(self.tmp) / "brain-wake.trigger"
        self.assertTrue(wake_file.exists())


class TestLineValidation(unittest.TestCase):
    """Test LINE config validation."""

    def test_missing_access_token(self):
        line = PFLine(config={"line_access_token": ""})
        ok, err = line.validate()
        self.assertFalse(ok)
        self.assertIn("Access Token", err)

    def test_whitespace_only_token_rejected(self):
        line = PFLine(config={"line_access_token": "   "})
        ok, _ = line.validate()
        self.assertFalse(ok)

    def test_valid_with_cloudflare(self):
        line = PFLine(config={"line_access_token": "tok"})
        with patch("channels.line.HAS_CLOUDFLARE", True):
            ok, err = line.validate()
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_no_tunnel_library(self):
        line = PFLine(config={"line_access_token": "tok"})
        with patch("channels.line.HAS_CLOUDFLARE", False), \
             patch("channels.line.HAS_PYNGROK", False):
            ok, err = line.validate()
        self.assertFalse(ok)
        self.assertIn("tunnel library", err)

    def test_ngrok_without_authtoken(self):
        line = PFLine(config={"line_access_token": "tok"})
        with patch("channels.line.HAS_CLOUDFLARE", False), \
             patch("channels.line.HAS_PYNGROK", True):
            ok, err = line.validate()
        self.assertFalse(ok)
        self.assertIn("ngrok Auth Token", err)

    def test_name(self):
        self.assertEqual(PFLine(config={}).name, "line")

    def test_default_webhook_port(self):
        self.assertEqual(PFLine(config={}).webhook_port, 8504)

    def test_custom_webhook_port_coerced_to_int(self):
        line = PFLine(config={"line_webhook_port": "9001"})
        self.assertEqual(line.webhook_port, 9001)

    def test_auth_header_built_from_token(self):
        line = PFLine(config={"line_access_token": " tok "})
        self.assertEqual(line.access_token, "tok")
        self.assertEqual(line.headers["Authorization"], "Bearer tok")


class TestLinePush(unittest.TestCase):
    """Test LINE push message delivery."""

    def setUp(self):
        self.line = PFLine(config={"line_access_token": "tok"})

    def test_push_success(self):
        with patch("channels.line.requests.post") as post:
            post.return_value = MagicMock(ok=True)
            self.assertTrue(self.line._push_message("U1", "hi"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], "U1")
        self.assertEqual(payload["messages"][0]["text"], "hi")

    def test_push_http_error(self):
        with patch("channels.line.requests.post") as post:
            post.return_value = MagicMock(ok=False, status_code=400, text="bad")
            self.assertFalse(self.line._push_message("U1", "hi"))

    def test_push_request_exception(self):
        import requests as _rq
        with patch("channels.line.requests.post", side_effect=_rq.RequestException("boom")):
            self.assertFalse(self.line._push_message("U1", "hi"))


class TestLineSend(unittest.TestCase):
    """Test LINE send_message routing between reply token and push."""

    def setUp(self):
        self.line = PFLine(config={"line_access_token": "tok"})

    def test_reply_token_used_first(self):
        with patch("channels.line.requests.post") as post:
            post.return_value = MagicMock(ok=True)
            self.assertTrue(self.line.send_message("hi", reply_token="R1"))
        self.assertIn("/message/reply", post.call_args.args[0])

    def test_falls_back_to_push_when_reply_fails(self):
        with patch.object(self.line, "_push_message", return_value=True) as push, \
             patch("channels.line.requests.post") as post:
            post.return_value = MagicMock(ok=False, status_code=400, text="expired")
            self.assertTrue(self.line.send_message("hi", reply_token="R1", user_id="U1"))
        push.assert_called_once_with("U1", "hi")

    def test_no_user_id_and_no_reply_token_fails(self):
        self.assertFalse(self.line.send_message("hi"))

    def test_push_used_when_no_reply_token(self):
        with patch.object(self.line, "_push_message", return_value=True) as push:
            self.assertTrue(self.line.send_message("hi", user_id="U1"))
        push.assert_called_once_with("U1", "hi")

    def test_long_message_chunked_at_5000(self):
        text = "x" * 12000
        with patch.object(self.line, "_push_message", return_value=True) as push, \
             patch("channels.line.time.sleep"):
            self.assertTrue(self.line.send_message(text, user_id="U1"))
        self.assertEqual(push.call_count, 3)
        self.assertEqual(len(push.call_args_list[0].args[1]), 5000)
        self.assertEqual(len(push.call_args_list[2].args[1]), 2000)

    def test_push_failure_aborts_remaining_chunks(self):
        with patch.object(self.line, "_push_message", return_value=False) as push, \
             patch("channels.line.time.sleep"):
            self.assertFalse(self.line.send_message("y" * 12000, user_id="U1"))
        self.assertEqual(push.call_count, 1)

    def test_reply_handler_passes_token_and_user(self):
        with patch.object(self.line, "send_message", return_value=True) as send:
            self.line.reply_handler("resp", {"reply_token": "R1", "user_id": "U1"})
        send.assert_called_once_with("resp", reply_token="R1", user_id="U1")

    def test_reply_handler_without_original_message(self):
        with patch.object(self.line, "send_message", return_value=True) as send:
            self.line.reply_handler("resp")
        send.assert_called_once_with("resp", reply_token="", user_id="")


class TestLineWebhookEvent(unittest.TestCase):
    """Test LINE webhook event parsing into the inbox."""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.line = PFLine(config={"line_access_token": "tok"}, data_dir=self.tmp)

    def tearDown(self):
        cleanup_temp_dir(self.tmp)

    def _event(self, message, source=None, reply_token="R1"):
        return {
            "type": "message",
            "replyToken": reply_token,
            "message": message,
            "source": source or {"type": "user", "userId": "U1"},
        }

    def test_text_message_written_to_inbox(self):
        self.line.process_webhook_event(self._event({"type": "text", "text": "hello", "id": "M1"}))
        inbox = read_json(str(self.line.inbox_file))
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["text"], "hello")
        self.assertEqual(inbox[0]["source"], "line")
        self.assertEqual(inbox[0]["reply_token"], "R1")
        self.assertEqual(inbox[0]["user_id"], "U1")
        self.assertFalse(inbox[0]["read"])

    def test_non_message_event_ignored(self):
        self.line.process_webhook_event({"type": "follow", "source": {"userId": "U1"}})
        self.assertFalse(Path(self.line.inbox_file).exists()
                         and read_json(str(self.line.inbox_file)))

    def test_empty_text_ignored(self):
        self.line.process_webhook_event(self._event({"type": "text", "text": ""}))
        self.assertFalse(Path(self.line.inbox_file).exists()
                         and read_json(str(self.line.inbox_file)))

    def test_image_message_placeholder(self):
        self.line.process_webhook_event(self._event({"type": "image", "id": "IMG9"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["text"], "[image:IMG9]")

    def test_sticker_message_placeholder(self):
        self.line.process_webhook_event(
            self._event({"type": "sticker", "packageId": "P1", "stickerId": "S2"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["text"], "[sticker:P1:S2]")

    def test_video_message_placeholder(self):
        self.line.process_webhook_event(self._event({"type": "video", "id": "V3"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["text"], "[video:V3]")

    def test_audio_message_placeholder(self):
        self.line.process_webhook_event(self._event({"type": "audio", "id": "A4"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["text"], "[audio:A4]")

    def test_file_message_uses_filename(self):
        self.line.process_webhook_event(self._event({"type": "file", "fileName": "report.pdf"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["text"], "[file:report.pdf]")

    def test_group_source_metadata(self):
        self.line.process_webhook_event(self._event(
            {"type": "text", "text": "yo", "id": "M2"},
            source={"type": "group", "userId": "U1", "groupId": "G1"},
        ))
        entry = read_json(str(self.line.inbox_file))[0]
        self.assertEqual(entry["chat_type"], "group")
        self.assertEqual(entry["group_id"], "G1")
        self.assertEqual(entry["room_id"], "")

    def test_room_source_metadata(self):
        self.line.process_webhook_event(self._event(
            {"type": "text", "text": "yo", "id": "M3"},
            source={"type": "room", "userId": "U1", "roomId": "RM1"},
        ))
        entry = read_json(str(self.line.inbox_file))[0]
        self.assertEqual(entry["chat_type"], "room")
        self.assertEqual(entry["room_id"], "RM1")

    def test_message_id_recorded(self):
        self.line.process_webhook_event(self._event({"type": "text", "text": "hi", "id": "MID7"}))
        self.assertEqual(read_json(str(self.line.inbox_file))[0]["message_id"], "MID7")


if __name__ == "__main__":
    unittest.main()
