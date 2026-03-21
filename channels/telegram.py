"""
Permafrost Telegram Channel — Bidirectional messaging via Telegram Bot API.

Features:
  - Receive messages from user via long polling
  - Send replies back (text, photo, document)
  - Typing indicator while processing
  - Wake brain on new message
  - Rate limiting to avoid API throttling
"""

import logging
import time

import requests

from .base import BaseChannel, register_channel

log = logging.getLogger("permafrost.channels.telegram")


@register_channel("telegram")
class PFTelegram(BaseChannel):
    """Telegram channel plugin for Permafrost."""

    LABEL = "Telegram"
    CONFIG_FIELDS = [
        {"name": "telegram_token", "label": "Bot Token", "type": "password",
         "help": "Create a bot via @BotFather on Telegram. That's all you need!", "required": True},
    ]

    def __init__(self, config: dict, data_dir: str = None):
        super().__init__(config, data_dir)
        self.bot_token = config.get("telegram_token", "")
        self.chat_id = str(config.get("telegram_chat_id", ""))
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self._auto_detect_chat_id(config)
        self.last_update_id = 0
        self.poll_interval = int(config.get("telegram_poll_interval", 2))
        self.parse_mode = config.get("telegram_parse_mode", "")

    @property
    def name(self) -> str:
        return "telegram"

    def _auto_detect_chat_id(self, config):
        """Auto-detect chat_id from recent messages if not configured."""
        if self.chat_id or not self.bot_token:
            return
        try:
            r = requests.get(f"{self.api_base}/getUpdates", params={"limit": 5}, timeout=10)
            if r.ok:
                updates = r.json().get("result", [])
                for u in updates:
                    msg = u.get("message", {})
                    chat = msg.get("chat", {})
                    if chat.get("id"):
                        self.chat_id = str(chat["id"])
                        log.info(f"Auto-detected chat_id: {self.chat_id}")
                        return
                if not self.chat_id:
                    log.warning("No chat_id detected. Send /start to the bot on Telegram first.")
        except Exception as e:
            log.warning(f"chat_id auto-detect failed: {e}")

    def validate(self) -> tuple[bool, str]:
        if not self.bot_token:
            return False, "Telegram Bot Token is required"
        if not self.chat_id:
            return False, "Chat ID not detected. Send /start to your bot on Telegram, then restart."
        return True, ""

    def send_message(self, text: str, **kwargs) -> bool:
        """Send a text message to the user."""
        parse_mode = kwargs.get("parse_mode", self.parse_mode)
        # Telegram message limit: 4096 chars
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for chunk in chunks:
            payload = {"chat_id": self.chat_id, "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                r = requests.post(f"{self.api_base}/sendMessage", json=payload, timeout=15)
                if not r.ok:
                    log.warning(f"send failed: {r.status_code} {r.text[:200]}")
                    return False
            except requests.RequestException as e:
                log.error(f"send error: {e}")
                return False
            if len(chunks) > 1:
                time.sleep(0.5)  # rate limit
        return True

    def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """Send a photo to the user."""
        try:
            with open(photo_path, "rb") as f:
                r = requests.post(f"{self.api_base}/sendPhoto",
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"photo": f}, timeout=30)
            return r.ok
        except Exception as e:
            log.error(f"send_photo error: {e}")
            return False

    def send_document(self, doc_path: str, caption: str = "") -> bool:
        """Send a document to the user."""
        try:
            with open(doc_path, "rb") as f:
                r = requests.post(f"{self.api_base}/sendDocument",
                    data={"chat_id": self.chat_id, "caption": caption},
                    files={"document": f}, timeout=60)
            return r.ok
        except Exception as e:
            log.error(f"send_document error: {e}")
            return False

    def reply_handler(self, response: str, original_msg: dict = None):
        """Route reply back to the correct chat."""
        chat_id = None
        if original_msg:
            chat_id = original_msg.get("chat_id", original_msg.get("metadata", {}).get("chat_id"))
        if chat_id and chat_id != self.chat_id:
            # Reply to a different chat (group, etc.)
            payload = {"chat_id": chat_id, "text": response[:4096]}
            if self.parse_mode:
                payload["parse_mode"] = self.parse_mode
            try:
                requests.post(f"{self.api_base}/sendMessage", json=payload, timeout=15)
            except requests.RequestException as e:
                log.error(f"reply_handler error: {e}")
        else:
            self.send_message(response)

    def send_typing(self):
        """Show typing indicator."""
        try:
            requests.post(f"{self.api_base}/sendChatAction",
                json={"chat_id": self.chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass

    def _get_updates(self) -> list:
        """Poll Telegram for new messages (long polling)."""
        try:
            r = requests.get(f"{self.api_base}/getUpdates", params={
                "offset": self.last_update_id + 1,
                "timeout": 10,
            }, timeout=15)
            if r.ok:
                return r.json().get("result", [])
        except requests.RequestException:
            pass
        return []

    def _is_authorized(self, message: dict) -> bool:
        """Check if message is from authorized user."""
        msg_chat_id = str(message.get("chat", {}).get("id", ""))
        return msg_chat_id == self.chat_id

    def _process_update(self, update: dict):
        """Process a single Telegram update."""
        self.last_update_id = update["update_id"]
        message = update.get("message")
        if not message:
            return
        if not self._is_authorized(message):
            return

        # Extract text and user info
        text = message.get("text", "")
        user = message.get("from", {})
        chat = message.get("chat", {})
        metadata = {
            "source": "telegram",
            "chat_id": str(chat.get("id", "")),
            "message_id": message.get("message_id"),
            "user_id": str(user.get("id", "")),
            "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "chat_type": chat.get("type", ""),  # private/group/supergroup
        }

        # Handle photos
        if "photo" in message:
            photo = message["photo"][-1]  # highest resolution
            text = f"[photo:{photo.get('file_id', '')}] {message.get('caption', '')}"

        # Handle documents
        if "document" in message:
            doc = message["document"]
            text = f"[document:{doc.get('file_name', '')}] {message.get('caption', '')}"

        # Handle voice
        if "voice" in message:
            text = f"[voice:{message['voice'].get('file_id', '')}]"

        if text:
            self.write_to_inbox(text, metadata)
            self.send_typing()
            log.info(f"received: {text[:80]}")

    def run(self):
        """Main polling loop."""
        ok, err = self.validate()
        if not ok:
            log.warning(f"not configured: {err}")
            return

        self.running = True
        log.info(f"started polling (chat_id={self.chat_id})")

        try:
            while self.running:
                updates = self._get_updates()
                for update in updates:
                    self._process_update(update)
                if not updates:
                    time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("stopped")
        finally:
            self.running = False


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    config = {
        "telegram_token": os.environ.get("PF_TG_TOKEN", ""),
        "telegram_chat_id": os.environ.get("PF_TG_CHAT_ID", ""),
    }
    tg = PFTelegram(config)
    tg.run()
