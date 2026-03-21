"""
Permafrost Discord Channel — Bot-based messaging via Discord API.

Features:
  - Receive messages from authorized users
  - Send replies back to source channel
  - Multi-channel support (monitors all channels bot has access to)
  - Message chunking for Discord's 2000 char limit
"""

import logging
import time

import requests

from .base import BaseChannel, register_channel

log = logging.getLogger("permafrost.channels.discord")


@register_channel("discord")
class PFDiscord(BaseChannel):
    """Discord channel plugin for Permafrost (REST API polling)."""

    LABEL = "Discord"
    CONFIG_FIELDS = [
        {"name": "discord_token", "label": "Bot Token", "type": "password",
         "help": "Create a bot at discord.com/developers/applications", "required": True},
        {"name": "discord_channel_id", "label": "Channel ID", "type": "text",
         "help": "Right-click channel > Copy ID (enable Developer Mode)", "required": True},
        {"name": "discord_allowed_users", "label": "Allowed User IDs", "type": "text",
         "help": "Leave empty to allow everyone. Fill in User IDs (comma-separated) to restrict who can chat.", "required": False},
    ]

    def __init__(self, config: dict, data_dir: str = None):
        super().__init__(config, data_dir)
        self.bot_token = config.get("discord_token", "")
        self.channel_id = config.get("discord_channel_id", "")
        self.allowed_users = [
            u.strip() for u in config.get("discord_allowed_users", "").split(",") if u.strip()
        ]
        self.api_base = "https://discord.com/api/v10"
        self.headers = {"Authorization": f"Bot {self.bot_token}"}
        self.last_message_id = None
        self.poll_interval = 3

    @property
    def name(self) -> str:
        return "discord"

    def validate(self) -> tuple[bool, str]:
        if not self.bot_token:
            return False, "Discord Bot Token is required"
        if not self.channel_id:
            return False, "Discord Channel ID is required"
        return True, ""

    def send_message(self, text: str, **kwargs) -> bool:
        """Send a message to the Discord channel."""
        channel_id = kwargs.get("channel_id", self.channel_id)
        chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
        for chunk in chunks:
            try:
                r = requests.post(
                    f"{self.api_base}/channels/{channel_id}/messages",
                    headers=self.headers,
                    json={"content": chunk},
                    timeout=15,
                )
                if not r.ok:
                    log.warning(f"send failed: {r.status_code} {r.text[:200]}")
                    return False
            except requests.RequestException as e:
                log.error(f"send error: {e}")
                return False
            if len(chunks) > 1:
                time.sleep(0.5)
        return True

    def reply_handler(self, response: str, original_msg: dict = None):
        """Route reply back to the source Discord channel."""
        target_channel = self.channel_id
        if original_msg:
            target_channel = original_msg.get("channel_id", self.channel_id)
        self.send_message(response, channel_id=target_channel)

    def _get_messages(self) -> list:
        """Fetch new messages from the channel."""
        params = {"limit": 10}
        if self.last_message_id:
            params["after"] = self.last_message_id
        try:
            r = requests.get(
                f"{self.api_base}/channels/{self.channel_id}/messages",
                headers=self.headers, params=params, timeout=15,
            )
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
        return []

    def _is_authorized(self, message: dict) -> bool:
        """Check if message author is authorized."""
        if message.get("author", {}).get("bot"):
            return False
        if not self.allowed_users:
            return True
        return message.get("author", {}).get("id", "") in self.allowed_users

    def run(self):
        """Main polling loop."""
        ok, err = self.validate()
        if not ok:
            log.warning(f"not configured: {err}")
            return

        self.running = True
        log.info(f"started polling (channel_id={self.channel_id})")

        try:
            while self.running:
                messages = self._get_messages()
                # Discord returns newest first, reverse for chronological order
                for msg in reversed(messages):
                    self.last_message_id = msg["id"]
                    if not self._is_authorized(msg):
                        continue
                    text = msg.get("content", "")
                    if text:
                        author = msg.get("author", {})
                        self.write_to_inbox(text, {
                            "source": "discord",
                            "user_id": author.get("id", ""),
                            "username": author.get("username", ""),
                            "chat_type": "guild" if msg.get("guild_id") else "dm",
                            "channel_id": msg.get("channel_id", ""),
                            "message_id": msg["id"],
                            "guild_id": msg.get("guild_id", ""),
                        })
                        log.info(f"received: {text[:80]}")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("stopped")
        finally:
            self.running = False
