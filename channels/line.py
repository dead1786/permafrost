"""
Permafrost LINE Channel — Messaging via LINE Messaging API.

Features:
  - Receive messages via webhook (requires public endpoint)
  - Send replies back
  - Text + sticker + image support

Requires: line-bot-sdk (pip install line-bot-sdk)
"""

import json
import logging
import time

import requests

from .base import BaseChannel, register_channel

log = logging.getLogger("permafrost.channels.line")


@register_channel("line")
class PFLine(BaseChannel):
    """LINE channel plugin for Permafrost (Messaging API)."""

    LABEL = "LINE"
    CONFIG_FIELDS = [
        {"name": "line_channel_secret", "label": "Channel Secret", "type": "password",
         "help": "From LINE Developers console > Messaging API", "required": True},
        {"name": "line_access_token", "label": "Channel Access Token", "type": "password",
         "help": "Long-lived token from LINE Developers console", "required": True},
        {"name": "line_user_id", "label": "Your User ID", "type": "text",
         "help": "Your LINE user ID for direct messaging", "required": True},
    ]

    def __init__(self, config: dict, data_dir: str = None):
        super().__init__(config, data_dir)
        self.channel_secret = config.get("line_channel_secret", "")
        self.access_token = config.get("line_access_token", "")
        self.user_id = config.get("line_user_id", "")
        self.api_base = "https://api.line.me/v2/bot"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @property
    def name(self) -> str:
        return "line"

    def validate(self) -> tuple[bool, str]:
        if not self.access_token:
            return False, "LINE Channel Access Token is required"
        if not self.user_id:
            return False, "LINE User ID is required"
        return True, ""

    def send_message(self, text: str, **kwargs) -> bool:
        """Send a push message to the user."""
        user_id = kwargs.get("user_id", self.user_id)
        # LINE has a 5000 char limit per text message
        chunks = [text[i:i + 5000] for i in range(0, len(text), 5000)]
        for chunk in chunks:
            payload = {
                "to": user_id,
                "messages": [{"type": "text", "text": chunk}],
            }
            try:
                r = requests.post(
                    f"{self.api_base}/message/push",
                    headers=self.headers,
                    json=payload,
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
        """Route reply — use reply token if available, otherwise push."""
        reply_token = None
        if original_msg:
            reply_token = original_msg.get("reply_token")

        if reply_token:
            payload = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": response[:5000]}],
            }
            try:
                r = requests.post(
                    f"{self.api_base}/message/reply",
                    headers=self.headers,
                    json=payload,
                    timeout=15,
                )
                if r.ok:
                    return
            except requests.RequestException:
                pass
            # Fall through to push if reply fails (token may have expired)

        self.send_message(response)

    def run(self):
        """LINE uses webhooks for receiving — poll-based fallback not available.

        For webhook mode, integrate with a web server (Flask/FastAPI)
        and call write_to_inbox() when webhook events arrive.

        This polling stub checks for manually placed inbox messages.
        """
        ok, err = self.validate()
        if not ok:
            log.warning(f"not configured: {err}")
            return

        self.running = True
        log.info("LINE channel ready (webhook mode — no polling)")
        log.info("To receive messages, set up a webhook endpoint that calls write_to_inbox()")

        # No polling loop for LINE — it's webhook-based.
        # The run() method just keeps the thread alive if needed.
        try:
            while self.running:
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
